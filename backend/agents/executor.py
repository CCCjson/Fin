"""
工具执行器 — 同步调用已注册的工具函数，统一错误兜底 + 回灌裁剪。

注意：orchestrator 是同步 Generator，按 advisor.py 的 thread/queue 模式在 worker
线程里被 drain，因此这里直接同步调用工具函数（阻塞不影响事件循环），无需 run_in_executor。

参数校验（迁移中，见 registry.py 头部说明）：td.args_model 存在时，先用 pydantic
校验 LLM 传回的原始 args；校验失败不执行 fn，直接把结构化错误（哪个字段/期望什么/
收到什么）回灌给 LLM——这就是「参数格式不对时代码触发重试」的全部机制：下一轮
LLM 自然看到错误详情去修正，ReAct 循环本身就是重试循环，不需要额外的重试状态机。
"""
import json
from typing import Any, Optional

from loguru import logger
from pydantic import ValidationError

from agents.registry import REGISTRY
from agents.tool_envelope import ErrorCode, ToolEnvelope, to_legacy_dict

# 回灌 LLM 的 summary 文本上限（防止把超大结果塞进 context）
_MAX_SUMMARY_CHARS = 3000


def truncate_json_safe(text: str, limit: int) -> str:
    """截断长文本，尽量保住 JSON 完整性——数字/键值绝不拦腰斩。

    - JSON 数组：保能装下的前缀项，标注「原 N 项保留 M 项」
    - JSON 对象：保能装下的前缀键值，标注被丢弃的字段名
    - 非 JSON 或单项就超限：退回字符硬截（原行为）
    """
    if len(text) <= limit:
        return text
    try:
        data = json.loads(text)
    except (ValueError, TypeError):
        data = None
    if isinstance(data, list) and data:
        keep: list = []
        size = 2
        for item in data:
            s = json.dumps(item, ensure_ascii=False, default=str)
            if size + len(s) + 1 > limit:
                break
            keep.append(item)
            size += len(s) + 1
        if keep:
            return (json.dumps(keep, ensure_ascii=False, default=str)
                    + f"\n…(已截断：原 {len(data)} 项，完整保留前 {len(keep)} 项)")
    elif isinstance(data, dict) and data:
        kept: dict = {}
        size = 2
        for k, v in data.items():
            s = json.dumps({k: v}, ensure_ascii=False, default=str)
            if size + len(s) > limit:
                break
            kept[k] = v
            size += len(s)
        if kept:
            dropped = [str(k) for k in data if k not in kept]
            note = (f"\n…(已截断，丢弃字段: {', '.join(dropped[:10])})"
                    if dropped else "")
            return json.dumps(kept, ensure_ascii=False, default=str) + note
    return text[:limit] + f"\n…(已截断，原长 {len(text)} 字符)"


def truncate_tool_content(
    content: str, gate_chars: int, keep_chars: int, marker: str,
) -> Optional[tuple[str, int]]:
    """长 tool 结果原地截断的共享内核：超过 gate_chars 才动手，截到 keep_chars 并
    加 marker。返回 (新 content, 省下字符数)；未超限则返回 None（调用方不改内容）。

    调用方（turn_monitor 轮中压缩 / history 轮间压缩）各自负责判断 role=="tool"、
    维护自己的记账状态（compacted 标记、seen 去重等），只共用这一段截断逻辑。
    """
    if len(content) <= gate_chars:
        return None
    new_content = truncate_json_safe(content, keep_chars) + marker
    return new_content, len(content) - len(new_content)


def _coerce_summary(result: Any) -> Any:
    """从工具返回值里取出要回灌 LLM 的部分（summary），并裁剪过长内容。"""
    if isinstance(result, dict) and "summary" in result:
        summary = result["summary"]
    else:
        # 工具没显式给 summary：除 widget 外的字段都回灌
        if isinstance(result, dict):
            summary = {k: v for k, v in result.items() if k != "widget"}
        else:
            summary = result

    text = summary if isinstance(summary, str) else json.dumps(
        summary, ensure_ascii=False, default=str)
    return truncate_json_safe(text, _MAX_SUMMARY_CHARS)


def _format_validation_error(e: ValidationError) -> tuple[dict, str]:
    """把 pydantic 校验错误拆成结构化 detail + 一句可读摘要（回灌 LLM 用于自我修正）。"""
    errors = []
    parts = []
    for err in e.errors():
        field = ".".join(str(x) for x in err["loc"]) or "(root)"
        errors.append({
            "field": field, "expected": err["type"],
            "received": err.get("input"), "msg": err["msg"],
        })
        parts.append(f"{field}: {err['msg']}（收到 {err.get('input')!r}）")
    return {"errors": errors}, "参数校验失败 — " + "；".join(parts)


def validate_tool_args(name: str, args_model, args: dict) -> tuple[dict, Optional[dict]]:
    """校验 args_model（若声明），返回 (校验/规整后的 kwargs, 校验失败时的 legacy 错误 dict)。

    args_model 为 None 时原样放行。校验失败时第二个返回值非 None，调用方应
    直接用它短路返回，不再往下执行——普通工具（run_tool）与 subagent（
    tool_dispatch.dispatch）共用这份校验，避免 subagent 参数校验各自为政。
    """
    kwargs = args or {}
    if args_model is None:
        return kwargs, None
    try:
        parsed = args_model.model_validate(kwargs)
    except ValidationError as e:
        detail, readable = _format_validation_error(e)
        logger.warning(f"工具 {name} 参数校验失败: {readable}")
        envelope = ToolEnvelope(ok=False, error_code=ErrorCode.VALIDATION_ERROR,
                                 error_detail=detail, message=readable)
        return kwargs, to_legacy_dict(envelope)
    return parsed.model_dump(), None


def run_tool(name: str, args: dict) -> dict:
    """
    执行工具，返回归一化结构：
      {"ok": True,  "summary": <str 回灌 LLM>, "widget": <可选>}
      {"ok": False, "summary": <错误说明回灌 LLM 让其自愈>}
    有 args_model 的工具还会带 business_result/error_code/data 等结构化字段。
    """
    try:
        td = REGISTRY.get(name)
    except KeyError as e:
        return {"ok": False, "summary": f"错误：{e}"}

    kwargs, err = validate_tool_args(name, td.args_model, args)
    if err is not None:
        return err

    try:
        raw = td.fn(**kwargs)
    except TypeError as e:
        # 参数不匹配（未迁移到 args_model 的工具）—— 回灌让模型修正
        logger.warning(f"工具 {name} 参数错误: {e}")
        return {"ok": False, "summary": f"工具 {name} 参数错误：{e}"}
    except Exception as e:
        logger.error(f"工具 {name} 执行异常: {e}")
        envelope = ToolEnvelope(ok=False, error_code=ErrorCode.INTERNAL_ERROR,
                                 error_detail={"exception_type": type(e).__name__, "message": str(e)},
                                 message=f"工具 {name} 执行失败：{e}")
        return to_legacy_dict(envelope)

    if isinstance(raw, ToolEnvelope):
        return to_legacy_dict(raw)

    # 过渡保险：所有域工具已迁移到 ToolEnvelope（Phase 2），这里只兜底未来
    # 新工具漏迁的裸 dict——带 "error" 键的绝不能被下面的默认 ok=True 吞掉，
    # 那正是 Phase 2 之前的旗舰 bug（静默假成功，见 memory/agent-arch-review）。
    if isinstance(raw, dict) and "error" in raw:
        envelope = ToolEnvelope(ok=False, error_code=ErrorCode.INTERNAL_ERROR,
                                 error_detail={"message": str(raw["error"])},
                                 message=str(raw["error"]))
        return to_legacy_dict(envelope)

    out: dict = {"ok": True, "summary": _coerce_summary(raw)}
    if isinstance(raw, dict) and raw.get("widget"):
        out["widget"] = raw["widget"]
    if isinstance(raw, dict) and raw.get("navigate"):
        out["navigate"] = raw["navigate"]
    # 未截断的完整原文，供 agents.trace.write_raw_tool_call 落语料用；
    # 与 to_legacy_dict 的 "data" 字段同一约定，widget 已旁路不重复放入。
    out["data"] = ({k: v for k, v in raw.items() if k != "widget"}
                    if isinstance(raw, dict) else raw)
    return out

"""
工具执行器 — 同步调用已注册的工具函数，统一错误兜底 + 回灌裁剪。

注意：orchestrator 是同步 Generator，按 advisor.py 的 thread/queue 模式在 worker
线程里被 drain，因此这里直接同步调用工具函数（阻塞不影响事件循环），无需 run_in_executor。
"""
import json
from typing import Any

from loguru import logger

from agents.registry import REGISTRY

# 回灌 LLM 的 summary 文本上限（防止把超大结果塞进 context）
_MAX_SUMMARY_CHARS = 4000


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
    if len(text) > _MAX_SUMMARY_CHARS:
        text = text[:_MAX_SUMMARY_CHARS] + f"\n…(已截断，原长 {len(text)} 字符)"
    return text


def run_tool(name: str, args: dict) -> dict:
    """
    执行工具，返回归一化结构：
      {"ok": True,  "summary": <str 回灌 LLM>, "widget": <可选>}
      {"ok": False, "summary": <错误说明回灌 LLM 让其自愈>}
    """
    try:
        td = REGISTRY.get(name)
    except KeyError as e:
        return {"ok": False, "summary": f"错误：{e}"}

    try:
        raw = td.fn(**(args or {}))
    except TypeError as e:
        # 参数不匹配 —— 回灌让模型修正
        logger.warning(f"工具 {name} 参数错误: {e}")
        return {"ok": False, "summary": f"工具 {name} 参数错误：{e}"}
    except Exception as e:
        logger.error(f"工具 {name} 执行异常: {e}")
        return {"ok": False, "summary": f"工具 {name} 执行失败：{e}"}

    out: dict = {"ok": True, "summary": _coerce_summary(raw)}
    if isinstance(raw, dict) and raw.get("widget"):
        out["widget"] = raw["widget"]
    return out

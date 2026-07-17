"""
统一工具返回信封 —— ToolEnvelope。

替代 executor.py 里松散拼装的 {"ok","summary","widget","navigate"} dict，
把三个正交维度分开表达：

  ok              技术态：fn 是否正常跑完（False = 参数校验被拦截 / 内部异常，fn 可能根本没跑）
  business_result 业务态（只有 ok=True 时才有意义）：
                    affirmative = 拿到了正向结果（推荐了股票/查到了数据/下单成交）
                    negative    = 正常执行完毕，但结论是"否/无/拒绝"
                                  （风控拒单、未找到新闻、今日无信号——这是诚实的"没有"，不是错误）
  quality         数据质量态（P0-2）：**拿到了数据，但这数据可不可信**
                    ok=True + affirmative + quality.core_degraded=True 完全合法 ——
                    「成功返回了三天前的收盘价」正是这三个维度都用得上的场景。

三个维度正交：不要用 ok=False 表达业务性否定结果，不要把技术异常塞进 negative，
也不要用 negative 表达「数据旧了」（那是 quality 的活，数据旧不代表没查到）。
"""
import json
from enum import Enum
from typing import Any, Literal, Optional

from pydantic import BaseModel, Field, model_validator


class ErrorCode(str, Enum):
    VALIDATION_ERROR = "validation_error"  # 代码校验拦截，fn 根本没跑
    INTERNAL_ERROR = "internal_error"       # fn 内部抛出未预期异常


class ToolEnvelope(BaseModel):
    ok: bool = True
    error_code: Optional[ErrorCode] = Field(
        default=None, description="ok=False 时的错误分类"
    )
    error_detail: Optional[dict] = Field(
        default=None,
        description="validation_error: {'errors': [{field,expected,received,msg}, ...]}；"
                    "internal_error: {'exception_type','message'}",
    )

    business_result: Literal["affirmative", "negative"] = Field(
        default="affirmative",
        description="只有 ok=True 时有意义：affirmative=正向结果，negative=正常执行但结论是否/无/拒绝",
    )

    data: Any = Field(default=None, description="结构化 payload，回灌 LLM 走 json 序列化+截断")
    quality: Optional[dict] = Field(
        default=None,
        description="数据质量（P0-2）：{overall_score, level, limitations, core_degraded, "
                    "block_scores, version}，由 common.context_quality.compute_quality 产出。"
                    "**回灌 LLM 时前置到 summary 最前面**——放尾部会被 truncate_json_safe "
                    "静默截掉（它按插入序保前缀键），而数据质量警告恰恰最不能丢。",
    )
    message: str = Field(default="", description="人类可读摘要，可选，多数场景可留空让 data 自解释")
    widget: Optional[dict] = Field(default=None, description="旁路直推前端的 UI 卡片，不进 LLM context")
    navigate: Optional[dict] = Field(default=None, description="前端导航指令")
    tokens: int = Field(
        default=0,
        description="仅 subagent 用：内层 LLM 消耗的 token 数，计入 TurnMonitor 预算（普通工具恒为 0）",
    )

    def is_bad(self) -> bool:
        """TurnMonitor 判定用：技术失败或业务性否定都算"需要关注"的一类。"""
        return not self.ok or self.business_result == "negative"

    @model_validator(mode="after")
    def _check_error_consistency(self) -> "ToolEnvelope":
        """把「忘了给 ok/error_code」这类语义坍缩问题变成构造期就炸的错误，而不是
        像 news.py 那样静默产出一个『看起来正常』的结果被下游默认判定成功。"""
        if not self.ok and self.error_code is None:
            raise ValueError("ok=False 时必须给 error_code")
        if self.ok and self.error_code is not None:
            raise ValueError("error_code 只在 ok=False 时有意义，ok=True 不应设置")
        return self


def _prepend_quality(text: str, quality: Optional[dict]) -> str:
    """把数据质量警告**前置**到回灌 LLM 的文本最前面。

    为什么是文本前缀，而不是塞进 data 里当一个键（两个坑一起绕）：

    1. **`truncate_json_safe` 按插入序保前缀键，超限就 break**（`executor.py:50-63`）。
       quality 塞在 data 尾部时，大结果一来它就被静默丢掉，只留一行「丢弃字段」——
       数据质量警告恰恰是最不能丢的那个。
    2. **`to_legacy_dict` 里 `message` 优先于 `data`**：工具只要给了 `message`，
       整个 `data` 就不进 summary。quality 挂在 data 上的话，凡是给了 message 的
       工具（大量）LLM 一个字都看不到。

    前缀两条都绕过：它在最前面，截断永远先保它；它不属于 data，不受 message 优先影响。

    **只在数据降级时出声**。健康时一个字都不加 —— 每轮每个工具都挂一句「数据正常」
    是纯粹的 token 浪费，而且狼来了喊多了就没人听了。
    """
    if not quality:
        return text
    limitations = quality.get("limitations") or ()
    if not (quality.get("core_degraded") or limitations):
        return text                       # 健康 → 不出声

    parts = ["⚠️ 数据质量降级"]
    if limitations:
        parts.append("：" + "；".join(str(x) for x in limitations))
    if quality.get("core_degraded"):
        parts.append("。**核心行情/K线/技术面数据不可信，结论不得声称高置信度**")
    score = quality.get("overall_score")
    if score is not None:
        parts.append(f"（质量分 {score}/100，{quality.get('level') or '?'}）")
    return "".join(parts) + "\n" + text


def to_legacy_dict(envelope: "ToolEnvelope") -> dict:
    """把 ToolEnvelope 映射成 orchestrator/executor 目前消费的 legacy dict 形状
    {"ok","summary","widget"?,"navigate"?}，附带 business_result/error_code/data
    等结构化字段供 TurnMonitor/PolicyChecker 读取。executor.run_tool（普通工具）
    与 orchestrator._run_subagent（子代理）共用同一份映射逻辑，避免两处各写一套。

    延迟导入 truncate_json_safe 避免与 agents.executor 的模块级循环依赖
    （executor.py 顶部 import 本模块的 ErrorCode/ToolEnvelope）。
    """
    from agents.executor import _MAX_SUMMARY_CHARS, truncate_json_safe

    summary_source = envelope.message or envelope.error_detail
    if summary_source is None:
        summary_source = envelope.data if envelope.data is not None else ""
    text = summary_source if isinstance(summary_source, str) else json.dumps(
        summary_source, ensure_ascii=False, default=str)
    text = _prepend_quality(text, envelope.quality)
    out: dict = {
        "ok": envelope.ok,
        "summary": truncate_json_safe(text, _MAX_SUMMARY_CHARS),
        "business_result": envelope.business_result,
    }
    if envelope.quality is not None:
        out["quality"] = envelope.quality
    if envelope.error_code is not None:
        out["error_code"] = envelope.error_code.value
    if envelope.error_detail is not None:
        out["error_detail"] = envelope.error_detail
    if envelope.data is not None:
        out["data"] = envelope.data
    if envelope.widget is not None:
        out["widget"] = envelope.widget
    if envelope.navigate is not None:
        out["navigate"] = envelope.navigate
    if envelope.tokens:
        out["tokens"] = envelope.tokens
    return out

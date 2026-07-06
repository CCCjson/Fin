"""
Turn 级 append-only trace —— 「模型当时看到了什么、调了什么、返回了什么」的可回放存档。

每个 turn 结束（正常 DONE / 熔断收尾 / LLM 失败 / 断连取消）落一行 JSONL：
    backend/data/agent_traces/{session_id}.jsonl
一行一个 turn：本 turn 的增量 messages（含 system 注入与 tool 结果原文）+
TurnMonitor 的调用轨迹（verdict/耗时/args_hash）+ 干预记录 + token 用量 + 结束原因。
DecisionLog 只回答「决策是什么」；这里回答「决策是怎么来的」。

回滚开关：AGENT_TRACE=off。写盘失败只记日志，绝不影响对话主流程。

另有一份独立的「原始 tool 输出」落盘（write_raw_tool_call），见该函数注释：
上面这份 turn trace 里的 messages 是回灌 LLM 的 summary（已按 3000 字截断），
未来做训练语料时那份不够用，所以在 tool 结果产生的当下（尚未被截断/压缩）
另存一份完整原文，供将来蒸馏/评测使用。
"""
import json
import os
import time
from pathlib import Path
from typing import Any, Optional

from loguru import logger

_TRACE_DIR = Path(__file__).parent.parent / "data" / "agent_traces"
_RAW_TRACE_DIR = Path(__file__).parent.parent / "data" / "agent_traces_raw"

# 磁盘 GC —— trace 是 append-only 无轮转，不清会无限涨（raw 存完整原文更占）。
# 照搬 context.py::SessionStore 的既有惯用法：保留天数常量 + 节流间隔 + glob+mtime+unlink。
_TRACE_MAX_AGE_DAYS_DEFAULT = 14  # 与 context.py 的 _PERSIST_MAX_AGE_DAYS 默认值一致但独立开关
_TRACE_CLEANUP_INTERVAL_S = 600   # 节流：不需要每个 turn 都扫一遍两个目录
_last_trace_cleanup_at = 0.0      # 节流游标（模块级，见 _cleanup_traces）


def trace_enabled() -> bool:
    return os.getenv("AGENT_TRACE", "on").lower() not in ("off", "0", "false")


def _trace_max_age_days() -> int:
    try:
        return int(os.getenv("AGENT_TRACE_MAX_AGE_DAYS", _TRACE_MAX_AGE_DAYS_DEFAULT))
    except (TypeError, ValueError):
        return _TRACE_MAX_AGE_DAYS_DEFAULT


def _cleanup_traces() -> None:
    """过期 trace 文件 GC——覆盖 _TRACE_DIR 与 _RAW_TRACE_DIR 两个目录（各 glob
    *.jsonl，按 st_mtime 超过保留期就 unlink）。节流到最多每 _TRACE_CLEANUP_INTERVAL_S
    跑一次。失败只记 log.warning，绝不影响主对话流程（同本模块既有文档化原则）。"""
    global _last_trace_cleanup_at
    now = time.time()
    if now - _last_trace_cleanup_at < _TRACE_CLEANUP_INTERVAL_S:
        return
    _last_trace_cleanup_at = now
    try:
        cutoff = now - _trace_max_age_days() * 86400
        for d in (_TRACE_DIR, _RAW_TRACE_DIR):
            if not d.exists():
                continue
            for f in d.glob("*.jsonl"):
                try:
                    if f.stat().st_mtime < cutoff:
                        f.unlink(missing_ok=True)
                except OSError:
                    continue
    except Exception as e:  # noqa: BLE001 — GC 失败不可影响对话主流程
        logger.warning(f"trace 磁盘 GC 失败: {e}")


def raw_trace_enabled() -> bool:
    return os.getenv("AGENT_TRACE_RAW", "on").lower() not in ("off", "0", "false")


def write_turn_trace(
    session,
    *,
    model: str,
    reason: str,
    turn_usage: Optional[dict] = None,
) -> None:
    """把本 turn 落成一行 JSONL。session.turn_start_idx 标记本 turn 首条消息。

    reason: done | max_rounds | token_budget | llm_error | cancelled | await_confirm
    """
    if not trace_enabled():
        return
    # 每个 turn 结束都会调一次，天然的节流触发点（GC 内部自带节流+吞异常）。
    _cleanup_traces()
    try:
        start = max(0, int(getattr(session, "turn_start_idx", 0) or 0))
        mon = getattr(session, "turn_monitor", None)
        entry = {
            "ts": time.strftime("%Y-%m-%d %H:%M:%S"),
            "session_id": session.session_id,
            "model": model,
            "reason": reason,
            "turn_start_idx": start,
            # 增量 messages：本 turn 的 user/assistant/tool/system 注入原文
            "messages": session.messages[start:],
            "tool_records": [
                {"round": r.round, "call_id": r.call_id, "name": r.name,
                 "args_hash": r.args_hash, "verdict": r.verdict,
                 "elapsed_ms": r.elapsed_ms, "summary_chars": r.summary_chars,
                 "is_subagent": r.is_subagent, "compacted": r.compacted}
                for r in (mon.records if mon else [])
            ],
            "interventions": list(mon.interventions) if mon else [],
            "usage": turn_usage or {},
        }
        _TRACE_DIR.mkdir(parents=True, exist_ok=True)
        path = _TRACE_DIR / f"{session.session_id}.jsonl"
        with path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False, default=str) + "\n")
    except Exception as e:  # noqa: BLE001 — trace 不可影响主流程
        logger.warning(f"turn trace 写盘失败: {e}")


def write_raw_tool_call(
    session_id: str,
    *,
    call_id: str,
    name: str,
    args: dict,
    raw: Any,
    ok: bool,
    elapsed_ms: int,
    is_subagent: bool = False,
) -> None:
    """把一次 tool_call 的未瘦身原始返回落一行 JSONL：
        backend/data/agent_traces_raw/{session_id}.jsonl

    与 write_turn_trace 的关键区别：那边存的是回灌 LLM 的 summary（已截断/
    未来还会被 history 压缩覆盖）；这里存的是工具产生结果的那一刻、尚未截断
    的完整原文（executor.run_tool / to_legacy_dict 的 "data" 字段），一旦
    压缩发生这份原文就再也拿不回来了，所以要在源头单独存一份。

    回滚开关：AGENT_TRACE_RAW=off。写盘失败只记日志，绝不影响对话主流程。
    """
    if not raw_trace_enabled():
        return
    try:
        entry = {
            "ts": time.strftime("%Y-%m-%d %H:%M:%S"),
            "session_id": session_id,
            "call_id": call_id,
            "name": name,
            "args": args,
            "raw_result": raw,
            "ok": ok,
            "elapsed_ms": elapsed_ms,
            "is_subagent": is_subagent,
        }
        _RAW_TRACE_DIR.mkdir(parents=True, exist_ok=True)
        path = _RAW_TRACE_DIR / f"{session_id}.jsonl"
        with path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False, default=str) + "\n")
    except Exception as e:  # noqa: BLE001 — trace 不可影响主流程
        logger.warning(f"原始 tool 输出落盘失败: {e}")

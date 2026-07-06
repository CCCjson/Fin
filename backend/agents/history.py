"""
会话历史压缩 —— 防止 session.messages 无限增长导致每轮全量重发 token 爆炸。

两段式（只在估算超 soft_limit 时触发，跑在 orchestrator 同一 worker 线程里）：
  Stage A（确定性，零成本）：旧回合块里的长 tool 结果截短、剥过期页面快照。
  Stage B（LLM，仍超限才跑）：旧回合块整体交给 cheap 模型做摘要，
    替换成紧跟 system 的一条 user 消息（user 角色天然规避 tool 配对问题）。

格式安全的关键：按「回合块」（每条 user 消息开启一块）整块处理，
tool 消息永远不会和它的 assistant.tool_calls 父消息分家。
"""
import json
import math
from typing import Optional

from loguru import logger

_SUMMARY_MARK = "[早前对话摘要（自动压缩）]"

# 实测本项目内容：中文散文 ~1.68 字符/token，JSON ~2.45；取 1.7 偏保守（宁可早压）
_CHARS_PER_TOKEN = 1.7

_SUMMARIZE_PROMPT = (
    "你是对话压缩器。把下面这段「量化交易助手 MoneyBill 与用户 Jason」的早前对话压缩成摘要，"
    "忽略细节与寒暄，但**必须完整保留**：\n"
    "1. 讨论过的股票名称+代码\n"
    "2. 提到的持仓、数量、价格、止损位等关键数字\n"
    "3. 给过的推荐/建议及其依据\n"
    "4. 未完成的用户意图或待办\n"
    "5. Jason 明确表达过的约束与偏好\n"
    "直接输出摘要正文（中文、条目式、尽量短），不要开场白。"
)


def _msg_chars(m: dict) -> int:
    content = m.get("content")
    n = len(content) if isinstance(content, str) else len(str(content or ""))
    for tc in m.get("tool_calls") or []:
        n += len(str(tc))
    return n


def estimate_tokens(messages: list[dict]) -> int:
    """粗估 messages 的 token 数（字符数 / 1.7，向上取整）。"""
    return math.ceil(sum(_msg_chars(m) for m in messages) / _CHARS_PER_TOKEN)


def _split_blocks(messages: list[dict]) -> list[list[dict]]:
    """system 之后按 user 消息切回合块；块首的非 user 消息（确认恢复等）并入上一块。"""
    blocks: list[list[dict]] = []
    current: list[dict] = []
    for m in messages:
        if m.get("role") == "user" and current:
            blocks.append(current)
            current = [m]
        else:
            current.append(m)
    if current:
        blocks.append(current)
    return blocks


def _compress_block_inplace(block: list[dict]) -> None:
    """Stage A：块内长 tool 结果截短（只改 content 字符串，不动结构）。"""
    from agents.executor import truncate_tool_content
    for m in block:
        if m.get("role") != "tool":
            continue
        content = m.get("content")
        if not isinstance(content, str):
            continue
        result = truncate_tool_content(content, gate_chars=300, keep_chars=200,
                                        marker="…[已压缩]")
        if result is not None:
            m["content"] = result[0]


def _render_for_summary(blocks: list[list[dict]]) -> str:
    """把回合块渲染成给摘要模型看的纯文本。"""
    lines: list[str] = []
    for block in blocks:
        for m in block:
            role = m.get("role")
            content = m.get("content") or ""
            if role == "assistant" and m.get("tool_calls"):
                calls = ", ".join(
                    tc.get("function", {}).get("name", "?")
                    for tc in m["tool_calls"])
                lines.append(f"助手（调用工具 {calls}）: {content}")
            elif role == "tool":
                lines.append(f"工具结果: {content[:300]}")
            elif isinstance(content, str) and content:
                who = {"user": "Jason", "assistant": "助手"}.get(role, role)
                lines.append(f"{who}: {content}")
    return "\n".join(lines)


def _llm_summarize(text: str, model: str) -> Optional[str]:
    from llm_client import stream_chat
    from agents.usage import USAGE
    out: list[str] = []
    try:
        for ev in stream_chat(
            [{"role": "system", "content": _SUMMARIZE_PROMPT},
             {"role": "user", "content": text}],
            model=model, tools=None,
        ):
            if ev["type"] == "done":
                p = ev.get("prompt_tokens", 0) or 0
                c = ev.get("completion_tokens", 0) or 0
                if p or c:
                    USAGE.record(model, p, c)
                msg = ev.get("message") or {}
                out.append(msg.get("content") or "")
    except Exception as e:  # noqa: BLE001 — 压缩失败不能坏对话主流程
        logger.warning(f"历史摘要 LLM 调用失败，本次跳过压缩: {e}")
        return None
    summary = "".join(out).strip()
    return summary or None


def compact_history(
    session,
    *,
    cheap_model: str,
    soft_limit: int = 24_000,
    keep_turns: int = 3,
) -> None:
    """历史超 soft_limit 时就地压缩 session.messages（保底 Stage A，必要时 Stage B 摘要）。

    keep_turns 为原样保留的回合块数，**含当前刚追加的回合**（默认=当前+最近2轮）。
    """
    messages = session.messages
    if not messages or messages[0].get("role") != "system":
        return
    before = estimate_tokens(messages)
    n_before = len(messages)
    if before <= soft_limit:
        return

    system_msg = messages[0]
    blocks = _split_blocks(messages[1:])
    # 已有旧摘要消息的话它自成一块（user 角色），会随旧块一起进再摘要输入
    if len(blocks) <= keep_turns:
        return
    old_blocks = blocks[:-keep_turns]
    recent_blocks = blocks[-keep_turns:]

    # Stage A：确定性压缩旧块
    for block in old_blocks:
        _compress_block_inplace(block)
    rebuilt = [system_msg] + [m for b in old_blocks for m in b] \
        + [m for b in recent_blocks for m in b]
    after_a = estimate_tokens(rebuilt)

    # Stage B：仍超限 → 旧块整体摘要成一条 user 消息
    stage_b_applied = False
    if after_a > soft_limit:
        summary = _llm_summarize(_render_for_summary(old_blocks), cheap_model)
        if summary:
            rebuilt = [system_msg,
                       {"role": "user", "content": f"{_SUMMARY_MARK}\n{summary}"}] \
                + [m for b in recent_blocks for m in b]
            # 旧页面快照可能已被摘掉，重置签名让下一条重新附全量页面上下文
            session.last_page_sig = None
            stage_b_applied = True

    session.messages[:] = rebuilt
    logger.info(
        f"[compact] 历史压缩 {before} → {estimate_tokens(rebuilt)} est.tokens "
        f"(msgs {n_before} → {len(rebuilt)}, stage_b={stage_b_applied})")

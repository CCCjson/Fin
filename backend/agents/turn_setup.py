"""
Turn 开局副作用 —— 从 orchestrator.run_stream 拆出来的「一轮新用户消息」开场白。

职责：系统提示注入（仅首次）、tool_groups 会话初始化、page_context 格式化/去重剥离、
历史压缩触发、TurnMonitor 重建。拆出来是为了让 orchestrator._loop 只剩纯粹的
Reason→Act→Observe 骨架——这部分是"turn 开始前要做好的准备"，不是循环本体。

移动是纯粹的代码搬运，逻辑与移动前逐字节一致（含注释里记录的边界条件），
不在本次拆分中顺带修改行为。
"""
import hashlib
import re
from typing import Optional

from loguru import logger

from agents.context import AgentSession
from agents.skills_loader import load_monitor_system_prompt

_VISIBLE_TEXT_MAX = 4000  # 服务端二次封顶，双保险控 token

# 页面可见文本块的分隔符（剥离旧消息里的过期截图依赖它，改动需同步 _strip_stale_page_text）
_PC_BEGIN = "<<<页面可见内容"
_PC_END = "页面可见内容>>>"
_PC_BLOCK_RE = re.compile(
    re.escape(_PC_BEGIN) + r".*?" + re.escape(_PC_END), re.DOTALL)


def _strip_stale_page_text(messages: list[dict]) -> None:
    """把历史 user 消息里的「页面可见内容」块剥掉（保留页面名/实体头）。

    旧的文字版截图在有新页面上下文后没有价值，却各占最多 4KB 一直躺在历史里。
    只改消息 content 字符串，不动消息结构，对 OpenAI 格式绝对安全。
    """
    for m in messages:
        if m.get("role") != "user":
            continue
        content = m.get("content")
        if isinstance(content, str) and _PC_BEGIN in content:
            m["content"] = _PC_BLOCK_RE.sub("（页面快照已过期，略）", content)


def format_page_context(pc: dict, *, visible_unchanged: bool = False) -> str:
    """把前端传来的页面上下文拼成一段提示，实现「针对当前页面提问」。"""
    page = pc.get("page") or "未知页面"
    entities = pc.get("entities") or {}
    parts = [f"[当前 Jason 正在查看：{page}页]"]
    if entities:
        kv = "，".join(f"{k}={v}" for k, v in entities.items())
        parts.append(f"[页面对象：{kv}]")
    parts.append("（如果 Jason 的问题指代「这只股票/这个回测/当前」，优先理解为上述页面对象。）")

    visible = (pc.get("visible_text") or "").strip()
    if visible and visible_unchanged:
        parts.append("[页面可见内容与上一条消息相同，未重复附上]")
    elif visible:
        if len(visible) > _VISIBLE_TEXT_MAX:
            visible = visible[:_VISIBLE_TEXT_MAX] + "…（内容过长已截断）"
        parts.append(
            "\n\n以下是 Jason 当前页面上的可见内容（含屏幕文字与输入框/筛选框里的值），"
            "用它理解他的指代与当前对象。"
            "**如果其中或上面的页面对象已包含股票代码/名称，就直接据此调用工具"
            "（如实时行情、五维体检、日线），不要反过来让 Jason 再发一遍代码。**"
            "但最终给出的数字/行情/持仓请以工具返回为准，不要照抄屏幕文字：\n"
            f"{_PC_BEGIN}\n{visible}\n{_PC_END}"
        )
    return " ".join(parts)


def _void_pending_confirm(session: AgentSession) -> None:
    """用户没响应确认弹窗就发了新消息：自动作废挂起的确认，保持 tool_calls 配对。

    不作废的话，历史里 assistant 的 tool_calls 缺响应，上游 API 每次调用都 400，
    整个会话永久损坏（连事后补 confirm 都救不回——tool 响应必须紧跟 tool_calls）。
    """
    pending = session.pending_tool_call
    if pending is None:
        return
    session.pending_tool_call = None
    session.messages.append({
        "role": "tool", "tool_call_id": pending.id,
        "content": "用户未在确认弹窗中响应就发起了新对话，该操作已自动取消，未执行。"})
    logger.info(f"悬空确认自动作废: {pending.name} ({pending.id})")


def _repair_orphan_tool_calls(messages: list[dict]) -> int:
    """补齐历史中缺失响应的 assistant tool_calls（插占位 tool 消息），返回修补条数。

    孤儿的来源：旧版悬空确认、进程中断、异常截断。一旦存在，整个会话不可用；
    每 turn 开局兜底扫一遍，把存量损坏会话也救活。
    """
    repaired = 0
    i = 0
    while i < len(messages):
        m = messages[i]
        if m.get("role") == "assistant" and m.get("tool_calls"):
            expected = [tc.get("id") for tc in m["tool_calls"] if tc.get("id")]
            j = i + 1
            answered = set()
            while j < len(messages) and messages[j].get("role") == "tool":
                answered.add(messages[j].get("tool_call_id"))
                j += 1
            for tc_id in expected:
                if tc_id not in answered:
                    messages.insert(j, {
                        "role": "tool", "tool_call_id": tc_id,
                        "content": "该操作响应缺失，已作废。"})
                    j += 1
                    repaired += 1
            i = j
        else:
            i += 1
    if repaired:
        logger.warning(f"历史修补：补齐 {repaired} 条孤儿 tool_calls 响应")
    return repaired


def prepare_turn(
    session: AgentSession,
    user_message: str,
    page_context: Optional[dict],
) -> None:
    """把一条新用户消息落进 session.messages，做好本 turn 开局的所有副作用：
    悬空确认作废/历史修补、系统提示注入（仅空会话）、tool_groups 初始化、
    page_context 格式化/去重、历史压缩、TurnMonitor 重建。"""
    # 必须在追加 user 消息之前：pending 意味着最后一条 assistant 带未响应的
    # tool_calls，作废响应要紧跟其后才合法。
    _void_pending_confirm(session)
    _repair_orphan_tool_calls(session.messages)

    if not session.messages:
        session.messages.append(
            {"role": "system", "content": load_monitor_system_prompt()})

    # 工具分组：核心常驻 + 按页面预载 + skill 声明的组；已加载的组 session 内粘滞
    from agents import tool_groups
    if tool_groups.grouping_enabled():
        page_path = (page_context or {}).get("path")
        base = tool_groups.initial_allowed(page_path)
        # 第三来源：monitor.md frontmatter 里 enabled_tools 声明的工具组
        # （无 frontmatter 时返回空，行为与之前完全一致）。未知/已含的组由 expand 自行忽略。
        from agents.skills_loader import parse_skill_frontmatter
        skill_groups = parse_skill_frontmatter("monitor.md").get("enabled_tools") or []
        if skill_groups:
            base, _added, _unknown = tool_groups.expand(base, skill_groups)
        session.allowed_tools = (
            base if session.allowed_tools is None
            else session.allowed_tools | base)

    content = user_message
    if page_context:
        session.page_context = page_context
        visible = (page_context.get("visible_text") or "").strip()
        sig = (hashlib.md5(
            f"{page_context.get('page')}\x00{visible}".encode()).hexdigest()
            if visible else None)
        unchanged = bool(sig) and sig == session.last_page_sig
        if not unchanged:
            # 新快照到来，历史里的旧快照即过期，剥掉省 token
            _strip_stale_page_text(session.messages)
            session.last_page_sig = sig
        content = (format_page_context(page_context, visible_unchanged=unchanged)
                   + "\n\n" + user_message)
    session.messages.append({"role": "user", "content": content})
    session.touch()

    # 历史超阈值自动压缩（确认等待中不动历史，避免打断 tool 配对）
    if session.pending_tool_call is None:
        from llm_config import get_cheap_model
        from agents.history import compact_history
        compact_history(session, cheap_model=get_cheap_model())

    # TurnMonitor：新用户消息 = 新 turn，重建监控器。
    # 必须在 compact_history 之后建——压缩会删旧消息，先建下标会漂移。
    from agents import turn_monitor
    session.turn_start_idx = len(session.messages) - 1  # trace 增量起点
    session.turn_monitor = (
        turn_monitor.TurnMonitor(turn_start_idx=session.turn_start_idx)
        if turn_monitor.monitor_enabled() else None)
    # 新 turn = 数据质量重新开始记（P0-2）。与 turn_start_idx 必须同步清 ——
    # 不清的话上一轮「行情抓失败」会一直挂着，把后面每一轮都误报成降级。
    session.turn_quality.clear()

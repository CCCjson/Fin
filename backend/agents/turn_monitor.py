"""
TurnMonitor — 工具调用流程监控 + 阶梯干预（纯规则，零 LLM 成本）。

监控每个 turn（一条用户消息到最终回答）内的工具调用循环：
- 观测：每个调用的质量判定（有效/空结果/失败/重复）、耗时、每轮 token；
- 干预（阶梯，格式安全——只注入 system 消息 / 伪造 tool 回复 / 就地截短 content）：
    L1 重复调用拦截（同工具+同参数不真跑，回放首次结果摘录）
    L2 连续坏调用纠偏（注入 system 提示检查参数/停止重试）
    L3 软预算提醒（turn token 超软线提示收敛）
    L4 轮中压缩（截短本 turn 早期工具结果，治「每轮重发全history」的平方增长）
    L5 硬熔断（超硬线强制无工具收尾，orchestrator 复用 max_rounds 机制）

实例挂 AgentSession.turn_monitor（跨确认中断续跑存活）；orchestrator 全程
`if mon:` 守卫，env AGENT_TURN_MONITOR=off 时行为与无监控完全一致。
"""
import hashlib
import json
import os
import re
import time
from dataclasses import dataclass, field
from typing import Callable, Optional

from loguru import logger

from agents.events import EV, emit


# ──────────────────── 配置（运行时读 env，照 tool_groups 先例） ────────────────────

def monitor_enabled() -> bool:
    return os.getenv("AGENT_TURN_MONITOR", "on").lower() not in ("off", "0", "false")


def intervene_enabled() -> bool:
    """细粒度回滚：off 时只观测不干预（拦截/注入/压缩/熔断全部跳过）。"""
    return os.getenv("AGENT_TM_INTERVENE", "on").lower() not in ("off", "0", "false")


def _cfg_int(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, "") or default)
    except ValueError:
        return default


def _soft_tokens() -> int:
    return _cfg_int("AGENT_TM_SOFT_TOKENS", 50_000)


def _compact_tokens() -> int:
    return _cfg_int("AGENT_TM_COMPACT_TOKENS", 60_000)


def _compact_step() -> int:
    return _cfg_int("AGENT_TM_COMPACT_STEP", 30_000)


def _hard_tokens() -> int:
    return _cfg_int("AGENT_TM_HARD_TOKENS", 120_000)


def _bad_streak() -> int:
    return _cfg_int("AGENT_TM_BAD_STREAK", 3)


def _dup_ttl_s() -> int:
    return _cfg_int("AGENT_TM_DUP_TTL_S", 120)


# 实时类工具：数据随时间变化，超 TTL 允许用相同参数重查（盘中价格会变）
REFRESHABLE_TOOLS = {
    "get_realtime_quote",
    "get_market_pulse",
    "get_system_pulse",
    "get_position_guard_status",
    "get_positions",
    "get_intraday_check",
}

# 质量判定：空结果类文案（须同时满足「短」才判空，防误伤正常短答案）
_EMPTY_RE = re.compile(
    r"无数据|没有数据|暂无|未找到|未查到|无结果|没有找到|无记录|无相关|查询为空"
    r"|not found|no data|no results?",
    re.IGNORECASE)

_COMPACT_MARK = "…[监控已压缩，如需完整数据请重新调用该工具]"

# per-tool 判定钩子：工具名 → fn(summary_str) -> verdict|None（None 走通用启发式）
VERDICT_HOOKS: dict[str, Callable[[str], Optional[str]]] = {}


def verdict_hook(name: str) -> Callable:
    """注册某工具的专属质量判定（如休市日「今日无信号」其实是正常结果）。"""
    def deco(fn: Callable[[str], Optional[str]]) -> Callable:
        VERDICT_HOOKS[name] = fn
        return fn
    return deco


# ──────────────────── 数据结构 ────────────────────

@dataclass
class ToolCallRecord:
    round: int
    call_id: str
    name: str
    args_hash: str
    verdict: str = ""        # ok | empty | error | duplicate | meta | confirm_cancelled | negative
    elapsed_ms: int = 0
    summary_chars: int = 0
    summary_head: str = ""   # 首 500 字符，重复拦截时回放给模型
    is_subagent: bool = False
    executed_at: float = 0.0
    msg_index: int = -1      # 该 tool 回复在 session.messages 的下标（轮中压缩定位）
    compacted: bool = False


def _args_hash(name: str, args: Optional[dict]) -> str:
    """参数指纹：排序抹平顺序差异；剔 _ 前缀内部键（_confirmed 不产生新 hash）。"""
    clean = {k: v for k, v in (args or {}).items() if not str(k).startswith("_")}
    raw = name + "\x00" + json.dumps(clean, sort_keys=True, ensure_ascii=False, default=str)
    return hashlib.md5(raw.encode()).hexdigest()


def classify(name: str, result: dict) -> str:
    """从 executor 归一化结果 {"ok", "summary"} 判定调用质量。"""
    if not result.get("ok", True):
        return "error"
    # ToolEnvelope 迁移后的工具（如 place_order 风控拒单）会在 result 里直接带
    # 结构化的 business_result="negative"——这是工具自己声明的"诚实的否"，比下面
    # 靠猜 summary 字符串的正则/JSON-key 启发式更准，优先信它。它是终态答案
    # （如风控正确拒单），不是「调用失败/空结果」，不计入 _BAD_VERDICTS 的连续坏调用计数。
    if result.get("business_result") == "negative":
        return "negative"
    summary = result.get("summary")
    s = summary.strip() if isinstance(summary, str) else json.dumps(
        summary, ensure_ascii=False, default=str)
    hook = VERDICT_HOOKS.get(name)
    if hook is not None:
        v = hook(s)
        if v is not None:
            return v
    if not s:
        return "empty"
    if len(s) <= 80 and _EMPTY_RE.search(s):
        return "empty"
    try:
        parsed = json.loads(s)
        if parsed in ([], {}, None):
            return "empty"
        if isinstance(parsed, dict):
            vals = [parsed.get(k) for k in
                    ("items", "results", "data", "list", "stocks", "signals", "records")
                    if k in parsed]
            if vals and all(v in ([], {}, None, 0) for v in vals):
                return "empty"
    except (ValueError, TypeError):
        pass
    return "ok"


_BAD_VERDICTS = {"error", "empty", "duplicate"}


@dataclass
class TurnMonitor:
    """一个 turn 的监控器。新用户消息重建；确认中断续跑沿用同一实例。"""

    turn_start_idx: int                      # 本 turn 首条消息在 session.messages 的下标
    records: list[ToolCallRecord] = field(default_factory=list)
    seen: dict[str, ToolCallRecord] = field(default_factory=dict)
    consecutive_bad: int = 0
    turn_prompt: int = 0
    turn_completion: int = 0
    round_no: int = 0
    soft_warned: bool = False
    nudged_at_streak: int = 0
    last_compact_at_tokens: int = 0
    hard_tripped: bool = False
    started_at: float = field(default_factory=time.time)
    interventions: list[dict] = field(default_factory=list)

    # ── 记账 ──

    def record_llm_round(self, prompt: int, completion: int) -> None:
        self.turn_prompt += prompt
        self.turn_completion += completion
        self.round_no += 1

    def record_subagent_tokens(self, total: int) -> None:
        """subagent 内部 LLM 消耗计入 turn 预算（挂 completion 侧，不增轮数）。"""
        self.turn_completion += total

    @property
    def turn_tokens(self) -> int:
        return self.turn_prompt + self.turn_completion

    # ── 重复拦截（L1） ──

    def check_duplicate(self, tc: dict, td) -> Optional[str]:
        """命中重复返回拦截文案（作为该 tool_call_id 的 tool 回复），否则 None 放行。"""
        if not intervene_enabled():
            return None
        if td is not None and getattr(td, "requires_confirmation", False):
            return None  # 确认流工具豁免：续跑会再现同一调用，且人工确认本身是闸门
        h = _args_hash(tc["name"], tc.get("args"))
        first = self.seen.get(h)
        if first is None:
            return None
        if tc["name"] in REFRESHABLE_TOOLS and (
                time.time() - first.executed_at) > _dup_ttl_s():
            return None  # 实时类数据超时可刷新
        # 首次失败允许原样重试一次（瞬时网络错误）；第 3 次相同调用无论如何拦
        if first.verdict == "error":
            retries = sum(1 for r in self.records if r.args_hash == h)
            if retries < 2:
                return None
        head = first.summary_head or "（首次结果为空）"
        # 进 interventions 供回合汇总回看（实时展示由 TOOL_RESULT 的 duplicate 徽标表达，不另发事件）
        self.interventions.append({
            "action": "duplicate_block", "round": self.round_no,
            "detail": {"tool": tc["name"]}})
        self._log_intervention("duplicate_block", tool=tc["name"])
        return (
            f"（监控拦截：本轮已用完全相同的参数调用过 {tc['name']}，未重复执行。\n"
            f"首次结果摘录：{head}\n"
            "请基于已有结果继续，或改用不同参数；若数据确实不足，"
            "请直接如实告知 Jason，不要再原样重试。）")

    # ── 结果登记 ──

    def record_result(self, tc: dict, td, result: dict,
                      elapsed_ms: int, msg_index: int) -> ToolCallRecord:
        summary = result.get("summary")
        s = summary if isinstance(summary, str) else json.dumps(
            summary, ensure_ascii=False, default=str)
        rec = ToolCallRecord(
            round=self.round_no,
            call_id=tc.get("id", ""),
            name=tc["name"],
            args_hash=_args_hash(tc["name"], tc.get("args")),
            verdict=classify(tc["name"], result),
            elapsed_ms=elapsed_ms,
            summary_chars=len(s),
            summary_head=s[:500],
            is_subagent=bool(td is not None and getattr(td, "is_subagent", False)),
            executed_at=time.time(),
            msg_index=msg_index,
        )
        self.records.append(rec)
        # 覆写而不是 setdefault：self.seen 要存的是"最近一次"执行记录，不是
        # "第一次"——否则 REFRESHABLE_TOOLS 的 TTL 判断会永远比对第一次的旧
        # 时间戳，一旦 TTL 到期就变成永久放行（失去"隔 TTL 才刷新一次"的节流
        # 意图）；且一次失败重试成功后，重复调用会一直回放最初那条过期的失败
        # 摘要而不是最近的真实结果。error-retry 豁免（下方 error 分支）和
        # _compact_turn_inplace() 的 pop 都不依赖"第一次"这个语义，覆写安全。
        self.seen[rec.args_hash] = rec
        if rec.verdict in _BAD_VERDICTS:
            self.consecutive_bad += 1
        else:
            self.consecutive_bad = 0
            self.nudged_at_streak = 0
        return rec

    def record_duplicate(self, tc: dict) -> ToolCallRecord:
        """被拦截的调用也进轨迹（verdict=duplicate，计入坏 streak）。"""
        rec = ToolCallRecord(
            round=self.round_no, call_id=tc.get("id", ""), name=tc["name"],
            args_hash=_args_hash(tc["name"], tc.get("args")),
            verdict="duplicate", executed_at=time.time())
        self.records.append(rec)
        self.consecutive_bad += 1
        return rec

    def record_meta(self, tc: dict) -> ToolCallRecord:
        """load_toolgroup 等元工具：进轨迹但不进 streak。"""
        rec = ToolCallRecord(
            round=self.round_no, call_id=tc.get("id", ""), name=tc["name"],
            args_hash="", verdict="meta", executed_at=time.time())
        self.records.append(rec)
        return rec

    # ── 轮首决策（L2-L5 汇聚点，格式安全窗口：上一轮 tool 回复已全部 append） ──

    def pre_round(self, session) -> tuple[list[str], bool]:
        """返回 (要 yield 的 NDJSON 事件列表, 是否硬熔断停机)。"""
        events: list[str] = []
        if not intervene_enabled():
            return events, False

        # L5 硬熔断
        if self.turn_tokens >= _hard_tokens():
            self.hard_tripped = True
            events.append(self._intervention_event(
                "hard_budget_stop", turn_tokens=self.turn_tokens))
            self._log_intervention("hard_budget_stop", turn_tokens=self.turn_tokens)
            return events, True

        # L2 连续坏调用纠偏（3 发一次，6 升级再发一次…每 +streak 阈值发一次）
        streak = self.consecutive_bad
        threshold = _bad_streak()
        if streak >= threshold and streak >= self.nudged_at_streak + threshold:
            self.nudged_at_streak = streak
            session.messages.append({
                "role": "system",
                "content": (
                    f"（监控提示：最近 {streak} 次工具调用连续失败/空结果/重复。"
                    "请先检查参数是否正确（股票代码、日期格式、必填项）；"
                    "若数据确实不存在，请立即停止重试并如实告知 Jason，"
                    "给出替代建议，不要继续空转。）"),
            })
            events.append(self._intervention_event("bad_streak_nudge", streak=streak))
            self._log_intervention("bad_streak_nudge", streak=streak)

        # L3 软预算提醒（只警一次）
        if not self.soft_warned and self.turn_tokens >= _soft_tokens():
            self.soft_warned = True
            session.messages.append({
                "role": "system",
                "content": (
                    f"（系统提示：本轮 token 消耗已超 {_soft_tokens() // 10000} 万。"
                    "请尽快收敛：优先基于已获得的结果直接回答，"
                    "仅在确有必要时再调用极少量工具。）"),
            })
            events.append(self._intervention_event(
                "soft_budget", turn_tokens=self.turn_tokens))
            self._log_intervention("soft_budget", turn_tokens=self.turn_tokens)

        # L4 轮中压缩（起跳线后每 +step 可再压）
        if (self.turn_tokens >= _compact_tokens()
                and self.turn_tokens - self.last_compact_at_tokens >= _compact_step()):
            n, saved = self._compact_turn_inplace(session)
            if n:
                self.last_compact_at_tokens = self.turn_tokens
                events.append(self._intervention_event(
                    "midturn_compact", compacted_msgs=n, saved_chars=saved))
                self._log_intervention("midturn_compact", compacted=n, saved_chars=saved)

        return events, False

    def _compact_turn_inplace(self, session, keep_recent: int = 4,
                              max_chars: int = 400) -> tuple[int, int]:
        """截短本 turn 早期工具结果。只改 content 字符串，tool 配对绝对安全。

        被压缩的记录移出 seen——允许模型真的重取全量而不被重复拦截。
        返回 (压缩条数, 省下字符数)。
        """
        candidates = [r for r in self.records
                      if r.msg_index >= 0 and not r.compacted]
        candidates = candidates[:-keep_recent] if keep_recent else candidates
        n, saved = 0, 0
        for rec in candidates:
            if rec.msg_index >= len(session.messages):
                continue
            m = session.messages[rec.msg_index]
            if m.get("role") != "tool":
                continue  # 防御：下标漂移就跳过，绝不误伤别的消息
            content = m.get("content")
            if not isinstance(content, str):
                rec.compacted = True
                continue
            from agents.executor import truncate_tool_content
            result = truncate_tool_content(content, gate_chars=max_chars,
                                            keep_chars=max_chars, marker=_COMPACT_MARK)
            if result is None:
                rec.compacted = True
                continue
            m["content"], delta = result
            saved += delta
            rec.compacted = True
            self.seen.pop(rec.args_hash, None)
            n += 1
        return n, saved

    # ── 事件/汇总 ──

    def _intervention_event(self, action: str, **detail) -> str:
        entry = {"action": action, "round": self.round_no, "detail": detail}
        self.interventions.append(entry)
        return emit(EV.MONITOR, kind="intervention", **entry)

    @staticmethod
    def _log_intervention(action: str, **kw) -> None:
        logger.info(f"[monitor] intervention={action} {kw}")

    def summary_fields(self) -> dict:
        verdicts: dict[str, int] = {}
        for r in self.records:
            verdicts[r.verdict] = verdicts.get(r.verdict, 0) + 1
        return {
            "rounds": self.round_no,
            "calls": len(self.records),
            "verdicts": verdicts,
            "elapsed_ms_total": sum(r.elapsed_ms for r in self.records),
            "subagent_calls": sum(1 for r in self.records if r.is_subagent),
            "turn_tokens": {"prompt": self.turn_prompt,
                            "completion": self.turn_completion},
            "interventions": self.interventions,
        }

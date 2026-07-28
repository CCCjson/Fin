"""策略变更提案 —— AI 提议怎么改，以及**确定性算出来的人话 diff**（S2）。

# 为什么 diff 必须由代码算，不能让 AI 自己描述

`00-PLAN §3` 裁决 3 里那条决定性理由：**Jason 明确要求提案「不要是代码细节」**。
DSL 让提案能变成「入场 composite 阈值 60 → 55，因为…」这种人话 —— 但前提是
**那句人话必须准确**。让 AI 描述自己改了什么，它可能说漏（改了三处只说两处）、
说错（说改了阈值实际动了冷却时间），而 Jason 要审的正是「到底改了哪几个数」。

所以：AI 交**完整的新 spec**（它已经会写，`compile_crypto_strategy` 就在干这个），
差异由 `diff_specs()` 逐字段比出来。**提案里 AI 写的 `change_summary` 只是它的说法，
diff 才是事实**，两者不一致时以 diff 为准 —— UI/preview 上永远优先显示 diff。
"""
from __future__ import annotations

import json
from typing import Any

from common.market_time import utc_now

# 字段路径 → 人话。没登记的路径回落到路径本身（不静默丢弃 —— 少显示一条改动，
# 就是让 Jason 在不知情的情况下批准了它）。
_FIELD_LABELS: dict[str, str] = {
    "name": "策略名",
    "strategy_kind": "策略类型",
    "interval_minutes": "tick 节奏（分钟）",
    "capital_basis": "本金口径",
    "mode": "运行模式",
    "universe.symbols": "交易对白名单",
    "entry_rules.cooldown_minutes": "同币两次进场最小间隔（分钟）",
    "exit_rules.use_atr_stop": "是否用 ATR 止损",
    "exit_rules.use_take_profit": "是否用止盈",
    "position_policy.target_pct_source": "目标仓位来源",
    "position_policy.fixed_target_pct": "固定目标仓位",
    "position_policy.max_position_pct": "单仓上限",
    "position_policy.per_symbol_exposure_cap_pct": "单币敞口上限",
    "cost_model.taker_fee_pct": "taker 费率",
    "cost_model.slippage_pct": "滑点假设",
    "cost_model.slippage_source": "滑点来源",
    "cost_model.min_net_edge_pct": "最小净边际",
    "cost_model.target_edge_source": "毛边际来源",
    "cost_model.fixed_target_edge_pct": "固定毛边际",
    "guardrails.per_order_notional_usdt": "单笔名义金额上限（USDT）",
    "guardrails.max_orders_per_day": "单日下单笔数上限",
    "guardrails.max_round_trips_per_day": "单日往返次数上限",
    "guardrails.max_fees_per_day_usdt": "单日手续费上限（USDT）",
    "guardrails.daily_loss_pct": "当日回撤熔断阈值",
    "guardrails.symbol_whitelist": "护栏白名单",
    "guardrails.max_confirm_slippage_pct": "确认时最大漂移",
    "entry_rules.when": "入场条件",
    "exit_rules.when": "出场条件",
    "universe.screen_filter": "动态筛选条件",
}

# 改这几个字段等于改**安全边界**，不是调参 —— preview 里要单独标红。
#
# ⛔ **`mode` 不在这里，也不进 diff**（S2 复审补）：它是**运行态**不是策略配置 ——
# `arm()` 无条件写 `live`、`enable_paper()` 无条件写 `paper`。把它算进 diff 的后果是
# 拿 armed 的 v1 比 draft 的 v2，**每次 arm 预览都会冒一条「🔒 运行模式：live → paper」**
# 的假告警，而 arm 完立刻就是 live。🔒 的全部价值在于它很少出现，每次都亮等于训练
# Jason 无视它。
_SAFETY_PATHS = frozenset({
    "guardrails.per_order_notional_usdt", "guardrails.max_orders_per_day",
    "guardrails.max_round_trips_per_day", "guardrails.max_fees_per_day_usdt",
    "guardrails.daily_loss_pct", "guardrails.symbol_whitelist",
    "guardrails.max_confirm_slippage_pct",
    "position_policy.max_position_pct", "position_policy.per_symbol_exposure_cap_pct",
})

# 运行态字段：由生命周期动作（arm/enable_paper/pause）决定，不是提案的内容。
_RUNTIME_PATHS = frozenset({"mode"})

# 条件树（ConditionGroup）不逐条比 —— 嵌套结构的字段级 diff 反而看不懂。
# 整棵树变了就整棵渲染成人话（`_render_group`）。
_CONDITION_PATHS = frozenset({"entry_rules.when", "exit_rules.when",
                              "universe.screen_filter"})

_OPS = {"gt": ">", "gte": "≥", "lt": "<", "lte": "≤", "eq": "=",
        "in": "属于", "between": "介于"}


def _render_cond(c: dict[str, Any]) -> str:
    raw_op = str(c.get("op") or "")
    op = _OPS.get(raw_op, raw_op)
    v = c.get("value")
    if isinstance(v, list) and c.get("op") == "between" and len(v) == 2:
        v = f"{v[0]}~{v[1]}"
    elif isinstance(v, list):
        v = "/".join(str(x) for x in v)
    return f"{c.get('field')} {op} {v}"


def _render_group(g: dict[str, Any] | None) -> str:
    if not g:
        return "（无）"
    parts = []
    if g.get("all_of"):
        parts.append("同时满足[" + "，".join(_render_cond(c) for c in g["all_of"]) + "]")
    if g.get("any_of"):
        parts.append("任一满足[" + "，".join(_render_cond(c) for c in g["any_of"]) + "]")
    return "；".join(parts) or "（无条件）"


def _flatten(spec: dict[str, Any]) -> dict[str, Any]:
    """spec dict → {点分路径: 值}。

    只展开一层：DSL 的第二层要么是标量（`cost_model.taker_fee_pct`），要么就是条件树
    （`entry_rules.when`）—— 后者整棵当一个值，由 `_fmt`/`_render_group` 渲染成人话。
    穷举验证过 spec 的全部 38 个叶子字段：改任意一个都能被检出，且 from/to 渲染不同。
    ⚠️ 以后 DSL 加了**三层嵌套的标量**，这里要跟着改，否则那个字段的改动会静默漏检 ——
    「少显示一条改动」等于让 Jason 在不知情的情况下批准了它。
    """
    out: dict[str, Any] = {}
    for k, v in spec.items():
        if isinstance(v, dict):
            for k2, v2 in v.items():
                out[f"{k}.{k2}"] = v2
        else:
            out[k] = v
    return out


def _fmt(path: str, v: Any) -> str:
    if path in _CONDITION_PATHS:
        return _render_group(v if isinstance(v, dict) else None)
    if v is None:
        return "（未设置）"
    if isinstance(v, bool):
        return "是" if v else "否"
    if isinstance(v, list):
        return "、".join(str(x) for x in v) or "（空）"
    return str(v)


def diff_specs(old: dict[str, Any], new: dict[str, Any]) -> list[dict[str, Any]]:
    """两个 spec dict 的人话差异。**确定性、无 LLM 参与。**

    Returns:
        `[{path, label, from, to, safety}]`，按「安全边界优先」排序 ——
        Jason 一眼先看到的应该是「单笔上限 500 → 5000」，不是「策略名改了」。
    """
    fo, fn = _flatten(old or {}), _flatten(new or {})
    out: list[dict[str, Any]] = []
    for path in sorted(set(fo) | set(fn)):
        if path in _RUNTIME_PATHS:
            continue                       # 运行态不是提案内容，见 _RUNTIME_PATHS 注释
        a, b = fo.get(path), fn.get(path)
        if a == b:
            continue
        out.append({
            "path": path,
            "label": _FIELD_LABELS.get(path, path),
            "from": _fmt(path, a),
            "to": _fmt(path, b),
            "safety": path in _SAFETY_PATHS,
        })
    out.sort(key=lambda d: (not d["safety"], d["path"]))
    return out


def diff_text(diff: list[dict[str, Any]]) -> str:
    """diff → 一段可以直接摆给 Jason 看的人话。"""
    if not diff:
        return "⚠️ 新旧 spec 完全相同 —— 这份提案没有实际改动。"
    lines = []
    for d in diff:
        mark = "🔒 " if d["safety"] else ""
        lines.append(f"{mark}{d['label']}：{d['from']} → {d['to']}")
    if any(d["safety"] for d in diff):
        lines.append("🔒 = 动到了安全边界（护栏/仓位/运行模式），不是普通调参。")
    return "\n".join(lines)


# ──────────────────────── 持久化 ────────────────────────

def _gen_proposal_id() -> str:
    import secrets
    return f"PS-{utc_now().strftime('%Y%m%d%H%M%S')}-{secrets.token_hex(3)}"


def create_proposal(*, base_strategy_id: str, family_id: str | None,
                    shortfall: str, change_summary: str, rationale: str,
                    expected_return_pct: float, expected_win_rate: float,
                    horizon_days: int, new_spec: dict[str, Any],
                    diff: list[dict[str, Any]]) -> dict[str, Any]:
    from data_engine.storage.database import get_session
    from data_engine.storage.models import CryptoStrategyProposal

    pid = _gen_proposal_id()
    session = get_session()
    try:
        row = CryptoStrategyProposal(
            proposal_id=pid, family_id=family_id, base_strategy_id=base_strategy_id,
            shortfall=shortfall, change_summary=change_summary, rationale=rationale,
            expected_return_pct=float(expected_return_pct),
            expected_win_rate=float(expected_win_rate),
            horizon_days=int(horizon_days),
            new_spec=json.dumps(new_spec, ensure_ascii=False, default=str),
            diff=json.dumps(diff, ensure_ascii=False),
            status="proposed")
        session.add(row)
        session.commit()
        return to_dict(row)
    finally:
        session.close()


def to_dict(row) -> dict[str, Any]:
    from common.market_time import utc_iso
    return {
        "proposal_id": row.proposal_id, "family_id": row.family_id,
        "base_strategy_id": row.base_strategy_id,
        "shortfall": row.shortfall, "change_summary": row.change_summary,
        "rationale": row.rationale,
        "expected_return_pct": row.expected_return_pct,
        "expected_win_rate": row.expected_win_rate,
        "horizon_days": row.horizon_days,
        "diff": json.loads(row.diff) if row.diff else [],
        "status": row.status, "applied_strategy_id": row.applied_strategy_id,
        "decision_note": row.decision_note,
        "created_at": utc_iso(row.created_at), "decided_at": utc_iso(row.decided_at),
    }


def staleness(proposal_id_or_row: Any) -> dict[str, Any]:
    """这份提案的 base 版本还是不是同族最新的。

    🔴 **为什么必须查（S2 复审）**：提案的 `base_strategy_id` 和 `new_spec` 都冻结在
    提出那一刻。同族有两份未决提案时：

        P1（tick 30→15）批准 → v2：tick=15
        P2（单笔 100→800，基于 v1）批准 → v3：**tick 被静默还原成 30**、单笔=800

    也就是 Jason 十分钟前批准的改动被下一次批准悄悄回滚，**而确认框里一个字都没提**
    （P2 的 diff 是对着 v1 算的，只会显示单笔那一条）。

    Returns:
        `{stale, base_version, latest_version, latest_strategy_id}`
    """
    from data_engine.storage.database import get_session
    from data_engine.storage.models import CryptoStrategy

    p = (proposal_id_or_row if isinstance(proposal_id_or_row, dict)
         else get_proposal(proposal_id_or_row))
    if not p:
        return {"stale": False}
    session = get_session()
    try:
        base = session.query(CryptoStrategy).filter(
            CryptoStrategy.strategy_id == p["base_strategy_id"]).first()
        if base is None:
            return {"stale": True, "reason": "基准版本已被删除"}
        family = base.family_id or base.strategy_id
        rows = session.query(CryptoStrategy).filter(
            (CryptoStrategy.family_id == family)
            | (CryptoStrategy.strategy_id == family)).all()
        latest = max(rows, key=lambda r: (r.version or 1))
        return {
            "stale": (latest.version or 1) > (base.version or 1),
            "base_version": base.version or 1,
            "latest_version": latest.version or 1,
            "latest_strategy_id": latest.strategy_id,
        }
    finally:
        session.close()


def supersede_siblings(family_id: str | None, keep_proposal_id: str) -> list[str]:
    """同族其它还挂着的提案标成 `superseded_by_newer`。

    它们的 base 已经不是最新版了，再批准就会回滚刚生效的改动（见 `staleness`）。
    ⛔ 别只依赖 `staleness` 在 apply 时拦 —— 那是**最后一道**，这里是让它们
    在列表里就显示成过期的，Jason 不用点进去才发现。
    """
    if not family_id:
        return []
    from data_engine.storage.database import get_session
    from data_engine.storage.models import CryptoStrategyProposal
    session = get_session()
    try:
        rows = session.query(CryptoStrategyProposal).filter(
            CryptoStrategyProposal.family_id == family_id,
            CryptoStrategyProposal.status == "proposed",
            CryptoStrategyProposal.proposal_id != keep_proposal_id).all()
        out = []
        for r in rows:
            r.status = "superseded_by_newer"
            r.decided_at = utc_now()
            r.decision_note = f"同族提案 {keep_proposal_id} 已批准，本提案的基准版本已过期"
            out.append(r.proposal_id)
        session.commit()
        return out
    finally:
        session.close()


def get_proposal(proposal_id: str) -> dict[str, Any] | None:
    from data_engine.storage.database import get_session
    from data_engine.storage.models import CryptoStrategyProposal
    session = get_session()
    try:
        row = session.query(CryptoStrategyProposal).filter(
            CryptoStrategyProposal.proposal_id == proposal_id).first()
        if row is None:
            return None
        d = to_dict(row)
        d["new_spec"] = json.loads(row.new_spec) if row.new_spec else None
        return d
    finally:
        session.close()


def list_proposals(*, family_id: str | None = None, status: str | None = None,
                   limit: int = 20) -> list[dict[str, Any]]:
    from data_engine.storage.database import get_session
    from data_engine.storage.models import CryptoStrategyProposal
    session = get_session()
    try:
        q = session.query(CryptoStrategyProposal)
        if family_id:
            q = q.filter(CryptoStrategyProposal.family_id == family_id)
        if status:
            q = q.filter(CryptoStrategyProposal.status == status)
        rows = q.order_by(CryptoStrategyProposal.created_at.desc()).limit(
            max(1, min(int(limit), 100))).all()
        return [to_dict(r) for r in rows]
    finally:
        session.close()


def mark_decided(proposal_id: str, *, status: str,
                 applied_strategy_id: str | None = None,
                 note: str | None = None) -> dict[str, Any] | None:
    """提案定案。⛔ **只允许从 `proposed` 出发** —— 幂等地挡住重复 apply
    （不然点两次确认会生成两个新版本，而第二个的 base 已经过时）。
    """
    from data_engine.storage.database import get_session
    from data_engine.storage.models import CryptoStrategyProposal
    session = get_session()
    try:
        row = session.query(CryptoStrategyProposal).filter(
            CryptoStrategyProposal.proposal_id == proposal_id).first()
        if row is None or row.status != "proposed":
            return None
        row.status = status
        row.applied_strategy_id = applied_strategy_id
        row.decision_note = note
        row.decided_at = utc_now()
        session.commit()
        return to_dict(row)
    finally:
        session.close()

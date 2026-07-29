"""
决策留痕类工具 —— 查 DecisionLog，复盘「系统当时为什么这么建议」+「后来对了吗」。

复用 decision_log.query_decisions（样本）+ decision_log.get_decision_stats（胜率）。
来源清单是 `common/decision_source.py` 生成的，**别在这个文件里手抄** —— 手抄的
下场就是 S0 修的那个病：`_SOURCES` 漏了 4 个 crypto 来源，MoneyBill 按 crypto
查不了胜率，而且不报错，只是查回 0 条。
"""
import json
import re
from typing import Any, Literal, Optional

from pydantic import BaseModel, Field

from agents.registry import tool
from agents.tool_envelope import ToolEnvelope
from common.decision_kind import ADVICE
from common.decision_source import (
    advice_alternative,
    describe_for_prompt,
    has_advice,
    is_known,
    kinds_of,
    label_of,
)

# ⚠️ pydantic 要**静态字面量**，没法 `Literal[*ALL_SOURCES]`，所以这串只能手写。
# 它与 `common.decision_source.SOURCES` 的一致性由
# `tests/test_decision_source_registry.py::test_literal_matches_registry` 钉死 ——
# 加了 source 忘了补这里 → 门禁红，而不是像以前那样静默查不到。
_SOURCES = Literal[
    "advisor", "cockpit", "crypto", "crypto_cockpit", "crypto_earn",
    "moneybill", "moneybill_recommend", "report_picks",
]

# 单条完整快照很大（input_snapshot 动辄几 KB），verbose 只在小 limit 下开放。
_VERBOSE_MAX_LIMIT = 3


class GetDecisionHistoryArgs(BaseModel):
    symbol: Optional[str] = Field(None, description="可选：股票代码，如 600519.SH")
    source: Optional[_SOURCES] = Field(None, description="可选：决策来源")
    action: Optional[Literal["BUY", "SELL", "HOLD", "AGGREGATE"]] = Field(
        None, description="可选：动作类型")
    entry_kind: Literal["advice", "execution", "ops", "all"] = Field(
        "advice",
        description=(
            "查哪一类留痕。advice=AI 建议（默认，胜率就是算它的）；"
            "execution=真实下单回执（成交/挂单/被风控拦）；ops=非交易操作（加自选、建预警…）；"
            "all=全部。**注意 stats/calibration 恒定只算 advice，不受本参数影响。**"
        ))
    start_date: Optional[str] = Field(None, description="可选：起始日期 YYYY-MM-DD")
    end_date: Optional[str] = Field(None, description="可选：结束日期 YYYY-MM-DD")
    outcome_status: Optional[Literal["completed", "pending", "unable"]] = Field(
        None, description="可选：后验评估状态。completed=已评出对错，pending=窗口未满，unable=没法评")
    horizon: Literal[5, 20] = Field(20, description="胜率的评估窗口：5 或 20 个交易日，默认 20")
    limit: int = Field(10, ge=1, le=50, description="最多返回几条**样本**，默认 10，按时间倒序")
    verbose: bool = Field(
        False,
        description=(
            f"返回完整快照（input_snapshot/output_summary/output_text/reasons 全文）。"
            f"**仅当 limit<={_VERBOSE_MAX_LIMIT} 时生效**，否则 token 会炸。"
            "查「当时到底看到了什么数据」时才开。"
        ))


# ── exec_state：把 execution 那堆「长得一模一样的 executed=false」拆开 ──────────
#
# 不做这个派生，「可见性」名不副实：库里 16 条 execution 全是 `executed=0`，
# LLM 只会说「你有 16 笔未成交订单」——**而真相是只有 5 张单真的挂在币安**，
# 6 次根本没到交易所（风控拦下 / 金额低于最小名义额），另 5 条是同一批单的重复留痕。
#
# 信息本来就全在 output_summary / output_text 里躺着，只是没结构化。
_ORDER_ID_RE = re.compile(r"order[=＝]\s*(\d+)")


def _summary_dict(d: dict[str, Any]) -> dict[str, Any]:
    """output_summary 已被 `_to_dict` json.loads 过，但解析失败时会原样返回字符串。"""
    s = d.get("output_summary")
    if isinstance(s, dict):
        return s
    if isinstance(s, str) and s.strip():
        try:
            v = json.loads(s)
            return v if isinstance(v, dict) else {}
        except (TypeError, ValueError):
            return {}
    return {}


def _derive_exec_state(d: dict[str, Any]) -> str:
    """filled｜resting｜blocked｜unknown。

    **判定顺序就是这个函数的全部内容，改之前先读完这段。**

    1. `state_unknown` 必须**排在 `reason` 之前**。`crypto_tools.place_crypto_order`
       在「请求已发出但超时且回查未果」时返回 `{state_unknown: True, reason: ...}`，
       它自己的文案写着「**这笔单可能已经成交**」。按 `reason` 判成 `blocked`，
       等于告诉 LLM「钱没动」—— 而这恰恰是**唯一一个钱可能真动了**的状态。
    2. `resting` 排在 `reason` 之前：两者同时出现时，「钱已经挂出去了」比「被拦下」
       更该被看见。
    3. 结构化字段判完才退到人话文本（crypto 侧那条留痕只有 output_text）。
    4. 「币安市价单零成交」既没成交也没挂住 → `unknown`，**别硬塞进前三类说瞎话**。
    """
    if d.get("executed"):
        return "filled"
    s = _summary_dict(d)
    if s.get("state_unknown"):
        return "unknown"
    # 补录券商 App 的真实成交（record_manual_trade）：回执里没有 `executed` 键，
    # confirm_gate 于是写成 executed=False —— 但它按定义就是**已经成交**的既成事实。
    if s.get("recorded"):
        return "filled"
    if s.get("resting"):
        # ⚠️ 市价单零成交时写入侧也会给 `resting: True`（`crypto_tools.py` 那处对
        # LIMIT/MARKET 不分家），它的形状是 `"price": round(price,4) if price else None`
        # —— 所以判据是**键在但值为 None**（= 明确没有限价），不是「没有 price 键」。
        # 用后者会把将来别的写入点那种精简回执误判成 unknown。
        if "price" in s and s["price"] is None and "限价" not in (d.get("output_text") or ""):
            return "unknown"
        return "resting"
    if s.get("reason"):
        return "blocked"
    if d.get("risk_passed") is False:
        return "blocked"
    text = d.get("output_text") or ""
    if "挂出" in text:
        return "resting"
    if "成交" in text and "未成交" not in text and "零成交" not in text:
        return "filled"
    return "unknown"


def _derive_order_id(d: dict[str, Any]) -> Optional[str]:
    """交易所订单号 —— **去重的唯一依据**。

    ✅ S4 已收口：同一张单现在只留一条痕（走确认门的路径由 `confirm_gate` 记，
    执行层传 `log_decision=False`）。`distinct_orders` 因此在新数据上恒等于行数 ——
    **但去重逻辑要留着**：库里还有 S4 之前的历史行是成对的，删了它们会被重复计数。
    """
    s = _summary_dict(d)
    oid = s.get("order_id")
    if oid:
        return str(oid)
    m = _ORDER_ID_RE.search(d.get("output_text") or "")
    return m.group(1) if m else None


def _kinds_hint(source: str) -> str:
    """这个 source 底下实际有哪些 entry_kind —— 指路文案用。

    返回**人话字符串**不返回 list：直接把 list 拼进 message 会让 LLM 读到
    `entry_kind=['execution', 'ops']` 这种 Python repr，它照着传就是非法值。
    """
    kinds = sorted(kinds_of(source))
    return " 或 ".join(f"entry_kind={k}" for k in kinds) if kinds else "entry_kind=all"


# 订单回执的全量上限。60 行的表远够用；写成常量是为了「真到了上限」时能显式说出来，
# 而不是悄悄少算几张单。
_EXEC_OVERVIEW_MAX = 2000


def _exec_overview(rows: list[dict[str, Any]], total: int) -> Optional[dict[str, Any]]:
    """「到底发生了几件事」的摘要。

    ⚠️ **必须喂全量行，不能喂被 `limit` 截断的样本。** 否则 `distinct_orders` 会
    随 `limit` 变（实测 limit=5→2、10→4、50→5，而真相是 5），旁边还摆着一个
    全量的 `total: 16` —— 这正是 `get_decision_stats` 的 docstring 明令禁止的那种
    「随 limit 变的数字」，比没有还糟。
    """
    if not rows:
        return None
    counts: dict[str, int] = {}
    order_ids: set[str] = set()
    for d in rows:
        st = _derive_exec_state(d)
        counts[st] = counts.get(st, 0) + 1
        oid = _derive_order_id(d)
        if oid:
            order_ids.add(oid)
    ov: dict[str, Any] = {
        "scope": "all_matching",          # 明说：不是样本口径
        "counts": counts,
        "distinct_orders": len(order_ids),
        "note": (
            "**distinct_orders 才是真实单数**（S4 之前的历史行同一张单有两条留痕，"
            "S4 收口后新数据一单一行）；blocked = 根本没到交易所（钱没动）；"
            "unknown = 状态不明，**可能已成交**，得去交易所核对。"
        ),
    }
    if total > len(rows):
        ov["truncated_at"] = len(rows)
        ov["note"] += f" ⚠️ 回执超过 {len(rows)} 条，本摘要只统计了最近这些。"
    return ov


@tool(
    name="get_decision_history",
    description=(
        "查历史 AI 决策记录 + **历史胜率**（按来源分组）。可按股票、来源、动作、日期、"
        "留痕类型、评估状态过滤。回答「上次为什么建议我买XX / 之前推荐过什么 / "
        "**你的推荐历史胜率是多少 / 你说的准不准** / **我下过哪些单**」。"
        f"来源: {describe_for_prompt()}。"
        "entry_kind 默认只看 AI 建议（advice）；问「下过哪些单」要传 execution，"
        "那时每条会带 exec_state（filled=成交 / resting=挂在盘口 / blocked=没到交易所）。"
        "返回的 stats 是**全量**胜率（不受 limit 影响）**且恒定只算 advice**；"
        "decisions 只是最近几条样本。"
        "注意 win_rate=null 表示「一条都没法评」而不是「胜率 0%」，"
        "看 unable_breakdown 说明为什么评不了。"
        "calibration=置信度校准（**你说高置信度时实际准多少**，按分桶）+ 反哺因子；"
        "样本<30 时 calibration_factor=1.0（不校准）。"
    ),
    args_model=GetDecisionHistoryArgs,
    category="review",
    group="review",
)
def get_decision_history(symbol: Optional[str] = None, source: Optional[str] = None,
                         action: Optional[str] = None, entry_kind: str = "advice",
                         start_date: Optional[str] = None,
                         end_date: Optional[str] = None, outcome_status: Optional[str] = None,
                         horizon: int = 20, limit: int = 10,
                         verbose: bool = False) -> ToolEnvelope:
    import decision_log

    limit = max(1, min(int(limit or 10), 50))
    kind = (entry_kind or ADVICE).strip().lower()
    if kind not in ("advice", "execution", "ops", "all"):
        kind = ADVICE
    verbose_suppressed = bool(verbose) and limit > _VERBOSE_MAX_LIMIT
    verbose = bool(verbose) and not verbose_suppressed

    r = decision_log.query_decisions(
        symbol=symbol, source=source, action=action,
        start_date=start_date, end_date=end_date,
        outcome_status=outcome_status,
        entry_kind=None if kind == "all" else kind,
        limit=limit,
    )
    # 胜率按**过滤条件全量**算，**不传 limit** —— limit 只管返回几条样本给 LLM 看。
    # 混淆会让「MoneyBill 推荐胜率」变成「最近 10 条的胜率」且随 limit 变，比没有还糟。
    #
    # ⭐ 这里**不透传 entry_kind**：胜率的定义就是「AI 建议准不准」。让 LLM 能算出
    # 「订单回执的胜率」，等于把 P0-4 批次1 刚焊死的口径又开一条缝。
    stats = decision_log.get_decision_stats(
        symbol=symbol, source=source,
        start_date=start_date, end_date=end_date, horizon=horizon,
    )

    # P0-3 置信度校准：「你说高置信度时实际准多少」。校准是 per-source 的（cockpit 的
    # composite 才是结构化置信度），不指定 source 时默认看 cockpit。
    calibration = decision_log.compute_calibration(source=source or "cockpit", horizon=horizon)
    scope = {"stats_scope": ADVICE, "queried_entry_kind": kind}

    if not r.get("total"):
        # 注意这里**仍然带上 stats**：「有 12 条建议但全都没法评」是个**有内容的**
        # negative —— 让 LLM 能说「advisor 那 12 条都没记方向和入场价，评不了」，
        # 而不是干巴巴一句「没有记录」。
        msg = "没有符合条件的决策记录。"
        # §2.3 的静默陷阱：传 source="crypto"（那是订单回执）+ 默认 entry_kind=advice
        # → 0 条 + 空 stats → LLM 回答「没有 crypto 记录」，而真正的 crypto 建议
        # 在 crypto_cockpit 里躺着。**静默的空结果比报错糟**，所以要指路。
        # ⚠️ `is_known` 这层不能省：没登记的 source 会让 `has_advice` 返 False，
        # 于是下面那句「里面只有既成事实」变成**凭空捏造**（LLM 走不到这条路——
        # executor 会用 Literal 拦掉——但直接调 Python 走得到）。
        if source and kind == ADVICE and is_known(source) and not has_advice(source):
            alt = advice_alternative(source)
            msg = (f"`{source}` 是「{label_of(source)}」，里面只有既成事实、"
                   f"没有可评胜率的 AI 建议。")
            if alt:
                msg += f"你要问的胜率应该查 `{alt}`（{label_of(alt)}）。"
            msg += f"想看这个来源本身的记录，请传 entry_kind=all 或 {_kinds_hint(source)}。"
        return ToolEnvelope(
            business_result="negative",
            message=msg,
            data={"stats": stats, "calibration": calibration, **scope},
        )

    # 精简回灌 LLM：默认去掉大字段（input_snapshot / output_summary / output_text 全文）。
    # ⚠️ 旧注释写的是「完整快照留在 /decisions 页面看」—— **那个页面不存在**（`api/routes/`
    # 里没有任何 route 暴露 DecisionLog，本工具是唯一出口）。所以补了 verbose。
    decisions = []
    for d in r.get("decisions", []):
        item = {
            "created_at": d.get("created_at"),
            "source": d.get("source"),
            "entry_kind": d.get("entry_kind"),
            "symbol": d.get("symbol"),
            "name": d.get("name"),
            "action": d.get("action"),
            "recommendation": d.get("recommendation"),
            "confidence": d.get("confidence"),
            "entry_price": d.get("entry_price"),
            "stop_loss": d.get("stop_loss"),
            "reasons": d.get("reasons"),
            "executed": d.get("executed"),
            "model_id": d.get("model_id"),
            "prompt_version": d.get("prompt_version"),
            # 后验：后来对了吗
            "outcome_status": d.get("outcome_status"),
            "unable_reason": d.get("unable_reason"),
            "outcome_5d": d.get("outcome_5d"),
            "outcome_20d": d.get("outcome_20d"),
            "return_5d": d.get("return_5d"),
            "return_20d": d.get("return_20d"),
            "first_hit": d.get("first_hit"),
            "first_hit_days": d.get("first_hit_days"),
        }
        if d.get("entry_kind") == "execution":
            item["exec_state"] = _derive_exec_state(d)
            item["order_id"] = _derive_order_id(d)
        if verbose:
            item.update({
                "decision_id": d.get("decision_id"),
                "input_snapshot": d.get("input_snapshot"),
                "output_summary": d.get("output_summary"),
                "output_text": d.get("output_text"),
                "risk_passed": d.get("risk_passed"),
                "position_pct": d.get("position_pct"),
                "take_profit": d.get("take_profit"),
            })
        decisions.append(item)

    data: dict[str, Any] = {"total": r["total"], "returned": len(decisions),
                            "stats": stats, "calibration": calibration,
                            **scope, "decisions": decisions}
    # 回执摘要走**独立的全量查询**，不复用上面被 limit 截断的样本 ——
    # 否则 `distinct_orders` 会随 limit 变（实测 5→2 / 10→4 / 50→5，真相是 5），
    # 旁边却摆着一个全量的 total，比不给还糟。多一次查询换一个不会撒谎的数字，值。
    if kind in ("execution", "all"):
        ex = decision_log.query_decisions(
            symbol=symbol, source=source, action=action,
            start_date=start_date, end_date=end_date,
            outcome_status=outcome_status, entry_kind="execution",
            limit=_EXEC_OVERVIEW_MAX,
        )
        overview = _exec_overview(ex.get("decisions", []), ex.get("total", 0))
        if overview:
            data["exec_overview"] = overview
    if verbose:
        data["verbose"] = True
    if verbose_suppressed:
        # 静默失效比报错糟（TurnMonitor 那条教训）—— 明说它没生效、以及怎么才能生效。
        data["verbose_suppressed"] = (
            f"verbose 未生效：完整快照只在 limit<={_VERBOSE_MAX_LIMIT} 时返回"
            f"（当前 limit={limit}），否则回灌 token 会炸。要看快照请把 limit 调小。"
        )
    return ToolEnvelope(data=data)

"""crypto 自主策略 —— MoneyBill「把人话编译成策略」的工具层（需求3）。

`compile_crypto_strategy`：MoneyBill 把 Jason 的自然语言规则映射成结构化 `CryptoStrategySpec`
（DSL），本工具**确定性地**校验 → 净费回测 → 落库。LLM 只当编译器，不进每一单的执行回路。

半自动闭环（Jason 2026-07-21 拍板：全自动不信任，先做半自动验证）：策略 arm 到 live 后，
引擎**自动产决策 + 自动排一张待确认单**推给 Jason，**每笔仍由 Jason 点确认才成交**——
CLAUDE.md 逐笔确认红线原样保留、不动。

⛔ 本工具标 `requires_confirmation=True`：创建一条自主策略是高风险动作，Jason 先审 DSL（预览
把条件树+成本闸用人话回给他）再落库。落库默认 `paper` 未启用（干跑观察）；上实盘（排真单等
确认）要显式 arm。

⚠️ **arm 不卡回测，这是刻意设计**（见 `crypto_strategy/service.py::arm`）：半自动系统的安全
边界是**逐笔人工确认 + 护栏 + 硬风控**，不是回测。那个回测是双均线代理、并不测你写的 DSL
规则，把它当准入门槛只会给人虚假的安心。烂策略只会提烂建议、被 Jason 拒掉，赔不了钱。
（此前本文件与 route 都写着「必须先过回测」，与代码不符，已更正。）
"""
from typing import Any

from loguru import logger
from pydantic import BaseModel, Field, ValidationError

from agents.registry import tool
from agents.tool_envelope import ToolEnvelope
from crypto_intel_engine.dsl import CryptoStrategySpec, round_trip_cost


class CompileCryptoStrategyArgs(BaseModel):
    spec: CryptoStrategySpec = Field(..., description="结构化策略规格（DSL）——由你把 Jason 人话映射填好")
    description_nl: str | None = Field(None, description="Jason 的原始人话规则（原样留存做审计）")


def _coerce_spec(raw: Any) -> CryptoStrategySpec:
    if isinstance(raw, CryptoStrategySpec):
        return raw
    return CryptoStrategySpec.model_validate(raw)


def _dsl_plain(spec: CryptoStrategySpec) -> dict:
    """把 DSL 用人话摘要（预览 + 成交后 widget 共用）。"""
    def _conds(group) -> list[str]:
        out = [f"{c.field} {c.op} {c.value}" for c in group.all_of]
        if group.any_of:
            out.append("（任一）" + " / ".join(f"{c.field} {c.op} {c.value}" for c in group.any_of))
        return out

    cm = spec.cost_model
    rt = round_trip_cost(cm)
    return {
        "name": spec.name, "kind": spec.strategy_kind, "mode": spec.mode,
        "interval_minutes": spec.interval_minutes,
        "universe": spec.universe.symbols,
        "entry": _conds(spec.entry_rules.when),
        "exit": _conds(spec.exit_rules.when),
        "position": {"source": spec.position_policy.target_pct_source,
                     "per_symbol_cap_pct": spec.position_policy.per_symbol_exposure_cap_pct},
        "cost": {"round_trip_pct": round(rt, 6), "min_net_edge_pct": cm.min_net_edge_pct,
                 "edge_clears_cost": cm.min_net_edge_pct > rt},
        "guardrails": {"per_order_usdt": spec.guardrails.per_order_notional_usdt,
                       "max_orders_per_day": spec.guardrails.max_orders_per_day,
                       "max_fees_per_day_usdt": spec.guardrails.max_fees_per_day_usdt,
                       "daily_loss_pct": spec.guardrails.daily_loss_pct},
    }


def preview_compile_crypto_strategy(args: dict) -> dict:
    """确认前把解析后的 DSL 回给 Jason（不落库、不回测，纯展示）。"""
    try:
        spec = _coerce_spec(args.get("spec"))
    except ValidationError as e:
        return {"error": "策略规格校验失败", "details": [str(err) for err in e.errors()]}
    out = _dsl_plain(spec)
    out["note"] = ("确认后将：① 校验 ② 跑一次参考回测（双均线代理，不测你的规则）③ 落库为 "
                   "paper 未启用。上实盘（arm 到 live）后=引擎自动产决策+排队待确认，"
                   "每笔仍由你点确认才成交。arm 不以回测为门槛——安全靠逐笔确认+护栏+硬风控。")
    return out


@tool(
    name="compile_crypto_strategy",
    description="【crypto 半自动策略·编译】把 Jason 用自然语言描述的加密交易规则编译成一条结构化策略，"
               "校验→参考回测→落库。用户说「以后按这个规则半自动交易/帮我做一个自动策略」时用。"
               "半自动=引擎自动产决策并排队待确认，**每笔仍由 Jason 点确认才成交**（不自动成交）。"
               "你负责把人话映射成 spec（DSL）字段；工具确定性地校验+回测+持久化。"
               "落库默认纸面未启用，上实盘要另外 arm（arm 不以回测为门槛）。会先让 Jason 确认 DSL。",
    args_model=CompileCryptoStrategyArgs,
    category="crypto", group="crypto",
    requires_confirmation=True, preview_fn=preview_compile_crypto_strategy,
)
def compile_crypto_strategy(spec: Any, description_nl: str | None = None) -> ToolEnvelope:
    from agents.widgets import metric_cards_widget
    from crypto_strategy.service import crypto_strategy_service

    try:
        parsed = _coerce_spec(spec)
    except ValidationError as e:
        return ToolEnvelope(business_result="negative",
                            message="策略规格校验失败：" + "；".join(str(err) for err in e.errors()))

    try:
        result = crypto_strategy_service.compile_and_persist(parsed, description_nl=description_nl)
    except Exception as e:  # noqa: BLE001
        logger.warning(f"编译策略失败: {e}")
        return ToolEnvelope(business_result="negative", message=f"编译/回测失败：{e}")

    bt = result.get("backtest") or {}
    net = bt.get("net_return")
    cm = parsed.cost_model
    rt = round_trip_cost(cm)
    net_label = f"{net:+.2%}" if isinstance(net, (int, float)) else "N/A"
    widget = metric_cards_widget([
        {"label": "策略", "value": parsed.name, "type": "neutral"},
        {"label": "参考回测", "value": net_label, "type": "neutral"},
        {"label": "往返成本", "value": f"{rt:.2%}", "type": "neutral"},
        # 契约是 type ∈ {return,risk,quality,neutral} + 独立 positive；
        # 传 "positive" 前端会 fallthrough 到 neutral（等于没写）。
        {"label": "下一步", "value": "可纸面/可上实盘", "type": "quality", "positive": True},
    ], title=f"🤖 已编译 · {parsed.name}（{parsed.strategy_kind}）")

    plain = _dsl_plain(parsed)
    plain.update({
        "strategy_id": result.get("strategy_id"),
        "backtest_net_return_ref": net,   # ⚠️ 参考值：双均线代理，不代表你的 DSL 规则
        "degraded": bt.get("degraded"), "degraded_reasons": bt.get("degraded_reasons"),
        # 「数字本身可信度」的警告（费率口径 / 回放覆盖度），与 degraded 正交
        "caveats": bt.get("caveats"),
        "next_steps": (
            "已落库。可先 enable 纸面干跑观察它「本该下什么单」，或直接 arm 上实盘"
            "（引擎按你的规则排单、你逐笔确认成交）。"
            "⚠️那个回测只是双均线代理、不测你的规则，仅供参考——安全靠逐笔确认+护栏+硬风控，不靠它。"),
    })
    return ToolEnvelope(data=plain, widget=widget)


# ──────────────────── 回头看：策略战绩（S1）────────────────────
#
# `crypto_strategy_runs` 从 2026-07 就在逐 tick 落审计日志，**在此之前没有任何工具读过它**。
# 这两个工具给 AI 装上「回头看我这条策略跑得怎么样」的眼睛，是策略竞技场（S3）里
# 「卫冕者是不是开始失效」的数据入口。

class StrategyPerformanceArgs(BaseModel):
    strategy_id: str = Field(..., min_length=3, description="策略号，形如 CS-20260721191526-17cb24")
    days: int = Field(30, ge=1, le=365, description="回看多少天，默认 30")


@tool(
    name="get_strategy_performance",
    description="【crypto 半自动策略·体检】单条策略的完整战绩：跑了多少 tick、"
                "**为什么没开单**（成本吃光边际/条件没触发/风控护栏拦下，按占比排序）、"
                "赚了多少、护栏状态，并给一句人话结论。"
                "用户问「我那条策略最近怎么样 / 它为什么不下单 / 它赚钱了吗」时用。"
                "⚠️ 返回的 pnl.basis 必须跟着数字一起读：live_fills=真金白银；"
                "paper_simulated=理想撮合的模拟账，**不可与实盘策略比大小**；none=还算不出来。",
    args_model=StrategyPerformanceArgs,
    category="crypto", group="crypto",
)
def get_strategy_performance(strategy_id: str, days: int = 30) -> ToolEnvelope:
    from crypto_strategy.performance import strategy_health

    h = strategy_health(strategy_id, days=days)
    if not h.get("ok"):
        # 「没跑过」和「跑了但没赚」给出的下一步动作完全不同，所以理由要原样带出去，
        # 不能压成一句「没有数据」。
        return ToolEnvelope(business_result="negative", message=h.get("reason", "查不到"),
                            data=h)
    return ToolEnvelope(data=h, widget=_health_widget(h))


def _health_widget(h: dict) -> Any:
    from agents.widgets import metric_cards_widget

    pnl = h.get("pnl") or {}
    basis = pnl.get("basis")
    # ⚠️ 卡片契约是 `type ∈ {'return','risk','quality','neutral'}` + 独立的 `positive: bool`
    # （`agents/widgets.py`）。传 `type="positive"` 前端会 fallthrough 到 neutral ——
    # **盈亏卡永远白色，赚亏不变色**。全仓 20+ 处无一例外，别在这儿开先例。
    realized = pnl.get("realized_pnl")
    if basis == "mixed":
        realized = (pnl.get("live") or {}).get("realized_pnl")
    if basis in ("live_fills", "mixed"):
        pnl_card = {"label": "实盘盈亏", "value": f"{realized or 0:+.2f} USDT",
                    "type": "return", "positive": (realized or 0) >= 0}
    elif basis == "paper_simulated":
        pnl_card = {"label": "盈亏（模拟）", "value": f"{realized or 0:+.2f}",
                    "type": "neutral"}
    else:
        pnl_card = {"label": "盈亏", "value": "暂无", "type": "neutral"}
    top = next(iter(h.get("no_order_reasons") or {}), None)
    return metric_cards_widget([
        {"label": "运行", "value": f"{h['runs']['total']} 次", "type": "neutral"},
        {"label": "待确认单", "value": f"{h.get('orders_staged', 0)} 张", "type": "neutral"},
        {"label": "没开单主因",
         "value": (h["no_order_reasons"][top]["means"] if top else "—"), "type": "neutral"},
        pnl_card,
    ], title=f"📊 {h.get('name')} · 最近 {h['window']['days']} 天")


class StrategyStandingsArgs(BaseModel):
    days: int = Field(30, ge=1, le=365, description="回看多少天，默认 30")
    include_archived: bool = Field(
        False, description="是否带上已退役/被新版本取代的历史版本，默认不带")


@tool(
    name="list_strategy_standings",
    description="【crypto 半自动策略·总览】列出全部策略及各自战绩摘要（运行次数/产单数/盈亏/一句话结论）。"
                "用户问「我现在有哪些策略 / 它们都怎么样」时用。"
                "⚠️ **刻意不排名**：paper 与 live 不可比、样本量差异大，按收益排序等于诱导追涨杀跌。"
                "真正的优劣判定要走观察期/显著性/回测/风险四道门槛（还没实现）。",
    args_model=StrategyStandingsArgs,
    category="crypto", group="crypto",
)
def list_strategy_standings(days: int = 30, include_archived: bool = False) -> ToolEnvelope:
    from crypto_strategy.performance import standings

    rows = standings(days=days, include_archived=include_archived)
    if not rows:
        msg = ("一条 crypto 策略都还没建。用 compile_crypto_strategy 编一条。"
               if include_archived else
               "没有在用的 crypto 策略（历史版本没算进来，要看传 include_archived=true）。")
        return ToolEnvelope(business_result="negative", message=msg)
    return ToolEnvelope(data={"count": len(rows), "days": days, "strategies": rows,
                              "include_archived": include_archived,
                              "ranking_note": "未排名——比较规则见工具说明。"})


# ──────────────────── 提议改：AI 提案 → Jason 审 → 新版本（S2）────────────────────
#
# `compile_crypto_strategy` 只能**从零编一条**，改不了现有的。这三个工具把
# 「AI 提议怎么改 → Jason 审 → 落成新版本 → 上线」补齐。
#
# ⭐ 确认门只挂在后两个：提案不动策略也不动钱，只是一段有结构的话，
# 给它挂确认门等于让 Jason 为「AI 想说句话」点两次（S2 §2.2）。

class ProposeStrategyChangeArgs(BaseModel):
    base_strategy_id: str = Field(..., min_length=3, description="要改哪个版本，形如 CS-…")
    shortfall: str = Field(..., min_length=4, description="目前策略的不足（具体，别写「表现一般」）")
    change_summary: str = Field(..., min_length=4, description="想怎么调整（人话一句）")
    rationale: str = Field(..., min_length=4, description="为什么这么改")
    expected_return_pct: float = Field(
        ..., description="预期收益（小数，0.08=+8%）。**这是可证伪断言，会被真实盈亏打分**")
    expected_win_rate: float = Field(
        ..., ge=0, le=1, description="预期胜率 0-1。**同样会被打分**")
    horizon_days: int = Field(..., ge=1, le=365, description="多少天内兑现上面两个数")
    new_spec: Any = Field(..., description="完整的新策略 DSL（在原 spec 基础上改，别只给改动部分）")


@tool(
    name="propose_strategy_change",
    description="【crypto 半自动策略·提议改】给现有策略提一份结构化变更提案："
                "目前的不足 / 想怎么调整 / 为什么 / **预期收益** / **预期胜率**。"
                "用户说「这条策略最近不行，帮我改改 / 你觉得该怎么调」时用。"
                "⚠️ 先用 get_strategy_performance 看战绩再提，别拍脑袋。"
                "⚠️ new_spec 要给**完整的新 DSL**（在原 spec 上改），不是只给改动部分；"
                "改了哪些字段由系统确定性算出来，你不用也别去描述具体字段值。"
                "⭐ 预期收益/胜率是**可证伪断言**，将来会拿真实盈亏给你打分——认真给，别写保险数字。"
                "本工具只落提案、不改任何东西；要真的生成新版本得 Jason 批准。",
    args_model=ProposeStrategyChangeArgs,
    category="crypto", group="crypto",
)
def propose_strategy_change(base_strategy_id: str, shortfall: str, change_summary: str,
                            rationale: str, expected_return_pct: float,
                            expected_win_rate: float, horizon_days: int,
                            new_spec: Any) -> ToolEnvelope:
    from crypto_strategy import proposals as pr
    from crypto_strategy.service import StrategyError, crypto_strategy_service

    try:
        base = crypto_strategy_service.get_strategy(base_strategy_id)
    except StrategyError as e:
        return ToolEnvelope(business_result="negative", message=str(e))
    try:
        parsed = _coerce_spec(new_spec)
    except ValidationError as e:
        return ToolEnvelope(business_result="negative",
                            message=f"新 DSL 校验失败（{e.error_count()} 处）：{e.errors()[:3]}")

    diff = pr.diff_specs(base.get("spec") or {}, parsed.model_dump())
    if not diff:
        # 空提案比错提案更浪费 Jason 的时间：他会打开、读完、发现什么都没改。
        return ToolEnvelope(business_result="negative",
                            message="新 spec 与当前版本完全相同，这份提案没有实际改动，没落库。")

    saved = pr.create_proposal(
        base_strategy_id=base_strategy_id, family_id=base.get("family_id"),
        shortfall=shortfall, change_summary=change_summary, rationale=rationale,
        expected_return_pct=expected_return_pct, expected_win_rate=expected_win_rate,
        horizon_days=horizon_days, new_spec=parsed.model_dump(), diff=diff)
    saved["diff_text"] = pr.diff_text(diff)
    saved["next_steps"] = (f"提案已存（{saved['proposal_id']}）。要真的生成新版本，"
                           f"用 apply_strategy_proposal —— 那一步 Jason 会先审。")
    return ToolEnvelope(data=saved)


def _decided_msg(pid: str, status: str) -> str:
    """已定案的提案为什么用不了 —— 用人话说，别把内部状态名甩给 LLM/Jason。"""
    if status == "superseded_by_newer":
        return (f"提案 {pid} 已过期作废：同族有别的提案先被批准了，它的基准版本不再是最新的。"
                f"应用它会把中间批准过的改动静默还原回去。请基于最新版重新提一份。")
    if status == "applied":
        return f"提案 {pid} 已经应用过了，不能重复应用（重复应用会生成第二个版本）。"
    if status == "rejected":
        return f"提案 {pid} 已被拒绝。"
    return f"提案 {pid} 已经是 {status}，不能再应用。"


def _proposal_preview(args: dict) -> dict:
    """确认前预览：**优先摆 diff，不是 AI 的说法**。

    AI 写的 `change_summary` 只是它对自己改动的描述，可能说漏说错；
    `diff` 是逐字段比出来的事实。Jason 要审的是后者（S2 §2.1）。
    """
    from crypto_strategy import proposals as pr
    pid = (args.get("proposal_id") or "").strip()
    p = pr.get_proposal(pid)
    if p is None:
        return {"error": f"找不到提案 {pid}"}
    if p["status"] != "proposed":
        return {"error": _decided_msg(pid, p["status"])}
    st = pr.staleness(p)
    if st.get("stale"):
        # ⛔ 不给「确认后果自负」的选项：这份提案的 new_spec 是对着旧版本写的，
        # 应用它会把之后批准过的改动**静默回滚**。让 AI 基于最新版重提一份。
        return {"error": (
            f"提案 {pid} 已过期：它基于 v{st.get('base_version')}，"
            f"而这条策略现在已经到 v{st.get('latest_version')}"
            f"（{st.get('latest_strategy_id')}）。"
            f"直接应用会把中间批准过的改动悄悄还原回去。"
            f"请让 AI 基于最新版重新提一份。")}
    return {
        "proposal_id": pid,
        "基于版本": f"{p['base_strategy_id']}（v{st.get('base_version')}，当前最新）",
        "实际改动（系统逐字段比对）": pr.diff_text(p["diff"]),
        "AI 说的不足": p["shortfall"],
        "AI 说的调整": p["change_summary"],
        "AI 给的理由": p["rationale"],
        "AI 的可证伪断言": (f"{p['horizon_days']} 天内预期收益 "
                            f"{p['expected_return_pct']:+.2%}、胜率 "
                            f"{p['expected_win_rate']:.0%}"),
        "note": ("批准 = 生成一个**新版本**（草稿，不启用），旧版本原样继续跑。"
                 "要让新版本上线，之后还要单独 arm 一次。"),
    }


class ApplyProposalArgs(BaseModel):
    proposal_id: str = Field(..., min_length=3, description="提案号，形如 PS-…")


@tool(
    name="apply_strategy_proposal",
    description="【crypto 半自动策略·应用提案】把一份已提交的变更提案落成**新版本**（草稿，不启用）。"
                "旧版本原样继续跑；要让新版本上线，之后还要单独 arm。会先让 Jason 审改动。",
    args_model=ApplyProposalArgs,
    category="crypto", group="crypto",
    requires_confirmation=True, preview_fn=_proposal_preview,
)
def apply_strategy_proposal(proposal_id: str) -> ToolEnvelope:
    from crypto_strategy import proposals as pr
    from crypto_strategy.service import StrategyError, crypto_strategy_service

    p = pr.get_proposal(proposal_id)
    if p is None:
        return ToolEnvelope(business_result="negative", message=f"找不到提案 {proposal_id}")
    if p["status"] != "proposed":
        return ToolEnvelope(business_result="negative",
                            message=_decided_msg(proposal_id, p["status"]))
    # 🔴 过期检查在这儿**也要有一份**：preview 只在走确认门时跑，直接调函数绕得过去。
    st = pr.staleness(p)
    if st.get("stale"):
        pr.mark_decided(proposal_id, status="superseded_by_newer",
                        note=f"基准 v{st.get('base_version')} 已被 v{st.get('latest_version')} 取代")
        return ToolEnvelope(
            business_result="negative",
            message=(f"提案 {proposal_id} 已过期：基于 v{st.get('base_version')}，"
                     f"而策略已到 v{st.get('latest_version')}。应用它会把中间批准过的改动"
                     f"静默还原。请基于 {st.get('latest_strategy_id')} 重新提一份。"))
    try:
        parsed = _coerce_spec(p["new_spec"])
        res = crypto_strategy_service.fork_version(p["base_strategy_id"], parsed)
    except (StrategyError, ValidationError) as e:
        return ToolEnvelope(business_result="negative", message=f"生成新版本失败：{e}")

    # ⚠️ 先建版本再定案：反过来的话，fork 失败会留下一条「已应用但没有新版本」的提案。
    # `mark_decided` 只认 `proposed`，所以重复点确认不会生成第二个版本。
    decided = pr.mark_decided(proposal_id, status="applied",
                              applied_strategy_id=res["strategy_id"])
    # 同族其它还挂着的提案 base 已经过期了 —— 在列表里就标出来，
    # 别让 Jason 点进去才发现（`superseded_by_newer` 这个状态就是为它准备的）。
    stale_now = pr.supersede_siblings(res.get("family_id"), proposal_id)
    out = {
        "proposal": decided or p, **res,
        "next_steps": (f"已生成 v{res['version']}（{res['strategy_id']}，草稿未启用）。"
                       f"旧版本 {p['base_strategy_id']} 仍在原样运行。"
                       f"确认要换上去，用 arm_crypto_strategy。"),
    }
    if stale_now:
        out["superseded_proposals"] = stale_now
        out["next_steps"] += (f" ⚠️ 同族另外 {len(stale_now)} 份提案的基准版本已过期"
                              f"（已标记），要用得基于新版本重提。")
    return ToolEnvelope(data=out)


def _arm_preview(args: dict) -> dict:
    """确认前预览：把「要上线的这版和现在跑的那版差在哪」摆出来。

    ⛔ 这不是可有可无的装饰：arm 一个新版本会让**旧版本立刻停跑**，
    Jason 得知道自己换掉的是什么。
    """
    from crypto_strategy import proposals as pr
    from crypto_strategy.service import StrategyError, crypto_strategy_service

    sid = (args.get("strategy_id") or "").strip()
    try:
        target = crypto_strategy_service.get_strategy(sid)
    except StrategyError as e:
        return {"error": str(e)}
    family = target.get("family_id")
    current = None
    for s in crypto_strategy_service.list_family(family):
        if s["strategy_id"] != sid and s["status"] in ("armed", "paused_by_guardrail"):
            current = s
    out: dict = {
        "strategy_id": sid, "name": target.get("name"),
        "version": f"v{target.get('version')}",
        "note": "arm = 开始按这条 DSL 排真单（每笔仍由你逐笔确认才成交）。",
    }
    if current:
        try:
            cur_spec = crypto_strategy_service.get_strategy(current["strategy_id"]).get("spec")
            out["将替换掉"] = f"{current['strategy_id']} v{current['version']}（会立刻停跑）"
            out["两版差异"] = pr.diff_text(pr.diff_specs(cur_spec or {},
                                                         target.get("spec") or {}))
        except StrategyError:
            pass
    else:
        out["将替换掉"] = "（同族当前没有在跑的版本）"
    return out


class ArmStrategyArgs(BaseModel):
    strategy_id: str = Field(..., min_length=3, description="要上线的策略版本，形如 CS-…")


@tool(
    name="arm_crypto_strategy",
    description="【crypto 半自动策略·上线】把某个策略版本武装到 live：引擎开始按它的 DSL 排"
                "**待确认单**（每笔仍由 Jason 逐笔确认才成交，引擎永不自动成交）。"
                "同一条策略的旧版本会自动停跑。会先让 Jason 审两版差异。",
    args_model=ArmStrategyArgs,
    category="crypto", group="crypto",
    requires_confirmation=True, preview_fn=_arm_preview,
)
def arm_crypto_strategy(strategy_id: str) -> ToolEnvelope:
    from crypto_strategy.service import StrategyError, crypto_strategy_service
    try:
        res = crypto_strategy_service.arm(strategy_id)
    except StrategyError as e:
        return ToolEnvelope(business_result="negative", message=str(e))
    msg = f"{res['name']} v{res['version']} 已上线（live）。"
    if res.get("superseded"):
        msg += f"同族旧版本 {'、'.join(res['superseded'])} 已停跑。"
    return ToolEnvelope(data=res, message=msg)


class ListProposalsArgs(BaseModel):
    family_id: str | None = Field(None, description="可选：只看某条策略族的提案")
    status: str | None = Field(None, description="可选：proposed / applied / rejected")
    limit: int = Field(10, ge=1, le=50, description="最多返回几条")


@tool(
    name="list_strategy_proposals",
    description="【crypto 半自动策略·提案列表】列出策略变更提案（含每条的预期收益/预期胜率断言"
                "和实际改了哪些字段）。用户问「有哪些待审的提案 / 你之前提过什么改法」时用。",
    args_model=ListProposalsArgs,
    category="crypto", group="crypto",
)
def list_strategy_proposals(family_id: str | None = None, status: str | None = None,
                            limit: int = 10) -> ToolEnvelope:
    from crypto_strategy import proposals as pr
    rows = pr.list_proposals(family_id=family_id, status=status, limit=limit)
    if not rows:
        return ToolEnvelope(business_result="negative", message="没有符合条件的策略变更提案。")
    for r in rows:
        r["diff_text"] = pr.diff_text(r.get("diff") or [])
    return ToolEnvelope(data={"count": len(rows), "proposals": rows})


# ──────────────────── 竞技场：该不该换策略（S3）────────────────────
#
# ⛔ 裁决 10：**规则判「切不切」，AI 判「为什么」和「下一条试什么」。**
# 本工具返回的是规则的判定；AI 的位置是在结论旁边说话（解释失效原因、造新挑战者、
# 对规则提异议），**不是去改判定结果**。

class ArenaArgs(BaseModel):
    days: int = Field(90, ge=7, le=365, description="用多长的历史算日收益率序列，默认 90 天")


@tool(
    name="evaluate_strategy_arena",
    description="【crypto 半自动策略·竞技场】按四道硬门槛判定「现在该不该换掉正在跑的策略」："
                "①观察期够长 ②优势显著（按日收益率序列算标准误，**挑战者越多门槛越高**）"
                "③回测过闸 ④风险不劣化，外加冷却期。同时给出「卫冕者是不是正在变弱」。"
                "用户问「该换策略了吗 / 哪条更好 / 我这条是不是不行了」时用。"
                "⚠️ 这是**规则的判定，不是你的判定**——你的活是解释卫冕者为什么开始失效、"
                "造新的挑战者、以及**对规则提出异议**（比如「数字上该切了，但这是财报季噪声，"
                "建议再观察两周」）。⛔ 别推翻 should_switch，要有异议就在旁边说清楚理由。"
                "⚠️ 本工具不写库不改状态：真要换仍要走 arm_crypto_strategy 的确认门。",
    args_model=ArenaArgs,
    category="crypto", group="crypto",
)
def evaluate_strategy_arena(days: int = 90) -> ToolEnvelope:
    from crypto_strategy.arena import evaluate_arena

    r = evaluate_arena(days=days)
    if r.get("champion") is None:
        return ToolEnvelope(business_result="negative", message=r["verdict"], data=r)
    return ToolEnvelope(data=r, message=r["verdict"])


def _benchmark_preview(args: dict) -> dict:
    from crypto_strategy.service import StrategyError, crypto_strategy_service
    sid = (args.get("strategy_id") or "").strip()
    try:
        s = crypto_strategy_service.get_strategy(sid)
    except StrategyError as e:
        return {"error": str(e)}
    on = bool(args.get("is_benchmark", True))
    return {
        "strategy_id": sid, "name": s.get("name"), "version": f"v{s.get('version')}",
        "动作": "设为基准线" if on else "取消基准线",
        "note": ("基准线 = 你手写的那条尺子：它**永不参与切换**（不会被淘汰、也不会自动上位），"
                 "只在排行里当参照。⭐ 没有基准线的胜率是自说自话 —— "
                 "只有 AI 能写策略的话，就永远不知道 AI 有没有价值。"),
    }


class SetBenchmarkArgs(BaseModel):
    strategy_id: str = Field(..., min_length=3, description="策略号")
    is_benchmark: bool = Field(True, description="True=设为基准线，False=取消")


@tool(
    name="set_strategy_benchmark",
    description="【crypto 半自动策略·基准线】把某条策略标记成**基准线**（Jason 手写的那条尺子）。"
                "基准线永不参与切换，只在竞技场排行里当参照。"
                "用户说「把这条当基准 / 拿它当尺子比」时用。会先让 Jason 确认——"
                "⛔ 谁是基准线是 Jason 对「我自己写的那条」的认定，你不能自作主张给某条封基准线。",
    args_model=SetBenchmarkArgs,
    category="crypto", group="crypto",
    requires_confirmation=True, preview_fn=_benchmark_preview,
)
def set_strategy_benchmark(strategy_id: str, is_benchmark: bool = True) -> ToolEnvelope:
    from crypto_strategy.service import StrategyError, crypto_strategy_service
    try:
        r = crypto_strategy_service.set_benchmark(strategy_id, is_benchmark)
    except StrategyError as e:
        return ToolEnvelope(business_result="negative", message=str(e))
    return ToolEnvelope(
        data=r,
        message=(f"{r['name']} v{r['version']} "
                 f"{'已设为基准线' if is_benchmark else '已取消基准线'}。"))


# ──────────────────── 提案后验：拿真实盈亏给断言打分（S4）────────────────────

class ProposalScorecardArgs(BaseModel):
    days: int = Field(365, ge=7, le=1095, description="统计多长时间内的提案，默认一年")
    refresh: bool = Field(True, description="先跑一遍回填再统计（默认开）")


@tool(
    name="get_proposal_scorecard",
    description="【crypto 半自动策略·提案战绩】你之前提的策略变更，**兑现了几次、落空了几次**。"
                "每份提案都带「预期收益/预期胜率/多少天内兑现」，这里拿真实盈亏去证伪它们。"
                "用户问「你提的那些改法靠谱吗 / 你的建议准不准」时用。"
                "⚠️ **只有计数没有胜率百分比，这是刻意的**：策略变更一个月才 1-2 次，"
                "样本量要 2-3 年才有统计意义，现在报百分比等于给噪声盖权威章。"
                "⚠️ `unable/never_armed` 是「Jason 没让它上线」，**不算你判断失误**，别往自己身上揽。",
    args_model=ProposalScorecardArgs,
    category="crypto", group="crypto",
)
def get_proposal_scorecard(days: int = 365, refresh: bool = True) -> ToolEnvelope:
    from crypto_strategy import proposal_outcome as po

    filled = po.backfill_outcomes() if refresh else None
    card = po.scorecard(days=days)
    if not card["proposals"]:
        return ToolEnvelope(business_result="negative",
                            message="这段时间里一份策略变更提案都没有。",
                            data=card)
    if filled:
        card["backfill"] = filled
    return ToolEnvelope(data=card, message=_scorecard_line(card))


def _scorecard_line(card: dict) -> str:
    """⛔ **这个函数里永远不许出现百分号**（`test_scorecard_line_never_formats_a_rate`
    会咬）。一年 12-24 条提案，任何比率都是噪声，摆出来只会被当成结论引用。
    """
    c = card.get("by_outcome") or {}
    hit, miss = c.get("hit", 0), c.get("miss", 0)
    parts = [f"{card['proposals']} 份提案：兑现 {hit}、落空 {miss}、"
             f"待观察 {c.get('pending', 0)}、没法评 {c.get('unable', 0)}、"
             f"还没走到评估 {c.get('unevaluated', 0)}。"]
    if hit + miss == 0:
        parts.append("还没有一份走完兑现窗口 —— 现在下任何结论都太早。")
    else:
        real = card.get("decided_with_real_money", 0)
        parts.append(f"其中真金白银定论的只有 {real} 条"
                     f"（其余是纸面模拟，**不算真钱兑现**）。"
                     f"样本远不足以下统计结论，当定性参考看。")
    return " ".join(parts)

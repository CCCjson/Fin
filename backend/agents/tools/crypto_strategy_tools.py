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
        {"label": "下一步", "value": "可纸面/可上实盘", "type": "positive"},
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


@tool(
    name="list_strategy_standings",
    description="【crypto 半自动策略·总览】列出全部策略及各自战绩摘要（运行次数/产单数/盈亏/一句话结论）。"
                "用户问「我现在有哪些策略 / 它们都怎么样」时用。"
                "⚠️ **刻意不排名**：paper 与 live 不可比、样本量差异大，按收益排序等于诱导追涨杀跌。"
                "真正的优劣判定要走观察期/显著性/回测/风险四道门槛（还没实现）。",
    args_model=StrategyStandingsArgs,
    category="crypto", group="crypto",
)
def list_strategy_standings(days: int = 30) -> ToolEnvelope:
    from crypto_strategy.performance import standings

    rows = standings(days=days)
    if not rows:
        return ToolEnvelope(business_result="negative",
                            message="一条 crypto 策略都还没建。用 compile_crypto_strategy 编一条。")
    return ToolEnvelope(data={"count": len(rows), "days": days, "strategies": rows,
                              "ranking_note": "未排名——比较规则见工具说明。"})

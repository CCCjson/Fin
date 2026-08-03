"""策略执行器 —— 从「规则命中」走到「下单」的那条链路（S5 批次2）。

## 🔒 闸门顺序是有讲究的，别随手调

```
① 分析卡          ── 取不到就跳过这个标的（不猜）
② DSL 评估        ── 卖优先于买（持仓先看退出，与 crypto 引擎一致）
③ 交易单位换算    ── lot_floor，不足一手直接不发单
④ 可交易性        ── 涨跌停封板、T+1 冻结
⑤ 🔒 硬风控闸门   ── **最后一道，紧挨着下单**
⑥ 下单
```

**⑤ 必须在 ⑥ 之前、且在 ③④ 之后**：风控要按**实际要下的量**判，
按「策略想要的量」判会漏 —— 取整和封板都会改变实际下单量。

⛔ 风控闸门不可绕过、不可跳过、不可「先下单再补检查」。
它管的是 CLAUDE.md 里那四条红线（总仓位 ≤80% / 单日亏损 ≤3% /
每笔必须止损 / 连亏 3 次暂停），全自动交易下**没有人会在中间拦一手**。

## ⚠️ 关于「全自动」

Jason 2026-08-02 拍板：股票走**全自动**（人只监控），与 crypto 的逐笔确认不同。
这是他的决定，且与 2026-07-27 的新口径（审策略变更、不审每笔）自洽。
🔒 但硬风控四条**一条都没放开**，而且新策略必须先 paper 跑一段才能 arm 到 live。
"""
from dataclasses import dataclass, field
from typing import Any, Protocol

from loguru import logger

from common.trading_rules import is_tradable_at, lot_floor


class MarketAdapter(Protocol):
    """一个市场要提供的三样东西（其余都是共享的）。"""

    market: str

    def analyze(self, symbol: str) -> dict | None:
        """这个标的现在的分析卡；取不到返回 None（⛔ 别返回空 dict 冒充）。"""
        ...

    def broker_info(self) -> dict:
        """账户快照：cash / market_value / total_value / positions / ..."""
        ...

    def held_quantity(self, symbol: str) -> int:
        ...

    def sellable_quantity(self, symbol: str) -> int:
        """今天实际能卖多少（T+1 市场里当日买入的部分是冻结的）。"""
        ...

    def submit(self, symbol: str, side: str, quantity: int, price: float) -> Any:
        ...


@dataclass
class SymbolDecision:
    """一个标的这一轮的决策结果 —— **每一步为什么停下都要说得出来**。"""

    symbol: str
    status: str                     # ordered / skipped / blocked_risk / blocked_market / no_signal
    side: str | None = None
    quantity: int = 0
    price: float | None = None
    reason: str | None = None
    fired: list[str] = field(default_factory=list)
    rule_set: str | None = None

    def as_dict(self) -> dict:
        return {"symbol": self.symbol, "status": self.status, "side": self.side,
                "quantity": self.quantity, "price": self.price,
                "reason": self.reason, "fired": self.fired, "rule_set": self.rule_set}


def _price_of(card: dict) -> float | None:
    p = (card.get("price") or {}).get("latest")
    return float(p) if p else None


def _change_pct_of(card: dict) -> float | None:
    """当日涨跌幅（判涨跌停用）。取不到返回 None —— ⛔ 别用 0 顶替。

    「今天平盘」和「取不到涨跌幅」必须可分辨：后者用 0 顶替的话，
    涨跌停判定会**永远判不出封板**，全自动交易就会一直往封死的板上挂单。
    """
    for path in (("price", "change_pct"), ("price", "change_today_pct"),
                 ("price", "change_percent")):
        cur: Any = card
        for k in path:
            cur = cur.get(k) if isinstance(cur, dict) else None
        if cur is not None:
            return float(cur)
    return None


def decide_symbol(spec, symbol: str, adapter: MarketAdapter,
                  capital: float, risk_manager) -> SymbolDecision:
    """单个标的的完整决策（不下单，可单测）。"""
    from crypto_strategy.evaluator import evaluate

    rules = spec.owner_of(symbol)
    if rules is None:
        # ⛔ 不静默跳过：universe 里有它却没人认领 = DSL 写漏了一块
        return SymbolDecision(symbol, "skipped",
                              reason="不在任何规则集的 universe 里，本轮没有规则可用")

    card = adapter.analyze(symbol)
    if not card:
        return SymbolDecision(symbol, "skipped", reason="取不到分析卡",
                              rule_set=rules.name)
    if card.get("error"):
        return SymbolDecision(symbol, "skipped", reason=str(card["error"]),
                              rule_set=rules.name)

    price = _price_of(card)
    if not price or price <= 0:
        return SymbolDecision(symbol, "skipped", reason="取不到有效价格",
                              rule_set=rules.name)

    held = adapter.held_quantity(symbol)
    entry_hit, entry_fired = evaluate(rules.entry_rules.when, card, spec.market)
    exit_hit, exit_fired = evaluate(rules.exit_rules.when, card, spec.market)

    # ── ② 卖优先于买（持仓先看退出，与 crypto 引擎同口径）──
    if held > 0 and exit_hit:
        sellable = adapter.sellable_quantity(symbol)
        if sellable <= 0:
            return SymbolDecision(symbol, "blocked_market", side="SELL",
                                  reason="退出条件命中，但持仓当日买入尚未解冻（T+1）",
                                  fired=exit_fired, rule_set=rules.name)
        ok, why = is_tradable_at(spec.market, symbol, _change_pct_of(card), "SELL",
                                 card.get("name"))
        if not ok:
            return SymbolDecision(symbol, "blocked_market", side="SELL",
                                  reason=why, fired=exit_fired, rule_set=rules.name)
        return _risk_gate(spec, symbol, "SELL", sellable, price, adapter,
                          risk_manager, exit_fired, rules.name)

    # ── 空仓 → 看买入 ──
    if held <= 0 and entry_hit:
        # ⭐ 组合策略：这条规则集只分到 `weight × 总资金`（单策略 weight=1.0）
        budget = capital * rules.weight * _target_pct(spec, card)
        qty = lot_floor(spec.market, budget / price)
        if qty <= 0:
            return SymbolDecision(symbol, "skipped", side="BUY",
                                  reason=f"按预算算出的数量不足一手（预算 {budget:,.0f}）",
                                  fired=entry_fired, rule_set=rules.name)
        ok, why = is_tradable_at(spec.market, symbol, _change_pct_of(card), "BUY",
                                 card.get("name"))
        if not ok:
            return SymbolDecision(symbol, "blocked_market", side="BUY",
                                  reason=why, fired=entry_fired, rule_set=rules.name)
        return _risk_gate(spec, symbol, "BUY", qty, price, adapter,
                          risk_manager, entry_fired, rules.name)

    return SymbolDecision(symbol, "no_signal", rule_set=rules.name,
                          fired=(exit_fired if held > 0 else entry_fired))


def _target_pct(spec, card: dict) -> float:
    """这一笔想动用多少比例的资金。

    `suggested` 源走分析卡自己给的建议仓位；`fixed` 源走 DSL 里配死的。
    ⚠️ 拿不到建议时回落到 DSL 的固定值，再拿不到就 0（=不发单），
    ⛔ 别默认成「满仓」。
    """
    pp = spec.position_policy
    if pp.target_pct_source == "fixed":
        return float(pp.fixed_target_pct or 0.0)
    suggested = (card.get("suggested") or {}).get("target_pct")
    if suggested is None:
        suggested = card.get("suggested_position_pct")
    if suggested is None:
        return float(pp.fixed_target_pct or 0.0)
    return float(suggested)


def _risk_gate(spec, symbol: str, side: str, qty: int, price: float,
               adapter: MarketAdapter, risk_manager, fired: list[str],
               rule_set: str) -> SymbolDecision:
    """🔒 **最后一道闸门，紧挨着下单。**

    ⚠️ 必须按**实际要下的量**判（取整、封板、T+1 都已经改过量了），
    按「策略想要的量」判会漏。
    ⛔ 不可绕过、不可跳过、不可「先下单再补检查」——
    全自动交易下没有人会在中间拦一手。
    """
    passed, results = risk_manager.check_order(
        symbol, side, qty, price, adapter.broker_info())
    if not passed:
        failed = "；".join(r.message for r in results if not r.passed) or "风控拒绝"
        return SymbolDecision(symbol, "blocked_risk", side=side, quantity=qty,
                              price=price, reason=failed, fired=fired,
                              rule_set=rule_set)
    return SymbolDecision(symbol, "ready", side=side, quantity=qty, price=price,
                          fired=fired, rule_set=rule_set)


def _record(spec, d: SymbolDecision, order, strategy_id: str | None, mode: str) -> None:
    """成交留痕（失败只 warning，不掀翻已经成交的单）。"""
    if not strategy_id:
        return
    from strategy_runtime.ledger import record_fill

    record_fill(market=spec.market, symbol=d.symbol, side=d.side or "",
                price=d.price or 0.0, quantity=d.quantity,
                strategy_id=strategy_id, rule_set=d.rule_set, mode=mode,
                broker_order_id=str(getattr(order, "order_id", "") or "") or None)


def run_tick(spec, adapter: MarketAdapter, *, capital: float,
             risk_manager=None, dry_run: bool = False,
             strategy_id: str | None = None, mode: str = "paper") -> dict:
    """跑一轮：逐标的决策，`ready` 的真的下单，并**当场留痕**。

    Args:
        dry_run: 只决策不下单（干跑 / 单测用）。
            ⚠️ 它**不是**「安全模式」—— `paper` 与 `live` 的区别在 broker，不在这里。
        strategy_id: 归因用。不传就**不留痕**（单测/临时试跑）。
            ⚠️ 真跑策略时必须传，否则战绩算不出来。
        mode: `paper` / `live` —— 🔒 台账里分桶，查询时**绝不合并**
            （paper 是理想撮合，跟真钱成绩加在一起等于拿模拟成绩背书）。

    Returns:
        `{decisions: [...], tally: {...}}`
    """
    if risk_manager is None:
        from trading_engine.risk.manager import RiskManager
        risk_manager = RiskManager()

    decisions: list[SymbolDecision] = []
    for symbol in spec.universe.symbols:
        try:
            d = decide_symbol(spec, symbol, adapter, capital, risk_manager)
        except Exception as e:  # noqa: BLE001 — 一个标的炸掉不该让整轮停摆
            logger.warning(f"[{spec.market}] {symbol} 决策异常: {e}")
            d = SymbolDecision(symbol, "skipped", reason=f"决策异常：{e}")
        if d.status == "ready" and not dry_run:
            try:
                order = adapter.submit(symbol, d.side, d.quantity, d.price)
                d.status = "ordered"
                # 🔴 归因**当场**写进成交台账。S1 的教训：靠桥表事后 join 的话，
                #    那张表的 FILLED 行 24 小时后被清理，「这笔属于哪条策略」
                #    就永久查不回来了。⛔ 别改成「先记引用回头补」。
                _record(spec, d, order, strategy_id, mode)
            except Exception as e:  # noqa: BLE001
                logger.warning(f"[{spec.market}] {symbol} 下单失败: {e}")
                d.status = "order_failed"
                d.reason = str(e)
        decisions.append(d)

    tally: dict[str, int] = {}
    for d in decisions:
        tally[d.status] = tally.get(d.status, 0) + 1
    return {"market": spec.market, "dry_run": dry_run,
            "decisions": [d.as_dict() for d in decisions], "tally": tally}

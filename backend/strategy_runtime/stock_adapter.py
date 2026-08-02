"""股票市场适配器 —— 把 A 股/美股接进通用执行器（S5 批次2）。

一个市场只需要提供三样东西（其余都是 `executor.py` 共享的）：
分析卡从哪来、下单走哪个 broker、持仓怎么读。

## ⚠️ T+1 的可卖量是这里最容易写错的地方

`BrokerPosition.available` 才是「今天真能卖多少」，`quantity` 是总持仓。
⛔ 别拿 `quantity` 当可卖量 —— A 股当日买入是冻结的，那样会排出一批
**券商必然拒掉**的卖单，而在纸面上它们会「成交」，于是纸面成绩虚高。
"""
from typing import Any

from loguru import logger

from common.market import infer_market_from_symbol


class StockAdapter:
    """A 股 / 美股的适配器。

    Args:
        broker: 任何实现了 `BaseBroker` 的对象。
            ⛔ 这里**不许出现具体券商的名字** —— 真券商到位时新增一个实现即可
            （Jason 2026-08-02：执行层先建地基，券商后接）。
        market: canonical 市场；不传则按第一个标的推断。
    """

    def __init__(self, broker, market: str | None = None):
        self.broker = broker
        self._market = market
        self._agg = None

    @property
    def market(self) -> str:
        if self._market is None:
            raise ValueError("StockAdapter 没有指定 market，且没法从标的推断")
        return self._market

    def for_spec(self, spec) -> "StockAdapter":
        """把 spec 声明的市场绑上来（spec 那边已经校验过标的与市场一致）。"""
        self._market = spec.market
        return self

    # ── 分析卡 ──

    def analyze(self, symbol: str) -> dict | None:
        """走 `CockpitAggregator` —— 股票侧的分析卡真源。

        ⛔ 取不到就返回 None，**别返回空 dict 冒充**：
        空 dict 会让所有 DSL 条件「取不到值 → 不满足」，看上去像
        「行情没到条件」，而其实是分析根本没跑出来。
        """
        if self._agg is None:
            from cockpit_engine.aggregator import CockpitAggregator
            self._agg = CockpitAggregator()
        try:
            card = self._agg.aggregate(symbol)
        except Exception as e:  # noqa: BLE001 — 一个标的分析失败不该掀翻整轮
            logger.warning(f"[stock] {symbol} 分析卡取不到: {e}")
            return None
        return card or None

    # ── 账户 ──

    def broker_info(self) -> dict:
        """账户快照 —— 风控闸门吃的就是它。

        ⚠️ 走 `trading_engine.risk.adapter.build_broker_info`，因为它会把
        `recent_closed_pnls` / `last_loss_date` 一起带上 —— 那是「连亏 3 次暂停」
        这条红线的输入。⛔ 自己手拼一个 dict 会把那条红线悄悄架空。
        """
        from trading_engine.risk.adapter import build_broker_info
        return build_broker_info()

    def held_quantity(self, symbol: str) -> int:
        pos = self.broker.get_position(symbol)
        return int(getattr(pos, "quantity", 0) or 0) if pos else 0

    def sellable_quantity(self, symbol: str) -> int:
        """今天真能卖多少。

        🔴 用 `available` 不是 `quantity`：A 股当日买入是冻结的（T+1）。
        拿 `quantity` 当可卖量会排出一批**券商必然拒掉**的卖单，
        而纸面上它们会「成交」→ 纸面成绩虚高。
        """
        pos = self.broker.get_position(symbol)
        if not pos:
            return 0
        avail = getattr(pos, "available", None)
        if avail is None:                      # 券商没给可卖量时保守取 0
            return 0
        return max(0, int(avail))

    # ── 下单 ──

    def submit(self, symbol: str, side: str, quantity: int, price: float) -> Any:
        order = self.broker.submit_order(symbol, side, quantity, price)
        status = getattr(getattr(order, "status", None), "value", None) or ""
        if status.upper() not in ("FILLED", "SUBMITTED", "PARTIAL_FILLED", "PENDING"):
            raise RuntimeError(getattr(order, "error_msg", None) or f"下单未成交：{status}")
        return order

    # ── 日切 ──

    def settle_new_day(self) -> None:
        """新交易日开盘：解冻 T+1 持仓。

        ⚠️ **一天只该调一次**。⛔ 别塞进 tick 循环 —— 那等于取消了 T+1，
        纸面成绩会重新变得比实盘好看。
        """
        fn = getattr(self.broker, "settle_t1", None)
        if callable(fn):
            fn()


def market_of(symbol: str) -> str:
    """按标的推断市场（全项目唯一的后缀推断实现）。"""
    return infer_market_from_symbol(symbol)

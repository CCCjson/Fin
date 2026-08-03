"""成交台账写入 —— 归因**当场**落库（S5）。

## 🔴 为什么不能事后 join

S1 踩过一次：策略排的单只在待确认单表里留了个桥接引用，而那张表的 FILLED 行
**24 小时后就被清理删掉** —— 超期之后「这笔成交属于哪条策略」永久查不回来，
策略战绩就成了一笔糊涂账。

所以 `source_kind` / `source_ref` / `rule_set` 在**下单成交的那一刻**就写进
成交行本身。⛔ 别改成「先记个引用，回头再补」。

## 🔒 纸面与实盘分桶

`mode` 列区分 paper / live，**查询时绝不合并**（S4 的教训：paper 是理想撮合，
把它跟真钱成绩加在一起等于拿模拟成绩给真钱决策背书）。
"""
from datetime import date
from typing import Any

from loguru import logger

from common.market_time import market_today
from common.trade_source import STRATEGY


def record_fill(*, market: str, symbol: str, side: str, price: float,
                quantity: int, strategy_id: str, rule_set: str | None,
                mode: str, commission: float = 0.0,
                broker_order_id: str | None = None,
                source_kind: str = STRATEGY) -> int | None:
    """写一行成交台账。返回行 id；失败返回 None。

    ⚠️ **写台账失败不该掀翻已经成交的单** —— 钱已经动了，抛异常只会让上层以为
    没成交而重复下单。所以这里吞异常并 warning，由对账去补
    （与 `record_decision` 同一套处置）。
    """
    from data_engine.storage.database import get_session
    from data_engine.storage.models import StrategyTrade

    session = None
    try:
        session = get_session()
        row = StrategyTrade(
            market=market, symbol=symbol, side=side.upper(),
            price=float(price), quantity=int(quantity),
            amount=float(price) * int(quantity), commission=float(commission or 0),
            broker_order_id=broker_order_id,
            # ⚠️ 交易日按**该市场的当地日**切（美股的 8/2 不是北京时间的 8/2）
            trade_date=market_today(market),
            source_kind=source_kind, source_ref=strategy_id,
            rule_set=rule_set, mode=mode)
        session.add(row)
        session.commit()
        return int(row.id)
    except Exception as e:  # noqa: BLE001 — 见 docstring：钱已经动了，别往上抛
        logger.warning(f"成交台账写入失败 {symbol} {side} {quantity}@{price}: {e}")
        return None
    finally:
        if session is not None:
            session.close()


def strategy_fills(strategy_id: str, *, mode: str | None = None,
                   days: int | None = 30,
                   until: date | None = None) -> list[dict[str, Any]]:
    """一条策略的成交明细。

    Args:
        mode: 只要某一桶。⚠️ 不传时**两种模式都会返回**，调用方必须自己分桶 ——
            ⛔ 别把 paper 和 live 的盈亏加在一起。
        days: 往回数多少天。🔴 **算盈亏必须传 `None`（=全历史）**：
            成本基础天然跨窗口（买在 40 天前、卖在昨天），按 `days` 切会把买入腿
            切掉 → 配对时 `costed = min(sold, 0) = 0` → 那笔平仓不记 →
            **收益静默低估**，而且不会有任何报错。窗口是**展示口径**，
            应该切在平仓日上（见 `crypto_strategy.performance._pair` 的 `since`）。
        until: 只看这一天（含）之前的成交，**按市场当地日**给。用于「回到 T 时刻
            重算一遍」这类历史窗口查询。
    """
    from datetime import timedelta

    from common.market_time import utc_now
    from data_engine.storage.database import get_session
    from data_engine.storage.models import StrategyTrade

    session = get_session()
    try:
        q = session.query(StrategyTrade).filter(
            StrategyTrade.source_ref == strategy_id)
        if days is not None:
            q = q.filter(StrategyTrade.trade_date
                         >= (utc_now() - timedelta(days=max(1, days))).date())
        if until is not None:
            q = q.filter(StrategyTrade.trade_date <= until)
        if mode:
            q = q.filter(StrategyTrade.mode == mode)
        rows = q.order_by(StrategyTrade.trade_date.asc(),
                          StrategyTrade.id.asc()).all()
        return [{"id": r.id, "market": r.market, "symbol": r.symbol, "side": r.side,
                 "price": r.price, "quantity": r.quantity, "amount": r.amount,
                 "commission": r.commission, "trade_date": r.trade_date.isoformat(),
                 "rule_set": r.rule_set, "mode": r.mode} for r in rows]
    finally:
        session.close()

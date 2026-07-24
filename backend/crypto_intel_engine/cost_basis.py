"""币安持仓成本重建 —— 从原始成交明细回放加权平均成本，并**如实标注覆盖度**。

## 为什么需要这个模块

币安**没有任何返回「持仓成本」的端点**（`/api/v3/account`、资金钱包、Simple Earn
三处全是纯余额）。此前 `BinanceBroker` 把 `avg_cost` 置成当前价、`unrealized_pnl` 恒 0，
后果不是「少显示一个数字」，而是**四条硬风控结构性失效**：

| 风控 | 失效原因 |
|---|---|
| 止损 -5% / 止盈 +15% | `loss_pct = (price - avg_cost)/avg_cost ≡ 0`，永不触发 |
| 单日最大亏损 3% | 吃的是 `unrealized_pnl ≡ 0` |
| 策略引擎当日回撤熔断 | 同上，浮亏 -30% 也照常排新单 |

币安 App 里的成本价同样是客户端按成交历史回放算出来的（官方社区专帖
"v3/myTrades: Determining Cost Basis from Trade History" 是同一结论），所以这条路是对的。

## 单一真源：三份回放收口成一份

改动前项目里有**三份**各自实现的加权成本回放（`execution.get_recent_crypto_closed_pnls`、
`engine._realized_today`、A股的 `risk/adapter.get_recent_closed_pnls`）。本模块把前两份
crypto 的收口成一份 `replay()`，口径完全一致；A股那份数据源不同（ManualTrade），不动。

## 诚实优先：覆盖度三态，绝不拿现价冒充成本

链上充值、空投、理财利息、法币一键买币进来的币**本就没有成本**，谁也算不出来。
`coverage` 如实返回三态，**算不出就返回 `avg_cost=0`** —— 这样 `StopLossRule` 会走
「无持仓成本，跳过」分支明说跳过，而不是拿假成本算出「亏损 0%」假装安全。
"""
from __future__ import annotations

from datetime import date, datetime, timezone
from typing import Any

from loguru import logger

from common.market import CRYPTO, to_binance_symbol
from common.market_time import market_day_bounds, market_day_of

STABLE_QUOTE = "USDT"          # v1 只处理 USDT 计价对（与 execution.STABLE_QUOTE 同口径）
_PAGE = 1000                   # myTrades 每页上限
_MAX_PAGES = 200               # 单次同步最多翻多少页（20 万笔，够任何个人账户）
# 回放数量与交易所真实余额的相对容差：低于此视为「对得上」（浮点/尘埃币误差）
_COVERAGE_TOL = 0.005


# ──────────────── symbol 拆解 ────────────────

def split_pair(symbol: str) -> tuple[str, str]:
    """`BTCUSDT.BN` → `("BTC", "USDT")`。非 USDT 计价对返回 `(整串, "")`。"""
    bn = to_binance_symbol(symbol)
    if bn.endswith(STABLE_QUOTE):
        return bn[: -len(STABLE_QUOTE)], STABLE_QUOTE
    return bn, ""


# ──────────────── 拉取 + 落库（增量）────────────────

def _last_trade_id(session, symbol: str, source: str = "spot") -> int:
    from sqlalchemy import text
    row = session.execute(text("""
        SELECT MAX(CAST(trade_id AS INTEGER)) FROM crypto_fills
        WHERE symbol = :symbol AND source = :source
    """), {"symbol": symbol, "source": source}).scalar()
    return int(row) if row is not None else -1


def _commit_with_retry(session, *, tries: int = 5, base_delay: float = 0.5) -> None:
    """commit，撞 SQLite `database is locked` 时退避重试。

    ⛔ 库是 WAL + `busy_timeout=30s`（`common/db.py`），照理短锁能自己等过去。但 A 股
    日线更新有 5000+ 只票的**超长批量写事务**，写锁能持有超过 30s，此时 commit 会抛
    `OperationalError('database is locked')`。crypto 成交明细同步撞上这个窗口本不该整轮
    报废 —— 而它偏偏会：`sync_symbol_fills` 的 commit 抛异常会冒泡出 `sync_held_fills`
    的循环，把**后面还没同步的币一起拖没**（实测就是这样让 ETH/SOL 的成本一直 unknown）。
    应用层再兜一层重试治本。仍非 locked 类错误、或重试耗尽，照常抛。
    """
    import time

    from sqlalchemy.exc import OperationalError
    for i in range(tries):
        try:
            session.commit()
            return
        except OperationalError as e:
            if "locked" not in str(e).lower() or i == tries - 1:
                raise
            session.rollback()
            time.sleep(base_delay * (i + 1))


def _insert_fills(session, rows: list[dict]) -> int:
    """幂等写入（撞 (source,symbol,trade_id) 唯一键即忽略）。返回**新增**条数。"""
    from sqlalchemy import text
    n = 0
    for r in rows:
        res = session.connection().execute(text("""
            INSERT OR IGNORE INTO crypto_fills
                (source, symbol, trade_id, order_id, price, quantity, quote_qty,
                 commission, commission_asset, is_buyer, trade_time)
            VALUES (:source, :symbol, :trade_id, :order_id, :price, :quantity, :quote_qty,
                    :commission, :commission_asset, :is_buyer, :trade_time)
        """), r)
        n += res.rowcount or 0
    _commit_with_retry(session)
    return n


def sync_symbol_fills(symbol: str, full: bool = False) -> dict[str, Any]:
    """把某交易对的币安成交明细增量同步到 `crypto_fills`。

    Args:
        full: True = 从头全量回填（`fromId=0`）；False = 从库里已有的最大 tradeId 之后续拉。

    Returns:
        `{"symbol", "fetched", "inserted", "pages", "error"?}`。出网失败**不抛**，
        返回带 `error` 的结果 —— 同步失败只该让覆盖度降级，不该炸掉持仓查询。
    """
    from acquisition.markets import binance_trade as bt
    from data_engine.storage.database import get_session

    if not bt.has_credentials():
        return {"symbol": symbol, "fetched": 0, "inserted": 0, "pages": 0,
                "error": "币安 API key 未配置"}

    session = get_session()
    try:
        from_id = 0 if full else _last_trade_id(session, symbol) + 1
        fetched = inserted = pages = 0
        while pages < _MAX_PAGES:
            try:
                batch = bt.my_trades(symbol, from_id=from_id, limit=_PAGE)
            except Exception as e:  # noqa: BLE001 — 网络/限速失败：保留已拉到的，下次续
                logger.warning(f"拉取 {symbol} 成交明细失败（已同步 {inserted} 条）: {e}")
                return {"symbol": symbol, "fetched": fetched, "inserted": inserted,
                        "pages": pages, "error": str(e)}
            if not batch:
                break
            pages += 1
            fetched += len(batch)
            inserted += _insert_fills(session, [_normalize_fill(symbol, t) for t in batch])
            if len(batch) < _PAGE:
                break
            from_id = int(batch[-1]["id"]) + 1
        return {"symbol": symbol, "fetched": fetched, "inserted": inserted, "pages": pages}
    finally:
        session.close()


def _normalize_fill(symbol: str, t: dict) -> dict:
    return {
        "source": "spot", "symbol": symbol, "trade_id": str(t["id"]),
        "order_id": str(t.get("orderId") or ""),
        "price": float(t["price"]), "quantity": float(t["qty"]),
        "quote_qty": float(t.get("quoteQty") or 0) or float(t["price"]) * float(t["qty"]),
        "commission": float(t.get("commission") or 0),
        "commission_asset": t.get("commissionAsset") or "",
        "is_buyer": 1 if t.get("isBuyer") else 0,
        # 币安给的是 ms UTC 时间戳。存 naive UTC（与库里其它 UTC 列一致）
        "trade_time": datetime.fromtimestamp(int(t["time"]) / 1000, tz=timezone.utc)
                              .replace(tzinfo=None),
    }


# ──────────────── 手续费折算（commissionAsset 单位不可混加）────────────────

class _FeePricer:
    """把手续费折算成计价币（USDT）。带缓存，只读本地日线，**不出网**。

    币安现货 BUY 的手续费默认扣**基础币**（买 BTC 扣 BTC），开了 BNB 抵扣则扣 BNB ——
    旧代码 `sum(commission)` 不看 `commissionAsset` 直接当 USDT 累加，把 0.00001 BTC
    当成 0.00001 美元，等于手续费恒等于 0。
    """

    def __init__(self) -> None:
        self._cache: dict[tuple[str, date], float | None] = {}

    def close_price(self, asset: str, day: date) -> float | None:
        """取 `{asset}USDT` 在 `day` 的日线收盘（本地库）。查不到返回 None。"""
        key = (asset, day)
        if key in self._cache:
            return self._cache[key]
        from sqlalchemy import text

        from data_engine.storage.database import get_session
        session = get_session()
        try:
            row = session.execute(text("""
                SELECT close FROM daily_quotes
                WHERE symbol = :symbol AND date <= :day
                ORDER BY date DESC LIMIT 1
            """), {"symbol": f"{asset}{STABLE_QUOTE}.BN", "day": day.isoformat()}).scalar()
            val = float(row) if row is not None else None
        except Exception as e:  # noqa: BLE001
            logger.warning(f"手续费折算取价失败 {asset}@{day}: {e}")
            val = None
        finally:
            session.close()
        self._cache[key] = val
        return val

    def to_quote(self, amount: float, asset: str, day: date) -> float | None:
        """折算成 USDT。返回 `None` = 折算不了。

        ⛔ **折算不了的额度由调用方记进「那个 symbol 自己的」`unpriced_fees`，不可当 0，
        也不可攒在本对象上**。此前 `_FeePricer` 自己有一个全局 `unpriced` 累加字典，
        `replay()` 末尾又把它整份赋给**每一个** symbol —— 只要任意一个币有折算不了的
        BNB 手续费，所有币的 `cost_for` note 都会挂上它。数值不受影响，但给 Jason 看的
        那句话是错的。
        """
        if amount <= 0:
            return 0.0
        if asset == STABLE_QUOTE:
            return amount
        px = self.close_price(asset, day)
        return None if px is None else amount * px


# ──────────────── 回放（单一真源）────────────────

def replay(symbol: str | None = None) -> dict[str, dict[str, Any]]:
    """按加权平均成本法回放 `crypto_fills`，返回 **{symbol: 状态}**。

    状态字段：
      - `quantity`     回放得出的应持数量（买入净额 - 卖出）
      - `total_cost`   当前持仓的累计成本（USDT）
      - `avg_cost`     加权平均成本；无持仓时为 0
      - `closed`       平仓明细 `[{"pnl", "date"}]`，**最新在前**
      - `fills`        参与回放的成交笔数
      - `unpriced_fees` **该 symbol 自己**折算不了的手续费 `{asset: amount}`（不静默当 0）

    ⚠️ 卖出量可能超过回放出的持仓量（币是充值/空投进来的，我们没有它的买入记录）。
    这种情况下把超出部分的成本记为 **未知**（不按 0 成本算成暴利，那会把当日已实现盈亏
    灌成巨额正数、当天的熔断线直接失效），并置 `has_uncosted_sell`。
    """
    from sqlalchemy import text

    from data_engine.storage.database import get_session
    session = get_session()
    try:
        sql = ("SELECT symbol, price, quantity, quote_qty, commission, commission_asset,"
               " is_buyer, trade_time FROM crypto_fills")
        params: dict[str, Any] = {}
        if symbol:
            sql += " WHERE symbol = :symbol"
            params["symbol"] = symbol
        sql += " ORDER BY trade_time ASC, CAST(trade_id AS INTEGER) ASC"
        rows = session.execute(text(sql), params).fetchall()
    except Exception as e:  # noqa: BLE001
        logger.warning(f"成交明细回放读取失败: {e}")
        return {}
    finally:
        session.close()

    pricer = _FeePricer()
    out: dict[str, dict[str, Any]] = {}
    for r in rows:
        sym, price, qty, quote_qty, fee, fee_asset, is_buyer, t_time = r
        base, quote = split_pair(sym)
        # `trade_time` 存 naive UTC。归到**市场日**而不是裸 `.date()`：crypto 的市场时区
        # 就是 UTC，两者当下相等，但写成 market_day_of 才是在声明口径——`closed[].date`
        # 要和 `realized_on(market_today(CRYPTO))` 对得上，也要和 `daily_quotes`(crypto)
        # 的 UTC 日同轴（`_FeePricer.close_price` 正好查那张表）。
        raw_time = (t_time if isinstance(t_time, datetime)
                    else datetime.fromisoformat(str(t_time)))
        day = market_day_of(raw_time, CRYPTO)
        st = out.setdefault(sym, {"quantity": 0.0, "total_cost": 0.0, "avg_cost": 0.0,
                                  "closed": [], "fills": 0, "unpriced_fees": {},
                                  "has_uncosted_sell": False})
        st["fills"] += 1
        qty = float(qty)
        gross = float(quote_qty or 0) or float(price) * qty
        fee = float(fee or 0)
        fee_asset = (fee_asset or "").upper()

        if is_buyer:
            net_qty, cost = qty, gross
            if fee > 0:
                if fee_asset == base:
                    net_qty -= fee                      # 扣基础币 = 实际到手更少
                else:
                    v = pricer.to_quote(fee, fee_asset, day)
                    if v is not None:
                        cost += v                       # 扣 USDT/BNB = 实际花得更多
                    else:
                        _note_unpriced(st, fee_asset, fee)
            st["total_cost"] += cost
            st["quantity"] += net_qty
        else:
            proceeds, sold = gross, qty
            if fee > 0:
                if fee_asset == base:
                    sold += fee                         # 罕见：卖出还扣基础币 = 多耗币
                else:
                    v = pricer.to_quote(fee, fee_asset, day)
                    if v is not None:
                        proceeds -= v
                    else:
                        _note_unpriced(st, fee_asset, fee)
            costed = min(sold, st["quantity"])          # 只有回放得出的那部分才有成本
            if costed < sold:
                # 卖的比我们知道的多 → 超出部分成本未知，不按 0 成本算成暴利
                st["has_uncosted_sell"] = True
            if costed > 0:
                pnl = proceeds * (costed / sold) - st["avg_cost"] * costed
                st["closed"].append({"pnl": pnl, "date": day.isoformat()})
            st["quantity"] = max(0.0, st["quantity"] - sold)
            st["total_cost"] = st["avg_cost"] * st["quantity"]

        st["avg_cost"] = (st["total_cost"] / st["quantity"]) if st["quantity"] > 0 else 0.0

    for st in out.values():
        st["closed"].reverse()                          # 最新在前
    return out


def _note_unpriced(st: dict[str, Any], asset: str, amount: float) -> None:
    """折算不了的手续费记进**这个 symbol 自己的**账上（绝不当 0，也绝不串到别的币）。"""
    st["unpriced_fees"][asset] = st["unpriced_fees"].get(asset, 0.0) + amount


# ──────────────── 对外：带覆盖度的成本 ────────────────

def cost_for(symbol: str, actual_quantity: float,
             replayed: dict[str, dict] | None = None) -> dict[str, Any]:
    """给一个持仓算成本 + **覆盖度**。绝不拿现价冒充成本。

    Args:
        actual_quantity: 交易所侧的真实持仓量（三钱包合计），用来校验回放覆盖了多少。
        replayed: 复用已有的 `replay()` 结果（批量查持仓时避免重复回放全表）。

    Returns:
        `{"avg_cost", "quality", "coverage", "covered_quantity", "fills", "note"}`
        - `quality="full"`    回放数量 ≥ 真实持仓 → 成本可信（多出来是提币出去了，不影响成本）
        - `quality="partial"` 回放数量 < 真实持仓 → 有来源不明的币（充值/空投/利息/闪兑漏拉），
                              `avg_cost` 只代表**已知那部分**
        - `quality="unknown"` 一笔成交都没有 → `avg_cost=0`，让止损规则走「无成本，跳过」
    """
    table = replay(symbol) if replayed is None else replayed
    st = table.get(symbol)
    if not st or st["fills"] == 0 or st["quantity"] <= 0:
        return {"avg_cost": 0.0, "quality": "unknown", "coverage": 0.0,
                "covered_quantity": 0.0, "fills": (st or {}).get("fills", 0),
                "note": "无成交记录可回放（币可能来自充值/空投/理财，或尚未同步成交明细）"}

    known = st["quantity"]
    actual = float(actual_quantity or 0)
    if actual <= 0:
        coverage = 1.0
    else:
        coverage = min(1.0, known / actual)

    if actual <= 0 or known >= actual * (1 - _COVERAGE_TOL):
        quality, note = "full", None
    else:
        quality = "partial"
        gap = actual - known
        note = (f"持有 {actual:g}，其中 {gap:g} 来源不明（充值/空投/理财利息/闪兑），"
                f"成本仅覆盖 {coverage:.0%}")
    notes = [note] if note else []
    if st.get("unpriced_fees"):
        # 只报**这个 symbol 自己**折算不了的（此前是全表共用一份，谁有问题所有币都跟着挂）
        notes.append(f"部分手续费无法折算成 USDT：{st['unpriced_fees']}")
    if st.get("has_uncosted_sell"):
        # 设了却没人读等于白设 —— 这条直接影响「这个成本能信几分」，必须让 Jason 看见
        notes.append("历史上卖出量超过可回放的买入量（有币来自充值/空投），"
                     "那部分平仓盈亏按成本未知处理，均价只代表有记录的部分")
    return {"avg_cost": st["avg_cost"], "quality": quality, "coverage": round(coverage, 4),
            "covered_quantity": known, "fills": st["fills"],
            "note": "；".join(notes) if notes else None}


# ──────────────── 对外：平仓盈亏（收口两份重复实现）────────────────

def closed_pnls(limit: int = 10) -> tuple[list[float], str | None]:
    """最近 N 笔平仓盈亏（最新在前）+ 最近亏损日期。喂 RiskManager 的 ConsecutiveLossRule。"""
    all_closed: list[dict] = []
    for st in replay().values():
        all_closed.extend(st["closed"])
    all_closed.sort(key=lambda c: c["date"], reverse=True)
    pnls = [c["pnl"] for c in all_closed[:limit]]
    last_loss = next((c["date"] for c in all_closed if c["pnl"] < 0), None)
    return pnls, last_loss


def fees_on(day: date) -> float:
    """某个**crypto 市场日**（UTC）的手续费合计（USDT），按 `commissionAsset` 正确折算。

    喂引擎的「单日手续费上限」护栏。⛔ 不能拿 `sum(commission)` 直接加：币安现货买入
    默认扣**基础币**（买 BTC 扣 BTC），把 0.00001 BTC 当成 0.00001 美元累加会让费用
    恒等于约 0，护栏结构上不可能触发。折算不了的（如本地没有 BNB 日线）计入返回值时
    按 0 处理，但同一笔在回放侧会记进该 symbol 的 `unpriced_fees`，由 `cost_for` 的 note 报出。

    ⛔ **必须用 `market_day_bounds` 切区间，不能写 `DATE(trade_time) = :day`**。
    `trade_time` 存的是 naive UTC，而调用方给的 `day` 曾经是服务器本地的 `date.today()`
    ——实测本地凌晨 3 点的成交按旧写法查**恒为 0**（那笔的 UTC 日期还是昨天），
    于是本地 00:00–08:00 的成交完全不进这条护栏，也不进当日回撤熔断。
    """
    from sqlalchemy import text

    from data_engine.storage.database import get_session
    start, end = market_day_bounds(CRYPTO, day)
    session = get_session()
    try:
        rows = session.execute(text("""
            SELECT symbol, commission, commission_asset, quantity, price, quote_qty, is_buyer
            FROM crypto_fills WHERE trade_time >= :start AND trade_time < :end
        """), {"start": start, "end": end}).fetchall()
    except Exception as e:  # noqa: BLE001
        logger.warning(f"当日手续费读取失败: {e}")
        return 0.0
    finally:
        session.close()

    pricer = _FeePricer()
    total = 0.0
    for sym, fee, fee_asset, qty, price, quote_qty, _is_buyer in rows:
        fee = float(fee or 0)
        if fee <= 0:
            continue
        base, _quote = split_pair(sym)
        asset = (fee_asset or "").upper()
        if asset == base:
            # 扣基础币 → 按这笔成交自己的成交价折算（比查日线更准，也不用出网）
            px = float(price or 0) or (float(quote_qty or 0) / float(qty) if qty else 0.0)
            total += fee * px
        else:
            total += pricer.to_quote(fee, asset, day) or 0.0
    return round(total, 6)


def realized_on(day: date) -> float:
    """某个 **crypto 市场日**（UTC）的已实现盈亏。喂引擎当日回撤熔断。

    `day` 必须来自 `market_today(CRYPTO)` —— `replay()` 里的 `closed[].date` 是按
    `market_day_of(..., CRYPTO)` 归的，拿服务器本地 `date.today()` 来对会错开 8 小时。
    """
    key = day.isoformat()
    return sum(c["pnl"] for st in replay().values()
               for c in st["closed"] if c["date"] == key)


def sync_held_fills(full: bool = False) -> dict[str, Any]:
    """同步**当前持仓 + 历史交易过**的所有交易对的成交明细。给调度器/手动补齐用。

    要同步哪些 symbol 有两个来源，缺一不可：
      - 交易所当前持仓 → 这些币现在就需要成本价；
      - 库里已有 fills 的 symbol → 已清仓的历史仓位，平仓盈亏还要参与连亏 streak 回放。

    没配 key 或连不上时静默跳过（返回 `skipped`）——同步不是关键路径，
    失败只该让覆盖度降级，不该报错打断调度。
    """
    from acquisition.markets import binance_trade as bt
    if not bt.has_credentials():
        return {"skipped": "no_credentials", "symbols": 0}

    symbols: set[str] = set()
    try:
        from trading_engine.brokers.binance_broker import get_binance_broker
        broker = get_binance_broker()
        if broker.connect():
            symbols.update(p.symbol for p in broker.get_positions())
    except Exception as e:  # noqa: BLE001
        logger.warning(f"同步成交明细时读持仓失败: {e}")

    from sqlalchemy import text

    from data_engine.storage.database import get_session
    session = get_session()
    try:
        symbols.update(r[0] for r in
                       session.execute(text("SELECT DISTINCT symbol FROM crypto_fills")))
    except Exception:  # noqa: BLE001 — 表还没建（首次运行）
        pass
    finally:
        session.close()

    # ⛔ 逐币 try：单个币同步失败（网络/限速/写库锁）**绝不能中断整轮**。此前是列表推导，
    # 一个币抛异常就把后面还没同步的币全拖没 —— ETH/SOL 成本长期 unknown 就是这么来的
    # （BTC 之外的币还没轮到就整轮挂了）。失败的币记进 errors，下一轮/下次仍会重试它。
    results = []
    for s in sorted(symbols):
        try:
            results.append(sync_symbol_fills(s, full=full))
        except Exception as e:  # noqa: BLE001 — 单币失败隔离，不拖累其它币
            logger.warning(f"同步 {s} 成交明细失败（不影响其它币）: {e}")
            results.append({"symbol": s, "inserted": 0, "error": str(e)})
    return {
        "symbols": len(results),
        "inserted": sum(r.get("inserted", 0) for r in results),
        "errors": [r["symbol"] for r in results if r.get("error")],
    }


def has_any_fills() -> bool:
    """库里有没有成交明细 —— 没有就退回旧的 CryptoTrade 台账口径（向后兼容）。"""
    from sqlalchemy import text

    from data_engine.storage.database import get_session
    session = get_session()
    try:
        return bool(session.execute(text("SELECT 1 FROM crypto_fills LIMIT 1")).scalar())
    except Exception:  # noqa: BLE001 — 表还没建/库不可用
        return False
    finally:
        session.close()

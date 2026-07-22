"""加密货币（币安现货）执行原语 —— 风控/资金腾挪/成交台账/理财扫归的**单一真源**。

从 `agents/tools/crypto_tools.py` 抽出，供两条下单路径共用、永不分叉：

- **交互式聊天下单** `place_crypto_order`（`requires_confirmation=True`，逐笔人工确认）。
- **自主策略引擎** `crypto_strategy/engine.py`（免逐笔确认，但受一整套护栏+这里的硬风控约束）。

⛔ 铁律：无论哪条路径，`crypto_broker_info` + `risk_check` = RiskManager 五条硬规则的入口，
**不可绕过**。资金补足/理财扫归/台账留痕逻辑集中在此，两条路径调同一份代码 → 行为一致。
"""
from typing import Any

from loguru import logger

from common.market import CRYPTO
from common.market_time import market_today

# ──────────────────── 风控 broker_info + 连亏台账回放 ────────────────────


def get_recent_crypto_closed_pnls(limit: int = 10) -> tuple[list[float], str | None]:
    """最近 N 笔平仓盈亏（最新在前）+ 最近亏损日期。喂 RiskManager 的 ConsecutiveLossRule。

    **优先用币安原始成交明细 `crypto_fills` 回放**（`cost_basis.closed_pnls`）——那是交易所
    侧的客观事实，含 Jason 在币安 App 里手动做的买卖；`crypto_trades` 台账只记「本系统下的
    单」，拿它算连亏会漏掉大半，还会把「系统外买入、系统内卖出」算成 0 成本的暴利。

    明细尚未同步时退回旧的台账口径（向后兼容，不因为没同步就把风控变哑）。
    crypto 与股票各算各的 streak，互不污染。
    """
    from crypto_intel_engine import cost_basis as cb
    try:
        if cb.has_any_fills():
            return cb.closed_pnls(limit)
    except Exception as e:  # noqa: BLE001 — 回放失败退回台账，不让风控断供
        logger.warning(f"成交明细回放平仓盈亏失败，退回台账口径: {e}")
    return _closed_pnls_from_ledger(limit)


def _closed_pnls_from_ledger(limit: int = 10) -> tuple[list[float], str | None]:
    """旧口径：从 `crypto_trades` 台账回放（仅当成交明细未同步时使用）。"""
    from data_engine.storage.database import get_session
    from data_engine.storage.models import CryptoTrade
    session = get_session()
    try:
        trades = (session.query(CryptoTrade)
                  .order_by(CryptoTrade.trade_date.asc(), CryptoTrade.id.asc()).all())
        if not trades:
            return [], None
        positions: dict[str, dict[str, float]] = {}
        closed: list[dict[str, Any]] = []   # {"pnl": float, "date": str|None}
        for t in trades:
            pos = positions.setdefault(t.symbol,
                                       {"quantity": 0.0, "total_cost": 0.0, "avg_cost": 0.0})
            if t.side == "BUY":
                pos["total_cost"] += t.amount + (t.commission or 0)
                pos["quantity"] += t.quantity
                if pos["quantity"] > 0:
                    pos["avg_cost"] = pos["total_cost"] / pos["quantity"]
            elif t.side == "SELL":
                sell_revenue = t.amount - (t.commission or 0)
                pnl = sell_revenue - pos["avg_cost"] * t.quantity
                closed.append({"pnl": pnl,
                               "date": str(t.trade_date) if t.trade_date else None})
                pos["quantity"] -= t.quantity
                pos["total_cost"] = pos["avg_cost"] * pos["quantity"]
        closed.reverse()   # 最新在前
        pnl_list = [c["pnl"] for c in closed[:limit]]
        last_loss_date = next((c["date"] for c in closed if c["pnl"] < 0), None)
        return pnl_list, last_loss_date
    except Exception as e:  # noqa: BLE001
        logger.warning(f"crypto 平仓盈亏回放失败: {e}")
        return [], None
    finally:
        session.close()


def crypto_broker_info(broker) -> dict[str, Any]:
    """按币安真实余额自建 RiskManager 所需 broker_info（不复用 A 股 adapter）。

    连亏字段（recent_closed_pnls/last_loss_date）从 crypto 独立成交台账回放而来，
    使 CLAUDE.md「连续亏损 3 次暂停」硬风控对 crypto 真正生效（不再硬编码空值）。
    """
    acct = broker.get_account_info()
    positions_map = {
        p.symbol: {"market_value": p.market_value, "avg_cost": p.avg_cost,
                   "current_price": p.current_price,
                   "available": p.available, "wallet_breakdown": p.wallet_breakdown}
        for p in broker.get_positions()
    }
    recent_pnls, last_loss_date = get_recent_crypto_closed_pnls(limit=10)
    return {
        "cash": acct["cash"], "market_value": acct["market_value"],
        "total_value": acct["total_value"], "unrealized_pnl": acct["unrealized_pnl"],
        "positions": positions_map,
        "recent_closed_pnls": recent_pnls, "last_loss_date": last_loss_date,
    }


def crypto_risk_config(broker_info: dict) -> dict:
    """crypto 专用风控配置：**日亏阈值按币安真实总值算**，不用 A 股那份全局总资金。

    ⛔ 修的是一处单位串味：`RiskManager` 在只给 `max_daily_loss_pct` 时会自己去
    `get_total_capital()` 取阈值基数，而那读的是 `UserSettings.total_capital` ——
    **Jason 的 A 股总资金，单位人民币（默认 5000）**，却拿去和币安的 USDT 浮亏比大小。
    此前因为 `unrealized_pnl` 恒为 0 从没暴露；成本价一修好它立刻变成活的错误。

    这里预先把 `max_daily_loss`（绝对值）算好塞进配置，`RiskManager` 就会走
    「配置里有绝对值」的分支，不再回落到全局资金。共用的 `manager.py`/`rules.py` 一行不改
    （A 股路径零风险）。
    """
    from trading_engine.risk.adapter import get_effective_risk_config
    cfg = dict(get_effective_risk_config())
    pct = cfg.pop("max_daily_loss_pct", None)
    total = float((broker_info or {}).get("total_value") or 0.0)
    if pct and total > 0:
        cfg["max_daily_loss"] = total * float(pct)
    elif pct:
        # 读不到真实总值时**保留百分比**让 RiskManager 走旧路径，总比完全没有日亏闸好
        cfg["max_daily_loss_pct"] = pct
    return cfg


def risk_check(symbol: str, action: str, quantity: float, price: float,
               broker_info: dict) -> tuple[bool, list[str], list[str]]:
    """无条件过 RiskManager.check_order。返回 (是否全过, 全部消息, 未过规则消息)。"""
    from trading_engine.risk.manager import RiskManager
    rm = RiskManager(crypto_risk_config(broker_info))
    passed, results = rm.check_order(symbol=symbol, action=action,
                                     quantity=quantity, price=price, broker_info=broker_info)
    return passed, [r.message for r in results], [r.message for r in results if not r.passed]


def resolve_qty_price(symbol: str, side: str, quantity: float | None,
                      quote_amount: float | None, price: float | None,
                      broker) -> tuple[float, float]:
    """确定下单币量与参考价。quote_amount(USDT) 给了则按现价换算成币量。"""
    ref = price or broker.get_current_price(symbol)
    if quantity:
        return float(quantity), ref
    if quote_amount and ref:
        return float(quote_amount) / ref, ref
    return 0.0, ref


# ──────────────────── 资金腾挪：买入自动补足 + 卖后理财扫归 ────────────────────

STABLE_QUOTE = "USDT"     # v1 只处理 USDT 计价对
FUND_BUFFER = 1.003       # 补足现货时多留 0.3% 缓冲（覆盖费率/滑点微差）


def _auto_earn_enabled() -> bool:
    import os
    return os.getenv("CRYPTO_AUTO_EARN_ENABLED", "true").lower() not in ("false", "0", "off")


def _earn_dust_min() -> float:
    import os
    try:
        return float(os.getenv("CRYPTO_EARN_DUST_MIN", "1"))
    except ValueError:
        return 1.0


def _earn_assets() -> list[str]:
    import os
    raw = os.getenv("CRYPTO_EARN_ASSETS", "USDT")
    return [a.strip().upper() for a in raw.split(",") if a.strip()]


def plan_funding(acct: dict, need_usdt: float) -> tuple[list[dict], float]:
    """现货 USDT 不够时，规划从「活期→资金」按序补足的步骤。返回 (steps, 仍缺口)。

    优先赎回活期（我们自己扫进去的、更快），再划转资金钱包。缺口>0 = 补不满。
    """
    spot = float(acct.get("spot_cash") or 0.0)
    remaining = round(max(0.0, need_usdt - spot), 2)
    steps: list[dict] = []
    if remaining <= 0:
        return steps, 0.0
    redeemable = float(acct.get("redeemable_cash") or 0.0)
    if remaining > 0 and redeemable > 0:
        amt = round(min(remaining, redeemable), 2)
        steps.append({"action": "redeem", "from": "理财活期", "asset": STABLE_QUOTE, "amount": amt})
        remaining = round(remaining - amt, 2)
    transferable = float(acct.get("transferable_cash") or 0.0)
    if remaining > 0 and transferable > 0:
        amt = round(min(remaining, transferable), 2)
        steps.append({"action": "transfer", "from": "资金钱包", "asset": STABLE_QUOTE, "amount": amt})
        remaining = round(remaining - amt, 2)
    return steps, round(max(0.0, remaining), 2)


def execute_funding(broker, steps: list[dict], need_usdt: float) -> bool:
    """执行补足步骤（赎回/划转），到账有延迟 → 轮询最多 3 次确认现货够了才算成功。"""
    import time

    from acquisition.markets import binance_trade as bt
    for st in steps:
        ok = (broker.earn_redeem_flexible(st["asset"], st["amount"]) if st["action"] == "redeem"
              else broker.transfer_funding_to_spot(st["asset"], st["amount"]))
        if not ok:
            logger.error(f"补足现货步骤失败: {st}")
            return False
    # 轮询现货 USDT 到账（币安划转/赎回有秒级延迟）
    for _ in range(3):
        time.sleep(1.2)
        try:
            free = next((float(b.get("free", 0)) for b in bt.account().get("balances", [])
                         if b.get("asset") == STABLE_QUOTE), 0.0)
        except Exception:  # noqa: BLE001
            free = 0.0
        if free >= need_usdt:
            return True
    logger.warning(f"补足后现货 {STABLE_QUOTE} 仍不足 {need_usdt}")
    return False


def ensure_spot_for_sell(broker, symbol: str, qty: float) -> tuple[bool, str | None]:
    """卖出前保证现货够卖：现货可用量 < qty 时，先从**活期理财**赎回、再从**资金钱包**划转，
    轮询到账。

    对称于买入侧 `plan_funding`/`execute_funding`（买补 USDT，卖补待卖币）。币安「自动申购」
    把币放进活期理财、跨钱包转账把币留在资金钱包，两处都算进 `BrokerPosition.quantity`
    这个总敞口 —— **只赎理财是补不齐的**：任何有资金钱包余额的持仓都会卡在这一步，
    退出/止损被永久堵死。故两条补足路径都必须走。

    ⛔ 补不齐时**拒单**，不部分卖、不替 Jason 撤挂单：挂单锁定的币（`spot_locked`）既卖不掉
    也变不出来，唯一出路是他自己去币安撤单——系统不替他做主。

    Returns:
        `(ok, reason)`。ok=True 时 reason=None；False 时 reason 是给 Jason 看的人话
        （还差多少、卡在哪个钱包），由上层直接放进拒单理由。
    """
    import time

    from acquisition.markets import binance_trade as bt
    from common.market import to_binance_symbol

    try:
        pos = broker.get_position(symbol)
    except Exception as e:  # noqa: BLE001
        logger.warning(f"卖前查持仓失败 {symbol}: {e}")
        pos = None
    available = float(getattr(pos, "available", 0.0) or 0.0) if pos else 0.0
    if available >= qty:
        return True, None   # 现货本就够卖，无需腾挪

    breakdown = (getattr(pos, "wallet_breakdown", None) or {}) if pos else {}
    earn_qty = float(breakdown.get("earn_flexible", 0.0) or 0.0)
    funding_qty = float(breakdown.get("funding", 0.0) or 0.0)
    locked_qty = float(breakdown.get("spot_locked", 0.0) or 0.0)
    shortfall = qty - available

    # 计价币名（symbol 形如 BTCUSDT.BN → base=BTC）：剥掉尾部 USDT 计价后缀
    bn = to_binance_symbol(symbol)
    base = bn[:-len(STABLE_QUOTE)] if bn.endswith(STABLE_QUOTE) else bn

    def _stuck(extra: str) -> str:
        parts = [f"卖前现货不足：需 {qty:g} {base}，现货可用 {available:g}，缺 {shortfall:g}"]
        if locked_qty > 0:
            parts.append(f"其中 {locked_qty:g} 被挂单锁定（需你自己在币安撤单）")
        parts.append(extra)
        return "；".join(parts)

    if earn_qty <= 0 and funding_qty <= 0:
        msg = _stuck("理财与资金钱包均无可腾挪余额")
        logger.warning(msg)
        return False, msg

    # ① 先赎活期理财（我们自己扫进去的，最快），② 再划转资金钱包 —— 与 plan_funding 同序
    remaining = shortfall
    if earn_qty > 0 and remaining > 0:
        amt = round(min(remaining, earn_qty), 8)
        if not broker.earn_redeem_flexible(base, amt):
            msg = _stuck(f"从活期理财赎回 {amt:g} 失败")
            logger.error(msg)
            return False, msg
        remaining = round(remaining - amt, 8)
    if funding_qty > 0 and remaining > 0:
        amt = round(min(remaining, funding_qty), 8)
        if not broker.transfer_funding_to_spot(base, amt):
            msg = _stuck(f"从资金钱包划转 {amt:g} 失败")
            logger.error(msg)
            return False, msg

    # 轮询现货该币到账（币安赎回/划转秒级延迟），镜像 execute_funding
    for _ in range(3):
        time.sleep(1.2)
        try:
            free = next((float(b.get("free", 0)) for b in bt.account().get("balances", [])
                         if b.get("asset") == base), 0.0)
        except Exception:  # noqa: BLE001
            free = 0.0
        if free >= qty:
            return True, None
    msg = _stuck("腾挪后现货仍不足（到账延迟或余额被占用）")
    logger.warning(msg)
    return False, msg


def auto_sweep_to_earn(broker) -> None:
    """卖出成交后：把现货**闲置稳定币**全自动申购最优活期理财吃收益（不弹确认）。

    Jason 批准全自动（活期可秒赎回、风险低）。留痕但**不进 CryptoTrade 台账、不算连亏
    streak**（申赎不是买卖交易）。失败仅日志，绝不回滚已成交的卖单。
    """
    if not _auto_earn_enabled():
        return
    dust = _earn_dust_min()
    try:
        acct = broker.get_account_info()
    except Exception as e:  # noqa: BLE001
        logger.warning(f"扫归读账户失败: {e}")
        return
    # 现货可用稳定币（spot_cash 只含稳定币 free，正是卖出到账的闲置资金）
    idle = float(acct.get("spot_cash") or 0.0)
    if idle < dust:
        return
    for asset in _earn_assets():
        if asset != STABLE_QUOTE:      # v1 仅扫 USDT；spot_cash 未按币种拆分
            continue
        amount = round(idle, 2)
        if amount < dust:
            continue
        if broker.earn_subscribe_flexible(asset, amount):
            record_earn_sweep(asset, amount)


def record_earn_sweep(asset: str, amount: float) -> None:
    """理财扫归留痕：business_event + DecisionLog 备注。**非交易**，不进 CryptoTrade 台账。"""
    try:
        from business_events import EARN_SWEEP, publish_event
        publish_event(EARN_SWEEP, source="crypto",
                      title=f"闲置 {amount:g} {asset} 自动申购活期理财（吃收益）",
                      asset=asset, amount=amount)
    except Exception:  # noqa: BLE001
        pass
    try:
        from decision_log import record_decision
        record_decision(source="crypto_earn", symbol=f"{asset}.EARN", action="SUBSCRIBE",
                        executed=True, risk_passed=True,
                        output_text=f"闲置 {amount:g} {asset} 自动申购活期理财")
    except Exception:  # noqa: BLE001
        pass


# ──────────────────── 成交/挂单留痕 ────────────────────


def record_crypto_trade(symbol: str, action: str, price: float, qty: float,
                        order_id: str, commission: float = 0.0) -> None:
    """**真实成交**留痕：成交台账 + 业务事件 + DecisionLog(source=crypto)。吞异常不坏主流程。

    只在 filled_quantity > 0 时调用（qty 为已成交量）。台账（CryptoTrade）喂连亏风控回放；
    commission 取自币安 fills 汇总（手续费币种可能非 USDT，回放里作近似处理）。
    """
    # ① 成交台账（连亏风控的回放数据源）
    try:

        from data_engine.storage.database import get_session
        from data_engine.storage.models import CryptoTrade
        session = get_session()
        try:
            session.add(CryptoTrade(
                symbol=symbol, side=action, price=price, quantity=qty,
                amount=round(price * qty, 8), commission=commission or 0.0,
                # 交易日按 **crypto 市场日**（UTC）—— 要和 `crypto_fills` 回放出的
                # `closed[].date`、以及引擎的当日熔断/费用窗口对得上
                order_id=order_id, trade_date=market_today(CRYPTO),
            ))
            session.commit()
        finally:
            session.close()
    except Exception as e:  # noqa: BLE001
        logger.warning(f"crypto 成交台账写入失败: {e}")
    # ② 业务事件
    try:
        from business_events import ORDER_FILLED, publish_event
        publish_event(ORDER_FILLED, source="crypto", symbol=symbol,
                      title=f"{'买入' if action == 'BUY' else '卖出'} {symbol} {qty:g} @ ${price:,.2f}（币安实盘）",
                      action=action, price=round(price, 4), quantity=qty,
                      amount=round(price * qty, 2))
    except Exception:  # noqa: BLE001
        pass
    # ③ 决策留痕
    try:
        from decision_log import record_decision
        record_decision(source="crypto", symbol=symbol, action=action,
                        entry_price=price, executed=True, risk_passed=True,
                        output_text=f"币安现货成交 order={order_id} {qty:g} @ ${price:,.4f}")
    except Exception as e:  # noqa: BLE001
        logger.warning(f"crypto 决策留痕失败: {e}")


def record_crypto_resting(symbol: str, action: str, price: float | None, order_id: str,
                          order_type: str = "LIMIT") -> None:
    """订单已受理但**零成交**的留痕：只写 DecisionLog(executed=False)。

    不发 ORDER_FILLED、不落成交台账 —— 未成交不是成交，成交后自然由 record_crypto_trade 记。

    Args:
        order_type: `LIMIT` = 限价单挂在盘口等撮合（正常状态）；`MARKET` = **市价单却零成交**，
            这不正常（薄盘/交易对暂停/被撮合引擎拒），文案必须区分开——否则待确认单路径下的
            市价单会被记成「限价单挂出，等待撮合」，Jason 按这句话去等一张根本不存在的挂单。
    """
    note = ("币安限价单挂出（未成交，盘口等待撮合）" if order_type.upper() == "LIMIT"
            else "币安市价单零成交（异常：薄盘/交易对暂停/被拒），请去币安核对")
    try:
        from decision_log import record_decision
        record_decision(source="crypto", symbol=symbol, action=action,
                        entry_price=price, executed=False, risk_passed=True,
                        output_text=f"{note} order={order_id}")
    except Exception as e:  # noqa: BLE001
        logger.warning(f"crypto 挂单留痕失败: {e}")

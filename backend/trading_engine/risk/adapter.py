"""
风控适配层 — 桥接 PortfolioCalculator 和 RiskManager 之间的数据格式

用于在交易录入时构建 RiskManager 所需的 broker_info 字典。
"""
from typing import List, Dict, Any, Optional
from datetime import date

from loguru import logger

from data_engine.storage.database import get_session
from data_engine.storage.models import ManualTrade, UserSettings
from portfolio.calculator import PortfolioCalculator
from trading_engine.config import RISK_CONFIG


def get_total_capital() -> float:
    """从 UserSettings 表读取总资金，默认 5000"""
    session = get_session()
    try:
        row = session.query(UserSettings).filter(UserSettings.key == "total_capital").first()
        if row and row.value:
            return float(row.value)
        return 5000.0
    except Exception as e:
        logger.warning(f"读取总资金设置失败，使用默认值: {e}")
        return 5000.0
    finally:
        session.close()


def get_max_position_pct() -> float:
    """从 UserSettings 读取单股最大仓位占比（集中度），默认 0.5。范围夹到 (0, 1]。"""
    default = 0.5
    session = get_session()
    try:
        row = session.query(UserSettings).filter(UserSettings.key == "max_position_pct").first()
        if row and row.value:
            v = float(row.value)
            if v > 0:
                return min(v, 1.0)
        return default
    except Exception as e:
        logger.warning(f"读取集中度设置失败，使用默认值: {e}")
        return default
    finally:
        session.close()


def get_effective_risk_config() -> Dict[str, Any]:
    """
    返回生效的风控配置：在 RISK_CONFIG 基础上用用户设置覆盖单股集中度。
    单股上限设大时，总仓位上限同步抬到 ≥ 单股上限（避免 all-in 100% 误报）。
    """
    cfg = dict(RISK_CONFIG)
    pct = get_max_position_pct()
    cfg["max_position_pct"] = pct
    cfg["max_total_position_pct"] = max(cfg.get("max_total_position_pct", 0.8), pct)
    return cfg


def get_recent_closed_pnls(limit: int = 10) -> tuple[List[float], Optional[str]]:
    """
    计算最近 N 笔平仓交易的盈亏列表（最近的排前面）

    Returns:
        (pnl_list, last_loss_date):
        - pnl_list: 最近平仓的盈亏列表，最新在前
        - last_loss_date: 最近一笔亏损交易的日期字符串
    """
    session = get_session()
    try:
        trades = (
            session.query(ManualTrade)
            .order_by(ManualTrade.trade_date.asc(), ManualTrade.id.asc())
            .all()
        )

        if not trades:
            return [], None

        # 回放交易，记录每笔 SELL 的盈亏
        positions: Dict[str, Dict[str, float]] = {}
        closed_pnls: List[Dict[str, Any]] = []  # {"pnl": float, "date": str}

        for t in trades:
            sym = t.symbol
            if sym not in positions:
                positions[sym] = {"quantity": 0, "total_cost": 0.0, "avg_cost": 0.0}

            pos = positions[sym]

            if t.side == "BUY":
                new_cost = t.amount + (t.commission or 0)
                pos["total_cost"] += new_cost
                pos["quantity"] += t.quantity
                if pos["quantity"] > 0:
                    pos["avg_cost"] = pos["total_cost"] / pos["quantity"]

            elif t.side == "SELL":
                sell_revenue = t.amount - (t.commission or 0)
                cost_basis = pos["avg_cost"] * t.quantity
                pnl = sell_revenue - cost_basis
                closed_pnls.append({
                    "pnl": pnl,
                    "date": str(t.trade_date) if t.trade_date else None,
                })
                pos["quantity"] -= t.quantity
                pos["total_cost"] = pos["avg_cost"] * pos["quantity"]

        # 按时间倒序（最近的在前）
        closed_pnls.reverse()

        pnl_list = [cp["pnl"] for cp in closed_pnls[:limit]]

        # 找最近一笔亏损的日期
        last_loss_date = None
        for cp in closed_pnls:
            if cp["pnl"] < 0:
                last_loss_date = cp["date"]
                break

        return pnl_list, last_loss_date

    except Exception as e:
        logger.error(f"计算平仓盈亏列表失败: {e}")
        return [], None
    finally:
        session.close()


def build_broker_info(total_capital: Optional[float] = None) -> Dict[str, Any]:
    """
    构建 RiskManager.check_order 所需的 broker_info 字典

    Args:
        total_capital: 总资金，None 则从 UserSettings 读取

    Returns:
        broker_info 字典
    """
    if total_capital is None:
        total_capital = get_total_capital()

    calculator = PortfolioCalculator()
    positions = calculator.get_current_positions()

    # 总持仓市值
    market_value = sum(
        p["market_value"] for p in positions if p["market_value"] is not None
    )

    # 总未实现盈亏
    unrealized_pnl = sum(
        p["unrealized_pnl"] for p in positions if p["unrealized_pnl"] is not None
    )

    # 可用现金 = 总资金 - 持仓成本
    total_cost = sum(p["total_cost"] for p in positions)
    cash = total_capital - total_cost

    # 按 symbol 索引持仓
    positions_map = {p["symbol"]: p for p in positions}

    # 最近平仓盈亏
    recent_pnls, last_loss_date = get_recent_closed_pnls(limit=10)

    return {
        "cash": cash,
        "market_value": market_value,
        "total_value": total_capital,
        "unrealized_pnl": unrealized_pnl,
        "positions": positions_map,
        "recent_closed_pnls": recent_pnls,
        "last_loss_date": last_loss_date,
    }

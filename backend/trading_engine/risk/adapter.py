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


def get_max_total_position_pct() -> float:
    """总仓位上限 —— **独立硬底线，只从 `RISK_CONFIG` 读，永不被任何设置抬高**。

    🔒 这是 2026-07-27 定位变更后仍然保留的四条硬风控之一（「总持仓 ≤ 80%，
    必须保留 20% 现金」）。它**不读 `UserSettings`** 是刻意的：`UserSettings` 是
    Jason 在设置页能改的东西，而这条线不该被改软；`agents/tools/settings_tools.py`
    的 `_RISK_READONLY_KEYS` 也把它对 MoneyBill 锁死了。

    ⛔ 别再让它跟单股上限联动（见 `get_effective_risk_config` 的注释）。
    """
    return float(RISK_CONFIG.get("max_total_position_pct", 0.80))


def get_max_position_pct() -> float:
    """单股最大仓位占比（集中度），读 UserSettings，**再夹到总仓位上限之内**。

    兜底默认 0.20 对齐旧风控规格：用户没有显式设置时取最严值；显式设置（含放宽
    到 0.5/1.0）仍被尊重——那是 Jason 在设置页的主动选择。

    🔄 **2026-07-27 定位变更**：单笔交易金额「无上限」，约束单笔的是硬风控而不是
    单股天花板。所以这里把上界从 1.0 改成 `get_max_total_position_pct()`（0.8）：
    设成 1.0 时单股上限失去约束力（等于取消），但**买满也只到 80%，20% 现金照留**。
    ⛔ 反过来拿单股上限去抬总仓位上限是错的，那会静默撤销现金保护。
    """
    default = 0.20
    ceiling = get_max_total_position_pct()
    session = get_session()
    try:
        row = session.query(UserSettings).filter(UserSettings.key == "max_position_pct").first()
        if row and row.value:
            v = float(row.value)
            if v > 0:
                return min(v, ceiling)
        return min(default, ceiling)
    except Exception as e:
        logger.warning(f"读取集中度设置失败，使用默认值: {e}")
        return min(default, ceiling)
    finally:
        session.close()


def get_effective_risk_config() -> Dict[str, Any]:
    """返回生效的风控配置：在 RISK_CONFIG 基础上用用户设置覆盖单股集中度。

    🔴 **2026-07-27 修正（S0 §1.3）**：旧实现是
    `cfg["max_total_position_pct"] = max(默认 0.8, 单股上限)` ——
    Jason 一旦按新规则把 `max_position_pct` 调到 1.0（取消单笔上限），总仓位上限
    就被顶成 1.0，**20% 现金保护被静默撤销**。而「单笔无上限 + 必须留 20% 现金」
    恰恰是新口径里被特意叮嘱「别记错」的那个组合。

    现在方向反过来：总仓位上限是独立硬底线，单股上限受它 `min` 约束
    （夹取动作收在 `get_max_position_pct()` 里，单一收口点）。
    门禁：`tests/test_risk_total_position_floor.py`。
    """
    cfg = dict(RISK_CONFIG)
    cfg["max_total_position_pct"] = get_max_total_position_pct()
    cfg["max_position_pct"] = get_max_position_pct()
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

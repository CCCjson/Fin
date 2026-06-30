"""
仓位计算 — 把「建议占总资金 %」换算成基于真实资金的具体金额 / 股数。

被决策驾驶舱(cockpit)、选股器(screener)、自选股预警(watchlist) 共用。
所有建议金额硬性受三重约束：
  1. 目标仓位金额 = target_pct% × 总资金
  2. 单股上限     = RISK_CONFIG["max_position_pct"] × 总资金（默认 20%）
  3. 可用现金     = broker_info["cash"]
取整到 A股 1 手 = 100 股；买不起 1 手 → affordable=False。
最后再过一遍 RiskManager(单股20%/总仓80%/连亏暂停 等) 作硬校验。
"""
from typing import Dict, List, Optional

from loguru import logger

from trading_engine.risk.adapter import (
    build_broker_info,
    get_effective_risk_config,
    get_max_position_pct,
    get_total_capital,
)
from trading_engine.risk.manager import RiskManager

A_SHARE_LOT = 100  # A股最小买入单位 1 手 = 100 股

# 按集中度 pct 缓存 RiskManager（规则无状态，避免选股器逐只重建）
_risk_managers: Dict[float, RiskManager] = {}


def _get_risk_manager(pct: float) -> RiskManager:
    key = round(pct, 4)
    rm = _risk_managers.get(key)
    if rm is None:
        cfg = get_effective_risk_config()
        cfg["max_position_pct"] = pct
        cfg["max_total_position_pct"] = max(cfg.get("max_total_position_pct", 0.8), pct)
        rm = RiskManager(cfg)
        _risk_managers[key] = rm
    return rm


def size_position(
    symbol: str,
    price: Optional[float],
    target_pct: float,
    *,
    broker_info: Optional[Dict] = None,
    total_capital: Optional[float] = None,
    max_position_pct: Optional[float] = None,
    lot: int = A_SHARE_LOT,
) -> Dict:
    """
    按真实资金把「目标仓位 %」换算成可执行的金额 / 股数。

    Args:
        symbol: 股票代码
        price: 最新价（无价无法计算）
        target_pct: 目标买入仓位（占总资金 %，0-100）
        broker_info: 复用已构建的 broker_info；None 则按 total_capital 现算
        total_capital: 总资金；None 则读 UserSettings(默认值见 get_total_capital)
        lot: 最小买入单位（A股 100）

    Returns:
        {affordable, shares, lots, amount, target_pct, total_capital,
         available_cash, max_single_amount, capped_by, risk_passed, warnings}
    """
    if total_capital is None:
        total_capital = get_total_capital()
    if broker_info is None:
        broker_info = build_broker_info(total_capital)
    if max_position_pct is None:
        max_position_pct = get_max_position_pct()

    available_cash = float(broker_info.get("cash") or 0.0)
    max_pct = float(max_position_pct)
    max_single_amount = round(max_pct * total_capital, 2)

    warnings: List[str] = []
    result = {
        "target_pct": round(target_pct, 1),
        "total_capital": round(total_capital, 2),
        "available_cash": round(available_cash, 2),
        "max_single_amount": max_single_amount,
        "shares": 0,
        "lots": 0,
        "amount": 0.0,
        "affordable": False,
        "risk_passed": False,
        "capped_by": None,
        "warnings": warnings,
    }

    if not price or price <= 0:
        warnings.append("无最新价，无法计算建议股数")
        return result
    if target_pct <= 0:
        result["capped_by"] = "score"  # 评分不建议买入
        return result

    one_lot_cost = price * lot
    target_amount = target_pct / 100.0 * total_capital
    # 硬上限 = 单股 20% 与可用现金中的较小者
    hard_cap = min(max_single_amount, available_cash)
    max_lots_hard = int(hard_cap // one_lot_cost)

    if max_lots_hard < 1:
        # 连 1 手都买不起（含「股价过高超 20% 上限」与「现金不足」两种）
        result["capped_by"] = "max_position_pct" if max_single_amount <= available_cash else "cash"
        warnings.append(
            f"买不起 1 手：1 手需 ¥{one_lot_cost:,.0f}，"
            f"单股上限 ¥{max_single_amount:,.0f} / 可用现金 ¥{available_cash:,.0f}"
        )
        return result

    # 目标驱动手数；不足 1 手则按最小 1 手（仍在硬上限内）
    target_lots = int(min(target_amount, hard_cap) // one_lot_cost)
    if target_lots >= 1:
        suggested_lots = target_lots
        if target_amount <= max_single_amount and target_amount <= available_cash:
            result["capped_by"] = "target"
        elif max_single_amount <= available_cash:
            result["capped_by"] = "max_position_pct"
        else:
            result["capped_by"] = "cash"
    else:
        suggested_lots = 1
        result["capped_by"] = "min_lot"
        warnings.append("目标仓位不足 1 手，已按最小 1 手建议")

    shares = suggested_lots * lot
    amount = round(shares * price, 2)
    result.update({"shares": shares, "lots": suggested_lots, "amount": amount, "affordable": True})

    # 硬约束：过一遍 RiskManager（单股集中度/总仓/连亏暂停 等）
    try:
        passed, checks = _get_risk_manager(max_pct).check_order(
            symbol=symbol, action="BUY", quantity=shares, price=price, broker_info=broker_info
        )
        result["risk_passed"] = passed
        for c in checks:
            if not c.passed:
                warnings.append(f"[{c.severity}] {c.message}")
    except Exception as e:  # 风控异常不应阻断展示，但要标红
        logger.warning(f"size_position 风控校验异常 {symbol}: {e}")
        result["risk_passed"] = False
        warnings.append(f"风控校验异常: {e}")

    return result

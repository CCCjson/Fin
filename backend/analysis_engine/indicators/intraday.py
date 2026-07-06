"""
盘中量能 / 分时VWAP / 交易时段 —— 个股盘中体检用的底层计算。

这里放「时点」类的纯计算/取数逻辑（A股全天 240 分钟交易时段口径），
供 agents.tools.intraday_tools 的薄适配层调用；工具层只做数据编排与展示，
判定阈值与计算公式集中在引擎层，避免散落在工具文件里。
"""
from typing import Optional

from loguru import logger


def elapsed_trading_minutes(now) -> int:
    """今日已走过的交易分钟数（9:30-11:30 + 13:00-15:00，全天 240）。"""
    minutes = now.hour * 60 + now.minute
    morning = max(0, min(minutes - (9 * 60 + 30), 120))
    afternoon = max(0, min(minutes - 13 * 60, 120))
    return morning + afternoon


def avg5_volume(symbol: str, today) -> Optional[float]:
    """近 5 个交易日的日均成交量（手，东财日线口径）。"""
    from data_engine.storage.database import get_session
    from data_engine.storage.models import DailyQuote
    session = get_session()
    try:
        rows = (session.query(DailyQuote.volume)
                .filter(DailyQuote.symbol == symbol, DailyQuote.date < today)
                .order_by(DailyQuote.date.desc())
                .limit(5).all())
        vols = [float(r[0]) for r in rows if r[0]]
        return sum(vols) / len(vols) if vols else None
    except Exception as e:  # noqa: BLE001
        logger.warning(f"读取 {symbol} 近5日均量失败: {e}")
        return None
    finally:
        session.close()


def intraday_vwap(df, current_price: float) -> Optional[float]:
    """分时均价 = 累计成交额 / 累计成交股数。

    pytdx 分钟线 vol 的单位（股/手）在不同市场历史上有出入，用现价做量级
    校验自适应：原始结果偏离现价 100 倍量级时按「手→股」修正。
    """
    if df.empty or "amount" not in df.columns or not current_price:
        return None
    vol_sum = float(df["volume"].sum())
    amt_sum = float(df["amount"].sum())
    if vol_sum <= 0 or amt_sum <= 0:
        return None
    raw = amt_sum / vol_sum
    for candidate in (raw, raw / 100.0):
        if 0.5 * current_price <= candidate <= 2.0 * current_price:
            return round(candidate, 3)
    return None

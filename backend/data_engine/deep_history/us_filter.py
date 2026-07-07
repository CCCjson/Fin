"""
美股正股/ETF vs 债券票据/杠杆ETF 粗分类 —— 深历史回补只处理正股+ETF。

分类结果落到 StockInfo.stock_type（该字段此前从未被港美股写入路径设置过），
方便审计/复用，也让"重新分类"变成一次幂等的本地计算，不需要重新请求数据源。
"""
import re
from typing import Dict

from loguru import logger

from data_engine.storage.database import get_session
from data_engine.storage.models import StockInfo

# 命中 AAPL22 / GS26 这类"字母+2位年份数字"结尾的债券/票据代码；
# 不会误伤 BRK-B / BF.B 这类用 - 或 . 分隔的合法股票代码
BOND_NOTE_PATTERN = re.compile(r"^[A-Z]{1,6}\d{2}$")

# 杠杆/反向 ETF 关键词（中英文名称 + 主流发行商），命中任一即排除
LEVERAGE_KEYWORDS = [
    "做多", "做空", "杠杆", "反向", "2倍", "3倍", "两倍", "三倍", "加速",
    "Bull", "Bear", "Ultra", "Inverse", "Leveraged", "2X", "3X", "-1X", "1.5X",
    "Direxion", "ProShares", "GraniteShares", "MicroSectors", "YieldMax",
]

ETF_KEYWORDS = ["ETF", "Trust", "Fund", "Index"]

STOCK_TYPE_STOCK = "stock"
STOCK_TYPE_ETF = "etf"
STOCK_TYPE_EXCLUDED_BOND_NOTE = "excluded_bond_note"
STOCK_TYPE_EXCLUDED_LEVERAGED_ETF = "excluded_leveraged_etf"


def classify_us_symbol(symbol: str, name: str) -> str:
    """单个美股代码+名称 -> 分类结果"""
    if BOND_NOTE_PATTERN.match(symbol):
        return STOCK_TYPE_EXCLUDED_BOND_NOTE
    name = name or ""
    if any(kw in name for kw in LEVERAGE_KEYWORDS):
        return STOCK_TYPE_EXCLUDED_LEVERAGED_ETF
    if any(kw in name for kw in ETF_KEYWORDS):
        return STOCK_TYPE_ETF
    return STOCK_TYPE_STOCK


def classify_and_persist_us_universe() -> Dict[str, int]:
    """对 StockInfo 里全部 us_stock 重新分类并写回 stock_type，返回各分类计数。

    幂等：纯本地正则/关键词匹配，重复调用不会产生副作用，分类规则更新后
    重新跑一次即可刷新全部结果。
    """
    session = get_session()
    counts = {STOCK_TYPE_STOCK: 0, STOCK_TYPE_ETF: 0,
              STOCK_TYPE_EXCLUDED_BOND_NOTE: 0, STOCK_TYPE_EXCLUDED_LEVERAGED_ETF: 0}
    try:
        rows = session.query(StockInfo).filter(StockInfo.market == "us_stock").all()
        for row in rows:
            stype = classify_us_symbol(row.symbol, row.name)
            row.stock_type = stype
            counts[stype] += 1
        session.commit()
        logger.info(f"美股分类完成: {counts}")
    finally:
        session.close()
    return counts

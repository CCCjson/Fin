"""
涨跌停阈值判定 —— 按市场板块 + 是否 ST 区分，替代项目里此前 compute_statistics()
用单一 9.9 阈值判断全市场涨停的简化做法（对创业板/科创板/ST 不准确）。

覆盖范围（MVP 明确排除北交所，降低复杂度，见方案「需要确认/实测的风险点」第4条）：
- 主板（沪深 60/00 开头，不含ST）：10%
- 创业板（30 开头）/科创板（688 开头）：20%（无论是否 ST，注册制下不下调）
- 主板 ST/*ST：5%
- 北交所（8/4/92 开头）：不支持，返回 None
"""
from typing import Optional


def _code_only(symbol: str) -> str:
    """"600519.SH" / "600519" 都能处理，取纯 6 位代码。"""
    return symbol.split(".")[0].strip()


def get_board_type(symbol: str) -> str:
    """返回 main(主板) / gem(创业板) / star(科创板) / bse(北交所，本功能不支持) / unknown。"""
    code = _code_only(symbol)
    if code.startswith("688"):
        return "star"
    if code.startswith(("300", "301")):
        return "gem"
    if code.startswith(("8", "4", "92")):
        return "bse"
    if code.startswith(("60", "00")):
        return "main"
    return "unknown"


def is_st(name: Optional[str]) -> bool:
    if not name:
        return False
    upper = name.upper()
    return "ST" in upper


def get_limit_threshold(symbol: str, name: Optional[str] = None) -> Optional[float]:
    """返回该股票的涨跌停幅度阈值（%），北交所/未知板块返回 None（本功能不覆盖）。"""
    board = get_board_type(symbol)
    if board in ("bse", "unknown"):
        return None
    if board in ("gem", "star"):
        return 19.9
    if is_st(name):
        return 4.9
    return 9.9


def is_limit_up(symbol: str, change_pct: Optional[float], name: Optional[str] = None) -> bool:
    threshold = get_limit_threshold(symbol, name)
    if threshold is None or change_pct is None:
        return False
    return change_pct >= threshold


def is_limit_down(symbol: str, change_pct: Optional[float], name: Optional[str] = None) -> bool:
    threshold = get_limit_threshold(symbol, name)
    if threshold is None or change_pct is None:
        return False
    return change_pct <= -threshold

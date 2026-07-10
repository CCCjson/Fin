"""
涨跌停阈值判定 —— 按市场板块 + 是否 ST 区分。全项目唯一实现，勿再手写 9.9。

住在 `common/`（基础层）而非 `limit_up_engine/`，因为 `data_engine.fetchers.realtime`
与 `report_engine` 也要用它，而依赖只许向下（CODING_STANDARDS §0）。

覆盖范围（MVP 明确排除北交所，降低复杂度，见方案「需要确认/实测的风险点」第4条）：
- 主板（沪深 60/00 开头，不含ST）：10%
- 创业板（30 开头）/科创板（688 开头）：20%（无论是否 ST，注册制下不下调）
- 主板 ST/*ST：5%
- 北交所（8/4/92 开头）：不支持，返回 None
"""


def _code_only(symbol: str) -> str:
    """"600519.SH" / "600519" 都能处理，取纯 6 位代码。"""
    return symbol.split(".")[0].strip()


def get_board_type(symbol: str) -> str:
    """返回 main(主板) / gem(创业板) / star(科创板) / bse(北交所，本功能不支持) / unknown。

    用 **2 位前缀**而非枚举 3 位段——同 `common/market.py::_STOCK_RULES` 的教训：
    3 位段会漏掉后开的代码段。实测漏网：`302132` 中航成飞（创业板）、`689xxx`
    九号公司（科创板），它们曾双双落进 unknown → 阈值 None → **永远判不出涨停**。
    """
    code = _code_only(symbol)
    if code.startswith("68"):          # 688xxx / 689xxx
        return "star"
    if code.startswith("30"):          # 300xxx / 301xxx / 302xxx
        return "gem"
    if code.startswith(("8", "4", "92")):
        return "bse"
    if code.startswith(("60", "00")):
        return "main"
    return "unknown"


def is_st(name: str | None) -> bool:
    if not name:
        return False
    upper = name.upper()
    return "ST" in upper


def get_limit_threshold(symbol: str, name: str | None = None) -> float | None:
    """返回该股票的涨跌停幅度阈值（%），北交所/未知板块返回 None（本功能不覆盖）。"""
    board = get_board_type(symbol)
    if board in ("bse", "unknown"):
        return None
    if board in ("gem", "star"):
        return 19.9
    if is_st(name):
        return 4.9
    return 9.9


def is_limit_up(symbol: str, change_pct: float | None, name: str | None = None) -> bool:
    threshold = get_limit_threshold(symbol, name)
    if threshold is None or change_pct is None:
        return False
    return change_pct >= threshold


def is_limit_down(symbol: str, change_pct: float | None, name: str | None = None) -> bool:
    threshold = get_limit_threshold(symbol, name)
    if threshold is None or change_pct is None:
        return False
    return change_pct <= -threshold

"""
组合类工具 —— 当前持仓、组合绩效。
"""
from agents.registry import tool

_calc = None


def _get_calc():
    global _calc
    if _calc is None:
        from portfolio.calculator import PortfolioCalculator
        _calc = PortfolioCalculator()
    return _calc


@tool(
    name="get_positions",
    description="获取用户当前所有持仓（代码、名称、数量、成本均价、已实现盈亏）。回答「我现在持有什么/仓位多少」时用。",
    parameters={"type": "object", "properties": {}},
    category="portfolio",
)
def get_positions() -> dict:
    from agents.widgets import position_table_widget
    positions = _get_calc().get_current_positions()
    held = [p for p in positions if p.get("quantity", 0) > 0]
    out = {"summary": {"count": len(held), "positions": held}}
    if held:
        out["widget"] = position_table_widget(held)
    return out


@tool(
    name="get_performance",
    description="获取组合整体绩效统计（总收益、胜率、盈亏比等）。回答「我赚了多少/表现如何」时用。",
    parameters={"type": "object", "properties": {}},
    category="portfolio",
)
def get_performance() -> dict:
    from agents.widgets import performance_metric_cards
    stats = _get_calc().get_performance_stats()
    return {"summary": stats, "widget": performance_metric_cards(stats)}

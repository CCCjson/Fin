"""
Widget 构造器 —— 把引擎原始 dict 映射成前端可渲染的结构化卡片/图表。

信封：{"type": ..., "title": ..., "data": {...}}
前端 WidgetRenderer 按 type 分发到对应组件（复用现成图表/卡片）。
"""
from typing import Optional


def cockpit_widget(r: dict) -> dict:
    """五维体检 → cockpit_score widget（复用 Cockpit 页 DimensionBar 风格）。"""
    dims = r.get("dimensions", {})
    return {
        "type": "cockpit_score",
        "title": f"{r.get('name') or r.get('symbol')} · 五维体检",
        "data": {
            "symbol": r.get("symbol"),
            "name": r.get("name"),
            "composite": r.get("composite"),
            "recommendation": r.get("recommendation"),
            "price": (r.get("price") or {}).get("latest"),
            "suggested_position_pct": r.get("suggested_position_pct"),
            "stop_loss": r.get("stop_loss"),
            "valuation": r.get("valuation"),
            "current_position": r.get("current_position"),
            "suggested": r.get("suggested"),
            "total_capital": r.get("total_capital"),
            "available_cash": r.get("available_cash"),
            "weights_used": r.get("weights_used", {}),
            "dimensions": {
                k: {"score": v.get("score"), "detail": v.get("detail")}
                for k, v in dims.items()
            },
        },
    }


def metric_cards_widget(cards: list[dict], title: Optional[str] = None) -> dict:
    """通用指标卡组 → metric_cards widget。
    card: {label, value, type?('return'|'risk'|'quality'|'neutral'), positive?}
    """
    return {"type": "metric_cards", "title": title, "data": {"cards": cards}}


def position_table_widget(positions: list[dict], title: str = "当前持仓") -> dict:
    """持仓列表 → position_table widget。"""
    rows = [
        {
            "symbol": p.get("symbol"),
            "name": p.get("name"),
            "quantity": p.get("quantity"),
            "avg_cost": p.get("avg_cost"),
            "realized_pnl": p.get("realized_pnl"),
        }
        for p in positions
    ]
    return {"type": "position_table", "title": title, "data": {"positions": rows}}


def prediction_widget(symbol: str, r: dict, name: Optional[str] = None) -> dict:
    """ML 涨跌预测 → prediction widget（环形置信度 + 方向 + 预测收益）。"""
    return {
        "type": "prediction",
        "title": f"{name or symbol} · 涨跌预测",
        "data": {
            "symbol": symbol,
            "name": name,
            "direction": r.get("direction"),          # 'UP' | 'DOWN' | str
            "confidence": r.get("confidence"),         # 0~1
            "predicted_return": r.get("predicted_return"),  # 小数，如 0.032
            "forward_days": r.get("forward_days"),
        },
    }


def quote_widget(quotes: list[dict]) -> dict:
    """实时行情列表 → price_quote widget（红涨绿跌报价卡）。
    兼容两套字段：a_share(change_percent/change) 与 EM 批量(change_pct/change_amount)。
    """
    def first(d: dict, *keys):
        for k in keys:
            v = d.get(k)
            if v is not None:
                return v
        return None

    rows = [
        {
            "symbol": q.get("symbol") or q.get("code"),
            "name": q.get("name"),
            "price": first(q, "price", "last"),
            "change_pct": first(q, "change_percent", "change_pct", "pct_change"),
            "change": first(q, "change", "change_amount"),
        }
        for q in quotes
    ]
    return {"type": "price_quote", "title": "实时行情", "data": {"quotes": rows}}


def sparkline_widget(symbol: str, series: list[dict], summary: dict) -> dict:
    """日线走势 → price_sparkline widget（迷你折线 + 区间涨跌幅）。
    series: [{date, close}, ...]（近 30~60 根）
    """
    return {
        "type": "price_sparkline",
        "title": f"{symbol} · 近{summary.get('window_days', '')}日走势",
        "data": {
            "symbol": symbol,
            "series": series,
            "latest_close": summary.get("latest_close"),
            "change_pct": summary.get("change_pct"),
            "high": summary.get("high"),
            "low": summary.get("low"),
        },
    }


def performance_metric_cards(stats: dict) -> dict:
    """组合绩效 dict → metric_cards widget（挑关键指标）。"""
    def yuan(v):
        try:
            return f"¥{float(v):,.0f}"
        except (TypeError, ValueError):
            return "—"

    cards = [
        {"label": "总盈亏", "value": yuan(stats.get("total_pnl")), "type": "return",
         "positive": (stats.get("total_pnl") or 0) >= 0},
        {"label": "总收益率", "value": f"{stats.get('total_pnl_pct', 0)}%", "type": "return",
         "positive": (stats.get("total_pnl_pct") or 0) >= 0},
        {"label": "已实现盈亏", "value": yuan(stats.get("realized_pnl")), "type": "return",
         "positive": (stats.get("realized_pnl") or 0) >= 0},
        {"label": "持仓市值", "value": yuan(stats.get("current_value")), "type": "neutral"},
        {"label": "胜率", "value": f"{stats.get('win_rate', 0)}%", "type": "quality"},
        {"label": "盈亏比", "value": f"{stats.get('profit_loss_ratio', 0)}", "type": "quality"},
        {"label": "交易次数", "value": str(stats.get("total_trades", 0)), "type": "neutral"},
        {"label": "已平仓", "value": str(stats.get("total_closed_trades", 0)), "type": "neutral"},
    ]
    return metric_cards_widget(cards, title="组合绩效")

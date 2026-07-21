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
    display_name = summary.get("name") or symbol
    return {
        "type": "price_sparkline",
        "title": f"{display_name} · 近{summary.get('window_days', '')}日走势",
        "data": {
            "symbol": symbol,
            "series": series,
            "latest_close": summary.get("latest_close"),
            "change_pct": summary.get("change_pct"),
            "high": summary.get("high"),
            "low": summary.get("low"),
        },
    }


def recommendation_board_widget(summary: dict) -> dict:
    """选股推荐 → recommendation_board widget（分区：新买入 / 持仓处置 / 观望态 / 买不起）。

    直接映射 recommend_stocks 的 summary。前端未注册时会 fallback 成 JSON details。
    """
    return {
        "type": "recommendation_board",
        "title": "选股推荐",
        "data": {
            "session_phase": summary.get("session_phase"),
            "market_state": summary.get("market_state"),
            "provisional": summary.get("provisional"),
            "total_capital": summary.get("total_capital"),
            "available_cash": summary.get("available_cash"),
            "max_single_amount": summary.get("max_single_amount"),
            "signal_date": summary.get("signal_date"),
            "signal_count_today": summary.get("signal_count_today"),
            "buys": summary.get("buys", []),
            "holdings_advice": summary.get("holdings_advice", []),
            "skipped_unaffordable": summary.get("skipped_unaffordable", []),
            "note": summary.get("note"),
        },
    }


def limit_up_pool_widget(overview: dict) -> dict:
    """涨停池复盘 → limit_up_pool widget（家数/连板梯队/炸板率/赚钱效应/连板榜）。"""
    return {
        "type": "limit_up_pool",
        "title": "涨停池全览",
        "data": {
            "trade_date": overview.get("trade_date"),
            "limit_up_count": overview.get("limit_up_count"),
            "break_count": overview.get("break_count"),
            "break_rate": overview.get("break_rate"),
            "ladder_distribution": overview.get("ladder_distribution", {}),
            "profit_effect": overview.get("profit_effect", {}),
            "top_boards": overview.get("top_boards", []),
            "top_zhaban": overview.get("top_zhaban", []),
        },
    }


def limit_up_candidates_widget(result: dict) -> dict:
    """次日涨停候选池 → limit_up_candidates widget（打分排名 + 归因 + 免责声明）。"""
    return {
        "type": "limit_up_candidates",
        "title": "次日涨停候选池",
        "data": {
            "predict_date": result.get("predict_date"),
            "target_date": result.get("target_date"),
            "candidates": result.get("candidates", []),
            "disclaimer": "预测基于历史规律统计，非100%准确，仅供参考，不构成投资建议",
        },
    }


def crypto_market_widget(r: dict) -> dict:
    """加密市场大势 → crypto_market widget（恐慌贪婪半环 + BTC 主导率 + 总市值 + regime 徽章）。"""
    ctx = r.get("market_context") or {}
    reg = r.get("btc_regime") or {}
    return {
        "type": "crypto_market",
        "title": "加密市场大势",
        "data": {
            "fear_greed": ctx.get("fear_greed"),                 # {value, label} | None
            "btc_dominance": ctx.get("btc_dominance"),
            "total_market_cap_usd": ctx.get("total_market_cap_usd"),
            "regime": reg.get("regime"),                          # bull | bear | unknown
            "btc_price": reg.get("price"),
            "btc_ma200": reg.get("ma200"),
            "above_ma200": reg.get("above"),
            "pct_from_ma": reg.get("pct_from_ma"),
        },
    }


def crypto_screen_widget(r: dict) -> dict:
    """单币排雷 → crypto_screen widget（排雷分 + verdict 徽章 + 红旗 + 关键维度）。"""
    return {
        "type": "crypto_screen",
        "title": f"{r.get('base_asset') or r.get('symbol')} · 排雷体检",
        "data": {
            "symbol": r.get("symbol"),
            "base_asset": r.get("base_asset"),
            "score": r.get("score"),                              # 0~100
            "verdict": r.get("verdict"),                          # pass | caution | avoid | unknown
            "flags": r.get("flags", []),
            "ambiguous": r.get("ambiguous"),
            # dimensions: {circulating_supply, max_supply, market_cap_usd,
            #              fdv_usd, fdv_mcap_ratio, commits_4w, stars}
            "dimensions": r.get("dimensions", {}),
        },
    }


def crypto_derivatives_widget(r: dict) -> dict:
    """币安衍生品情绪 → crypto_derivatives widget（资金费率 + OI + 多空比）。"""
    return {
        "type": "crypto_derivatives",
        "title": f"{r.get('symbol')} · 衍生品情绪",
        "data": {
            "symbol": r.get("symbol"),
            "funding": r.get("funding"),                          # {funding_rate, mark_price, next_funding_time} | None
            "open_interest": r.get("open_interest"),              # {oi, oi_value, time} | None
            "long_short": r.get("long_short"),                    # {ratio, long_pct, short_pct, time} | None
        },
    }


def crypto_account_widget(r: dict) -> dict:
    """币安现货账户 → crypto_account widget（账户条 + 持仓表）。"""
    acct = r.get("account") or {}
    return {
        "type": "crypto_account",
        "title": "币安现货账户",
        "data": {
            "cash": acct.get("cash"),
            "market_value": acct.get("market_value"),
            "total_value": acct.get("total_value"),
            "unrealized_pnl": acct.get("unrealized_pnl"),
            "positions": r.get("positions", []),                  # [{symbol, quantity, current_price, market_value}]
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

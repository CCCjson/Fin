"""
工具完成话术 —— 每个工具调用完成时给前端的一句「完成了什么」。

原则（Jason 定）：只说成果、不泄露工具名/内部机制（商业化考虑，
别让用户从话术反推出内部工具架构）。所以模板全部是成果导向的自然语言，
可以带对象（股票/网站域名/关键词），不许出现工具名、组名、"API"之类的实现词。

未收录的工具走通用兜底话术，不像 tool_groups 那样强校验——漏一个只是话术变通用，
不会报错。
"""
import re
from typing import Callable, Dict


def _sym(args: dict) -> str:
    """挑最有信息量的标的参数。"""
    return str(args.get("symbol") or args.get("keyword") or args.get("query") or "").strip()


def _domain(args: dict) -> str:
    url = str(args.get("url") or "")
    m = re.match(r"https?://([^/]+)", url)
    return m.group(1) if m else "目标网站"


def _with(prefix: str, key: str = "symbol") -> Callable[[dict], str]:
    """「前缀 + 参数对象」式模板；参数缺失时去掉结尾冒号只出前缀。

    兼容复数形式（symbols=[...] 取第一个，多只时加「等 N 只」）。
    """
    def fmt(args: dict) -> str:
        v = args.get(key)
        if not v:
            plural = args.get(key + "s")
            if isinstance(plural, list) and plural:
                v = plural[0] if len(plural) == 1 else f"{plural[0]} 等 {len(plural)} 只"
        v = str(v or "").strip()
        return f"{prefix}{v}" if v else prefix.rstrip("：")
    return fmt


# 工具名 → 成果话术（args 已知）。保持一句话、成果导向。
_TEMPLATES: Dict[str, Callable[[dict], str]] = {
    # 核心常驻
    "search_stocks": lambda a: f"已确认「{_sym(a)}」的股票代码" if _sym(a) else "已完成股票检索",
    "get_realtime_quote": _with("已拿到实时行情："),
    "get_daily_data": _with("已调出历史K线："),
    "get_cockpit_score": _with("已完成五维体检："),
    "get_market_pulse": lambda a: "已把握当前大盘脉搏",
    "get_intraday_check": _with("已核对盘中时机："),
    "recommend_stocks": lambda a: "已完成今日选股扫描",
    "get_positions": lambda a: "已清点当前持仓",
    "get_performance": lambda a: "已核算组合绩效",
    "size_position": _with("已算好可买仓位："),
    "place_order": _with("已提交下单请求："),
    "open_page": lambda a: "已为你打开对应页面",
    "load_toolgroup": lambda a: "已备好所需分析能力",
    # signals
    "get_today_signals": lambda a: "已汇总今日买卖信号",
    "get_stock_signals": _with("已调出信号记录："),
    "get_signal_stats": lambda a: "已统计策略历史胜率",
    # screener
    "screen_stocks": lambda a: "已按条件筛出候选股",
    "list_stock_pool": lambda a: "已圈定股票池范围",
    "list_industry_stocks": lambda a: "已列出行业成分股",
    # watchlist_alerts
    "get_watchlist": lambda a: "已调出自选股清单",
    "add_to_watchlist": _with("已加入自选股："),
    "remove_from_watchlist": _with("已移出自选股："),
    "create_price_alert": _with("已设好价格预警："),
    "list_price_alerts": lambda a: "已调出预警清单",
    "delete_price_alert": lambda a: "已删除该预警",
    # portfolio_risk
    "get_position_guard_status": lambda a: "已巡查持仓止损防线",
    "get_portfolio_risk": lambda a: "已完成组合风险体检",
    "get_reconciliation_status": lambda a: "已核对账实一致性",
    "get_trade_history": lambda a: "已调出交易流水",
    "record_manual_trade": _with("已补录成交："),
    # news
    "get_news_sentiment": _with("已评估舆情温度："),
    "get_morning_brief": lambda a: "已备好盘前简报",
    "get_recent_news_conclusions": lambda a: "已调出新闻结论",
    # signals（写操作）
    "generate_signals": lambda a: "已重新生成信号",
    # market_sentiment
    "get_limit_up_pool": lambda a: "已复盘涨停股池",
    "predict_limit_up_candidates": lambda a: "已算出涨停候选池",
    # knowledge_web
    "search_knowledge": lambda a: "已检索知识库",
    "list_alpha_ideas": lambda a: "已调出策略想法清单",
    "web_search": lambda a: "已完成一轮联网搜索",
    "sec_search": lambda a: "已检索美股监管披露文件",
    "read_url": lambda a: f"已读完 {_domain(a)} 的页面",
    "scrape": lambda a: f"已抓取 {_domain(a)} 的数据",
    "login_site": lambda a: f"已开浏览器等你登录 {_domain(a)}",
    # review
    "get_daily_review": lambda a: "已调出当日复盘",
    "request_review_ai_score": lambda a: "已完成 AI 交易打分",
    "get_decision_history": lambda a: "已调出历史决策记录",
    # backtest_ml
    "run_backtest": lambda a: "已跑完策略回测",
    "predict_stock": _with("已生成涨跌预测："),
    # system
    "get_system_pulse": lambda a: "已巡检系统健康状态",
    "get_settings": lambda a: "已读取系统配置",
    "update_setting": lambda a: "已更新系统配置",
    # deep_agents（subagent 也走同一 TOOL_RESULT）
    "run_deep_stock": _with("已完成深度研判："),
    "run_news_analysis": lambda a: "已完成新闻深度解读",
    "run_alpha_lab": lambda a: "已完成策略研发迭代",
    # 五个报告章节（13.2 拆解，各自独立成稿）
    "report_market": lambda a: "已写好大盘与板块章节",
    "report_news": lambda a: "已写好新闻舆情章节",
    "report_positions": lambda a: "已写好持仓诊断章节",
    "report_strategy": lambda a: "已写好上期回顾与策略章节",
    "report_picks": lambda a: "已写好买入推荐章节",
}

_FALLBACK_OK = "已完成一步数据处理"
_FALLBACK_FAIL = "这一步没成功，换个路子再试"


def done_desc(name: str, args: dict | None, ok: bool = True) -> str:
    """工具完成时的一句成果话术（失败时统一话术，不暴露内部细节）。"""
    if not ok:
        return _FALLBACK_FAIL
    fmt = _TEMPLATES.get(name)
    if fmt is None:
        return _FALLBACK_OK
    try:
        return fmt(args or {})
    except Exception:  # noqa: BLE001 — 话术生成绝不能影响主流程
        return _FALLBACK_OK

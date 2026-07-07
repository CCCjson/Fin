"""
导航类工具 —— 让 MoneyBill 主动帮 Jason 打开系统里的功能页并预填股票。

返回 ToolEnvelope(navigate=...)；executor 透传 navigate，orchestrator 发 NAVIGATE 事件，
前端据此切页（并把当前对话带进缩小的浮窗 MoneyBill 继续）。path 走白名单，杜绝任意跳转。
"""
from typing import Optional

from pydantic import BaseModel, Field

from agents.registry import tool
from agents.tool_envelope import ToolEnvelope

# 白名单：与前端 MainStage PAGES 路由一致（架构收敛后仅保留 7 个可视化工作台）
ALLOWED_PATHS = {
    "/app/market", "/app/backtest", "/app/prediction",
    "/app/orderbook", "/app/data-monitor", "/trading", "/fine-tune",
}

# 已收敛到对话的旧页面 → 提示该用什么能力（agent 直接改用对应工具回答）
RETIRED_PATHS = {
    "/app/cockpit": "决策驾驶舱已收敛到对话：直接用 get_cockpit_score 回答（会带 cockpit_score 卡片）",
    "/app/dashboard": "账户概览已收敛到对话：直接用 get_positions / get_performance 回答",
    "/app/realtime": "实时行情已收敛到对话：用 get_realtime_quote / get_market_pulse 回答",
    "/app/signals": "信号分析已收敛到对话：用 signals 工具组（get_today_signals 等）回答",
    "/app/tracking": "信号追踪已收敛到对话：用 signals 工具组回答",
    "/app/reports": "AI 报告已收敛到对话：用 run_research_report 生成",
    "/app/portfolio": "交易记录已收敛到对话：用 get_trade_history / record_manual_trade",
    "/app/review": "每日复盘已收敛到对话：用 review 工具组回答",
    "/app/advisor": "AI 顾问已并入 MoneyBill 本体：直接对话或用 run_deep_stock",
    "/app/pipeline": "数据管道已并入数据监控页：改开 /app/data-monitor",
    "/app/news": "新闻分析已收敛到对话：用 news 工具组 / run_news_analysis",
    "/app/watchlist": "自选股已收敛到对话：用 watchlist_alerts 工具组",
    "/app/screener": "选股器已收敛到对话：用 screen_stocks / recommend_stocks",
    "/app/knowledge": "知识库已收敛到对话：用 search_knowledge",
    "/app/settings": "设置已收敛到对话：用 get_settings / update_setting",
    "/alpha-lab": "Alpha Lab 已收敛到对话：用 run_alpha_lab 跑策略研发",
}


class OpenPageArgs(BaseModel):
    # path 不用 Literal 强校验：未知/已退役路径是正常的业务性拒绝（见函数体），
    # 不该被校验层挡成 validation_error。
    path: str = Field(..., min_length=1, description="目标页面路径，如 /app/market")
    symbol: Optional[str] = Field(None, description="可选，预填的股票代码，如 600519.SH")


@tool(
    name="open_page",
    description=(
        "帮 Jason 打开系统某个可视化工作台页面，可预填股票代码。用户说「打开K线/"
        "去回测页」等想看图表页面时调用。可用 path："
        "/app/market(K线行情) /app/backtest(回测) "
        "/app/prediction(股价预测) /app/orderbook(订单簿) /app/data-monitor(数据监控·含数据管道) "
        "/trading(自动化交易) /fine-tune(模型微调)。"
        "打开个股相关页(market/prediction)时务必带上 symbol。"
        "其他旧页面（驾驶舱/信号/筛股/自选/复盘/报告/新闻/知识库/设置/交易记录）已收敛到对话，"
        "直接用对应工具回答，不要尝试跳页。"
    ),
    args_model=OpenPageArgs,
    category="nav",
    group="core",
)
def open_page(path: str, symbol: str = None) -> ToolEnvelope:
    path = (path or "").strip()
    if path in RETIRED_PATHS:
        return ToolEnvelope(business_result="negative", message=f"该页面已退役，{RETIRED_PATHS[path]}。")
    if path not in ALLOWED_PATHS:
        return ToolEnvelope(business_result="negative", message=f"打不开页面：未知路径 {path}。请用系统支持的功能页路径。")
    nav = {"path": path}
    if symbol:
        nav["symbol"] = symbol.strip()
    tail = f"（已预填 {nav['symbol']}）" if symbol else ""
    return ToolEnvelope(message=f"已为你打开 {path}{tail}。", navigate=nav)

"""
回测类工具 —— 包 C++ 回测引擎（正版，走 :8002 代理）跑单次策略回测。

复用 services/backtest_cpp_client.py 的 `run_single_backtest`（全同步、自带异常兜底、
自动从 DataEngine 取 K 线并持久化到历史库）。C++ 服务没启动时它返回 status=failed，
本工具据此给出可操作提示，让 LLM 自愈而不是崩。
"""
from datetime import timedelta
from typing import Optional

from pydantic import BaseModel, Field

from agents.registry import tool
from agents.tool_envelope import ToolEnvelope

from common.market import A_SHARE
from common.market_time import market_today
from agents.widgets import metric_cards_widget


def _default_window() -> tuple[str, str]:
    """默认回测区间：近 1 年（到今天）。

    「今天」按 A 股口径 —— 回测标的可能混市场，但默认窗口末端差一天不影响结论，
    显式声明比裸 `date.today()` 好（调用方可传 start/end 精确控制）。
    """
    end = market_today(A_SHARE)
    start = end - timedelta(days=365)
    return start.isoformat(), end.isoformat()


class RunBacktestArgs(BaseModel):
    symbol: str = Field(..., min_length=1, description="股票代码，如 600519.SH")
    strategy: str = Field("MA_CROSS", description="策略名，默认 MA_CROSS")
    start_date: str = Field("", description="开始日期 YYYY-MM-DD，可选")
    end_date: str = Field("", description="结束日期 YYYY-MM-DD，可选")
    initial_capital: float = Field(100000.0, gt=0, description="初始资金，默认 100000")
    market: str = Field("a_share", description="市场 a_share/hk_stock/us_stock，默认 a_share（其余写法会自动归一）")
    params: Optional[dict] = Field(
        None, description="策略参数对象，如 {\"fast_period\":5,\"slow_period\":20}")


@tool(
    name="run_backtest",
    description=(
        "对单只股票跑历史回测，返回收益率、年化、夏普、最大回撤、胜率、盈亏比、交易次数。"
        "策略 strategy 取值如 MA_CROSS(均线交叉)/MOMENTUM(动量)；params 传策略参数(如均线周期)。"
        "不填日期默认回测近一年。用户问「这策略过去表现如何/回测一下」时调用。"
    ),
    args_model=RunBacktestArgs,
    category="backtest",
    group="backtest_ml",
)
def run_backtest(symbol: str, strategy: str = "MA_CROSS", start_date: str = "",
                 end_date: str = "", initial_capital: float = 100000.0,
                 market: str = "a_share", params: dict = None) -> ToolEnvelope:
    from services.backtest_cpp_client import run_single_backtest as _run_single_backtest_sync
    from common.market import normalize_market

    # LLM/前端可能传 us/hk 等历史写法，归一到 canonical 再进 C++（否则费率静默错算成 A股）
    market = normalize_market(market)

    if not start_date or not end_date:
        d_start, d_end = _default_window()
        start_date = start_date or d_start
        end_date = end_date or d_end

    r = _run_single_backtest_sync(
        symbol=symbol,
        strategy=(strategy or "MA_CROSS").upper(),
        params=params or {},
        start_date=start_date,
        end_date=end_date,
        initial_capital=float(initial_capital),
        market=market,
        batch_id=f"agent_{market_today(A_SHARE).isoformat()}",
        task_label="MoneyBill",
    )

    if r.get("status") != "completed" or not r.get("metrics"):
        err = r.get("error", "回测失败")
        if "C++" in err or "未启动" in err or "8002" in err:
            return ToolEnvelope(
                business_result="negative",
                message="回测跑不了：C++ 回测服务(:8002)未启动。请先运行 `cd backtest_cpp/build && ./backtest_server`。")
        return ToolEnvelope(business_result="negative", message=f"{symbol} {strategy} 回测失败：{err}")

    m = r["metrics"]
    tr = m.get("total_return_pct", 0.0)
    summary = {
        "symbol": symbol,
        "strategy": strategy,
        "period": f"{start_date} ~ {end_date}",
        "total_return_pct": round(tr, 2),
        "annual_return_pct": round(m.get("annual_return", 0.0), 2),
        "sharpe_ratio": round(m.get("sharpe_ratio", 0.0), 2),
        "max_drawdown_pct": round(m.get("max_drawdown_pct", 0.0), 2),
        "win_rate_pct": round(m.get("win_rate", 0.0), 1),
        "profit_factor": round(m.get("profit_factor", 0.0), 2),
        "total_trades": int(m.get("total_trades", 0)),
    }
    cards = [
        {"label": "总收益率", "value": f"{summary['total_return_pct']}%", "type": "return",
         "positive": tr >= 0},
        {"label": "年化", "value": f"{summary['annual_return_pct']}%", "type": "return",
         "positive": summary["annual_return_pct"] >= 0},
        {"label": "夏普", "value": f"{summary['sharpe_ratio']}", "type": "quality"},
        {"label": "最大回撤", "value": f"{summary['max_drawdown_pct']}%", "type": "risk"},
        {"label": "胜率", "value": f"{summary['win_rate_pct']}%", "type": "quality"},
        {"label": "盈亏比", "value": f"{summary['profit_factor']}", "type": "quality"},
        {"label": "交易次数", "value": str(summary["total_trades"]), "type": "neutral"},
    ]
    widget = metric_cards_widget(cards, title=f"📊 {symbol} · {strategy} 回测 ({summary['period']})")
    return ToolEnvelope(data=summary, widget=widget)

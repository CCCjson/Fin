"""
API Request/Response Models
"""
from typing import List, Dict, Optional, Any
from datetime import datetime, date
from pydantic import BaseModel, Field


# ==================== 数据模块 ====================

class StockDataRequest(BaseModel):
    """查询股票数据请求"""
    symbol: str = Field(..., description="股票代码", example="688576.SH")
    start_date: str = Field(..., description="开始日期", example="2025-01-01")
    end_date: str = Field(..., description="结束日期", example="2025-12-31")
    db_only: bool = Field(False, description="仅查数据库缓存，不联网拉取")


class StockDataResponse(BaseModel):
    """股票数据响应"""
    symbol: str
    data: List[Dict[str, Any]]
    count: int


class StockListResponse(BaseModel):
    """股票列表响应"""
    stocks: List[Dict[str, str]]
    count: int


# ==================== 分析模块 ====================

class IndicatorRequest(BaseModel):
    """技术指标计算请求"""
    symbol: str = Field(..., description="股票代码")
    start_date: str = Field(..., description="开始日期")
    end_date: str = Field(..., description="结束日期")
    indicators: List[str] = Field(..., description="指标列表", example=["MA", "MACD", "RSI"])
    params: Optional[Dict[str, Any]] = Field(default={}, description="指标参数")


class IndicatorResponse(BaseModel):
    """技术指标响应"""
    symbol: str
    indicators: Dict[str, List[Any]]
    count: int


class SignalRequest(BaseModel):
    """信号检测请求"""
    symbol: str
    start_date: str
    end_date: str
    signal_types: Optional[List[str]] = Field(default=None, description="信号类型")


class SignalResponse(BaseModel):
    """信号响应"""
    symbol: str
    signals: List[Dict[str, Any]]
    count: int


class PatternRequest(BaseModel):
    """K线形态识别请求"""
    symbol: str
    start_date: str
    end_date: str


class PatternResponse(BaseModel):
    """K线形态响应"""
    symbol: str
    patterns: Dict[str, List[Dict[str, Any]]]
    count: int


# ==================== 回测模块 ====================

class BacktestRequest(BaseModel):
    """回测请求"""
    strategy_name: str = Field(..., description="策略名称", example="MA_CROSS")
    symbol: str = Field(..., description="股票代码")
    start_date: str = Field(..., description="开始日期")
    end_date: str = Field(..., description="结束日期")
    initial_capital: float = Field(default=1000000.0, description="初始资金")
    strategy_params: Optional[Dict[str, Any]] = Field(default={}, description="策略参数")


class BacktestResponse(BaseModel):
    """回测响应"""
    strategy_name: str
    symbol: str
    period: str
    initial_capital: float
    final_value: float
    total_return: float
    total_return_pct: float
    sharpe_ratio: float
    max_drawdown: float
    max_drawdown_pct: float
    win_rate: float
    profit_factor: float
    total_trades: int
    winning_trades: int
    losing_trades: int
    equity_curve: List[Dict[str, Any]]
    trades: List[Dict[str, Any]]


# ==================== 交易模块 ====================

class OrderRequest(BaseModel):
    """下单请求"""
    symbol: str = Field(..., description="股票代码")
    action: str = Field(..., description="买卖方向", example="BUY")
    quantity: int = Field(..., description="数量", gt=0)
    price: Optional[float] = Field(default=None, description="价格（None为市价单）")


class OrderResponse(BaseModel):
    """订单响应"""
    order_id: str
    symbol: str
    action: str
    quantity: int
    price: Optional[float]
    status: str
    filled_price: Optional[float]
    filled_quantity: int
    commission: float
    submit_time: datetime
    message: str


class PositionResponse(BaseModel):
    """持仓响应"""
    symbol: str
    quantity: int
    available_quantity: int
    avg_cost: float
    current_price: float
    market_value: float
    unrealized_pnl: float
    unrealized_pnl_pct: float


class AccountResponse(BaseModel):
    """账户响应"""
    cash: float
    market_value: float
    total_value: float
    available_cash: float
    frozen_cash: float
    initial_cash: float
    unrealized_pnl: float
    total_commission: float
    total_trades: int
    return_pct: float
    positions: List[PositionResponse]


class UpdatePriceRequest(BaseModel):
    """更新价格请求"""
    symbol: str
    price: float


# ==================== 监控模块 ====================

class TradeLogResponse(BaseModel):
    """交易日志响应"""
    trades: List[Dict[str, Any]]
    count: int
    statistics: Dict[str, Any]


class OrderStatusResponse(BaseModel):
    """订单状态响应"""
    orders: List[Dict[str, Any]]
    count: int
    statistics: Dict[str, Any]


class PerformanceResponse(BaseModel):
    """绩效响应"""
    current_performance: Dict[str, Any]
    statistics: Dict[str, Any]
    equity_curve: List[Dict[str, Any]]
    drawdown_curve: List[Dict[str, Any]]


class AlertResponse(BaseModel):
    """告警响应"""
    alerts: List[Dict[str, Any]]
    count: int
    statistics: Dict[str, Any]


# ==================== 通用响应 ====================

class MessageResponse(BaseModel):
    """通用消息响应"""
    message: str
    success: bool = True
    data: Optional[Any] = None


class ErrorResponse(BaseModel):
    """错误响应"""
    error: str
    detail: Optional[str] = None
    success: bool = False

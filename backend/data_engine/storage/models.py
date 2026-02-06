"""
数据库模型定义
"""
from sqlalchemy import Column, String, Float, Integer, DateTime, Date, Index, ForeignKey, Text
from sqlalchemy.sql import func
from data_engine.storage.database import Base


class StockInfo(Base):
    """股票基本信息表"""
    __tablename__ = "stock_info"

    symbol = Column(String(20), primary_key=True)
    name = Column(String(100), nullable=False)
    market = Column(String(20), nullable=False, index=True)
    exchange = Column(String(10))
    industry = Column(String(50))
    sector = Column(String(50))
    stock_type = Column(String(20))
    list_date = Column(Date)
    is_active = Column(Integer, default=1, index=True)
    created_at = Column(DateTime, server_default=func.now())
    updated_at = Column(DateTime, server_default=func.now(), onupdate=func.now())

    def __repr__(self):
        return f"<StockInfo(symbol={self.symbol}, name={self.name})>"


class DailyQuote(Base):
    """日线行情表"""
    __tablename__ = "daily_quotes"

    id = Column(Integer, primary_key=True, autoincrement=True)
    symbol = Column(String(20), nullable=False, index=True)
    market = Column(String(20), nullable=False, index=True)
    date = Column(Date, nullable=False, index=True)

    # OHLCV
    open = Column(Float, nullable=False)
    high = Column(Float, nullable=False)
    low = Column(Float, nullable=False)
    close = Column(Float, nullable=False)
    volume = Column(Float, nullable=False)

    # 扩展字段
    amount = Column(Float)
    turnover = Column(Float)
    adjust_factor = Column(Float)

    # 元数据
    created_at = Column(DateTime, server_default=func.now())
    updated_at = Column(DateTime, server_default=func.now(), onupdate=func.now())

    # 复合索引
    __table_args__ = (
        Index('idx_symbol_date', 'symbol', 'date', unique=True),
        Index('idx_market_date', 'market', 'date'),
    )

    def __repr__(self):
        return f"<DailyQuote(symbol={self.symbol}, date={self.date}, close={self.close})>"


class RealtimeQuote(Base):
    """实时行情表"""
    __tablename__ = "realtime_quotes"

    id = Column(Integer, primary_key=True, autoincrement=True)
    symbol = Column(String(20), nullable=False, index=True)
    market = Column(String(20), nullable=False)

    price = Column(Float, nullable=False)
    change = Column(Float)
    change_percent = Column(Float)
    volume = Column(Float)
    amount = Column(Float)

    timestamp = Column(DateTime, nullable=False, index=True)

    def __repr__(self):
        return f"<RealtimeQuote(symbol={self.symbol}, price={self.price})>"


class DataUpdateLog(Base):
    """数据更新日志表"""
    __tablename__ = "data_update_logs"

    id = Column(Integer, primary_key=True, autoincrement=True)
    market = Column(String(20), nullable=False, index=True)
    update_type = Column(String(20), nullable=False)  # daily, realtime
    symbols_count = Column(Integer)
    records_count = Column(Integer)
    status = Column(String(20), nullable=False)  # success, failed, partial
    error_message = Column(Text)
    started_at = Column(DateTime, nullable=False)
    completed_at = Column(DateTime)
    duration_seconds = Column(Float)

    def __repr__(self):
        return f"<DataUpdateLog(market={self.market}, status={self.status})>"


class Signal(Base):
    """交易信号表"""
    __tablename__ = "signals"

    id = Column(Integer, primary_key=True, autoincrement=True)
    symbol = Column(String(20), nullable=False, index=True)
    date = Column(Date, nullable=False, index=True)
    signal_type = Column(String(10), nullable=False)  # BUY / SELL
    strength = Column(Float, nullable=False)  # 信号强度 (0-1)
    price = Column(Float, nullable=False)  # 触发价格

    # 建议操作
    entry_price = Column(Float)
    stop_loss = Column(Float)
    take_profit = Column(Float)
    position_size = Column(String(10))

    # 信号原因 (JSON)
    reasons = Column(Text)  # JSON格式的原因列表

    # 元数据
    strategy = Column(String(50))  # 策略名称
    signal_id = Column(String(50), unique=True, index=True)  # 信号唯一标识
    created_at = Column(DateTime, server_default=func.now())

    # 复合索引
    __table_args__ = (
        Index('idx_signal_symbol_date', 'symbol', 'date'),
        Index('idx_signal_type', 'signal_type'),
        Index('idx_strength', 'strength'),
    )

    def __repr__(self):
        return f"<Signal(symbol={self.symbol}, type={self.signal_type}, strength={self.strength})>"


class BacktestTask(Base):
    """回测任务表"""
    __tablename__ = "backtest_tasks"

    task_id = Column(String(50), primary_key=True)
    name = Column(String(200))
    status = Column(String(20), nullable=False, index=True)  # pending, running, completed, failed
    strategy_type = Column(String(50), nullable=False)
    strategy_params = Column(Text)  # JSON格式策略参数

    # 回测配置
    symbols = Column(Text, nullable=False)  # JSON格式股票列表
    start_date = Column(Date, nullable=False)
    end_date = Column(Date, nullable=False)
    initial_capital = Column(Float, nullable=False)
    commission_rate = Column(Float)
    slippage_rate = Column(Float)

    # 时间
    created_at = Column(DateTime, server_default=func.now(), index=True)
    started_at = Column(DateTime)
    completed_at = Column(DateTime)

    # 错误信息
    error_message = Column(Text)

    def __repr__(self):
        return f"<BacktestTask(task_id={self.task_id}, status={self.status})>"


class BacktestResult(Base):
    """回测结果表"""
    __tablename__ = "backtest_results"

    id = Column(Integer, primary_key=True, autoincrement=True)
    task_id = Column(String(50), ForeignKey("backtest_tasks.task_id"), nullable=False, index=True)

    # 收益指标
    total_return = Column(Float)
    total_return_pct = Column(Float)
    annual_return = Column(Float)
    final_value = Column(Float)

    # 风险指标
    max_drawdown = Column(Float)
    max_drawdown_pct = Column(Float)
    volatility = Column(Float)
    sharpe_ratio = Column(Float)
    sortino_ratio = Column(Float)

    # 交易统计
    total_trades = Column(Integer)
    winning_trades = Column(Integer)
    losing_trades = Column(Integer)
    win_rate = Column(Float)
    profit_factor = Column(Float)

    # 详细数据 (JSON)
    daily_records = Column(Text)  # 每日净值记录
    trade_records = Column(Text)  # 交易记录

    created_at = Column(DateTime, server_default=func.now())

    def __repr__(self):
        return f"<BacktestResult(task_id={self.task_id}, return={self.total_return_pct}%)>"


class Order(Base):
    """订单记录表"""
    __tablename__ = "orders"

    order_id = Column(String(50), primary_key=True)
    account_id = Column(String(50), nullable=False, index=True)
    symbol = Column(String(20), nullable=False, index=True)
    side = Column(String(10), nullable=False)  # BUY / SELL
    order_type = Column(String(20), nullable=False)  # MARKET / LIMIT / STOP
    quantity = Column(Integer, nullable=False)
    price = Column(Float)  # 限价
    stop_price = Column(Float)  # 止损价
    status = Column(String(20), nullable=False, index=True)  # PENDING, SUBMITTED, FILLED, CANCELLED, REJECTED
    filled_quantity = Column(Integer, default=0)
    avg_fill_price = Column(Float, default=0)
    commission = Column(Float, default=0)

    # 元数据
    strategy = Column(String(50))
    signal_strength = Column(Float)
    reason = Column(Text)
    risk_checked = Column(Integer, default=0)
    rejected_reason = Column(Text)

    # 时间
    created_at = Column(DateTime, server_default=func.now(), index=True)
    submitted_at = Column(DateTime)
    filled_at = Column(DateTime)
    cancelled_at = Column(DateTime)

    # 复合索引
    __table_args__ = (
        Index('idx_account_symbol', 'account_id', 'symbol'),
        Index('idx_status_created', 'status', 'created_at'),
    )

    def __repr__(self):
        return f"<Order(order_id={self.order_id}, symbol={self.symbol}, side={self.side}, status={self.status})>"


class Trade(Base):
    """成交记录表"""
    __tablename__ = "trades"

    trade_id = Column(String(50), primary_key=True)
    order_id = Column(String(50), ForeignKey("orders.order_id"), nullable=False, index=True)
    account_id = Column(String(50), nullable=False, index=True)
    symbol = Column(String(20), nullable=False, index=True)
    direction = Column(String(10), nullable=False)  # long / short
    quantity = Column(Integer, nullable=False)
    price = Column(Float, nullable=False)
    commission = Column(Float, nullable=False)
    slippage = Column(Float, default=0)
    amount = Column(Float, nullable=False)  # 成交金额

    executed_at = Column(DateTime, server_default=func.now(), index=True)

    # 复合索引
    __table_args__ = (
        Index('idx_trade_account_symbol', 'account_id', 'symbol'),
        Index('idx_executed_at', 'executed_at'),
    )

    def __repr__(self):
        return f"<Trade(trade_id={self.trade_id}, symbol={self.symbol}, quantity={self.quantity}, price={self.price})>"

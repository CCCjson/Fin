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


class RealtimeSnapshot(Base):
    """实时行情快照表 — 每次请求全市场数据存一份"""
    __tablename__ = "realtime_snapshots"

    id = Column(Integer, primary_key=True, autoincrement=True)
    snapshot_time = Column(DateTime, nullable=False, index=True)  # 快照时间
    symbol = Column(String(20), nullable=False, index=True)
    name = Column(String(100))
    price = Column(Float)           # 最新价
    change_pct = Column(Float)      # 涨跌幅 %
    change_amount = Column(Float)   # 涨跌额
    volume = Column(Float)          # 成交量（手）
    amount = Column(Float)          # 成交额
    amplitude = Column(Float)       # 振幅 %
    turnover = Column(Float)        # 换手率 %
    pe_ratio = Column(Float)        # 市盈率
    high = Column(Float)            # 最高
    low = Column(Float)             # 最低
    open = Column(Float)            # 今开
    prev_close = Column(Float)      # 昨收

    __table_args__ = (
        Index('idx_snapshot_time_symbol', 'snapshot_time', 'symbol'),
    )

    def __repr__(self):
        return f"<RealtimeSnapshot(symbol={self.symbol}, price={self.price}, change={self.change_pct}%)>"


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
    batch_id = Column(String(50), nullable=True, index=True)  # 关联批量回测
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


class SignalTracking(Base):
    """信号追踪表 — 追踪每个信号的实际市场表现"""
    __tablename__ = "signal_tracking"

    id = Column(Integer, primary_key=True, autoincrement=True)
    signal_id = Column(String(50), unique=True, nullable=False, index=True)
    symbol = Column(String(20), nullable=False, index=True)
    strategy = Column(String(50), index=True)
    signal_type = Column(String(10), nullable=False)  # BUY / SELL
    signal_date = Column(Date, nullable=False, index=True)
    signal_price = Column(Float, nullable=False)
    stop_loss = Column(Float)
    take_profit = Column(Float)

    # N 日收益率（%）
    return_1d = Column(Float)
    return_3d = Column(Float)
    return_5d = Column(Float)
    return_10d = Column(Float)
    return_20d = Column(Float)

    # 极值统计（20 日窗口内）
    max_gain = Column(Float)       # 最大浮盈 %（MFE）
    max_loss = Column(Float)       # 最大浮亏 %（MAE）
    max_gain_day = Column(Integer)  # 第几个交易日达到最大浮盈
    max_loss_day = Column(Integer)  # 第几个交易日达到最大浮亏

    # 止损止盈追踪
    hit_stop_loss = Column(Integer, default=0)
    hit_take_profit = Column(Integer, default=0)
    days_to_stop = Column(Integer)
    days_to_target = Column(Integer)

    # 状态
    tracking_status = Column(String(20), nullable=False, default='pending', index=True)
    outcome = Column(String(10))  # win / loss / neutral
    tracked_days = Column(Integer, default=0)

    created_at = Column(DateTime, server_default=func.now())
    updated_at = Column(DateTime, server_default=func.now(), onupdate=func.now())

    __table_args__ = (
        Index('idx_tracking_strategy', 'strategy'),
        Index('idx_tracking_signal_date', 'signal_date'),
        Index('idx_tracking_outcome', 'outcome'),
        Index('idx_tracking_symbol_date', 'symbol', 'signal_date'),
    )

    def __repr__(self):
        return f"<SignalTracking(signal_id={self.signal_id}, status={self.tracking_status}, outcome={self.outcome})>"


class AnalysisReport(Base):
    """AI分析报告表"""
    __tablename__ = "analysis_reports"

    id = Column(Integer, primary_key=True, autoincrement=True)
    report_id = Column(String(50), unique=True, nullable=False, index=True)
    report_type = Column(String(20), nullable=False, index=True)  # weekly / monthly
    title = Column(String(200), nullable=False)
    content = Column(Text)  # 完整Markdown内容

    period_start = Column(Date)
    period_end = Column(Date)

    model_used = Column(String(50))
    token_count = Column(Integer)
    generation_time_seconds = Column(Float)
    data_snapshot = Column(Text)  # JSON格式的原始数据快照

    status = Column(String(20), nullable=False, default="generating", index=True)  # generating / completed / failed
    error_message = Column(Text)

    created_at = Column(DateTime, server_default=func.now(), index=True)

    def __repr__(self):
        return f"<AnalysisReport(report_id={self.report_id}, type={self.report_type}, status={self.status})>"


class ManualTrade(Base):
    """手动交易记录表"""
    __tablename__ = "manual_trades"

    id = Column(Integer, primary_key=True, autoincrement=True)
    symbol = Column(String(20), nullable=False, index=True)
    name = Column(String(100))
    side = Column(String(10), nullable=False)  # BUY / SELL
    price = Column(Float, nullable=False)
    quantity = Column(Integer, nullable=False)
    amount = Column(Float, nullable=False)
    commission = Column(Float, default=0)
    trade_date = Column(Date, nullable=False, index=True)
    note = Column(Text)

    # 来源追溯
    source_type = Column(String(20), nullable=True, index=True)
        # "automation" | "manual_broker" | "manual_entry" | None(历史数据)
    pending_order_id = Column(String(50), nullable=True, index=True)
        # 关联 PendingOrder.order_id，用于溯源

    # AI 报告关联（可空，手动录入时为 null）
    report_id = Column(String(50), nullable=True, index=True)
    ai_recommended_price = Column(Float, nullable=True)
    ai_stop_loss = Column(Float, nullable=True)
    ai_take_profit = Column(Float, nullable=True)
    ai_composite_score = Column(Float, nullable=True)
    ai_strategy = Column(String(100), nullable=True)

    created_at = Column(DateTime, server_default=func.now())
    updated_at = Column(DateTime, server_default=func.now(), onupdate=func.now())

    __table_args__ = (
        Index('idx_manual_trade_symbol_date', 'symbol', 'trade_date'),
        Index('idx_manual_trade_report', 'report_id'),
    )

    def __repr__(self):
        return f"<ManualTrade(symbol={self.symbol}, side={self.side}, price={self.price}, qty={self.quantity})>"


class DailyReview(Base):
    """每日复盘记录表"""
    __tablename__ = "daily_reviews"

    id = Column(Integer, primary_key=True, autoincrement=True)
    review_date = Column(Date, unique=True, nullable=False, index=True)

    # 当日快照
    daily_pnl = Column(Float)
    positions_count = Column(Integer)
    trades_count = Column(Integer)
    signals_count = Column(Integer)
    index_snapshot = Column(Text, nullable=True)  # JSON: 5个指数的 price/change_pct

    # 评分系统
    self_score = Column(Integer, nullable=True)       # 自评 1-10
    ai_score = Column(Integer, nullable=True)         # AI评分 1-10
    ai_score_reason = Column(Text, nullable=True)     # AI打分理由 Markdown
    ai_dimension_scores = Column(Text, nullable=True) # JSON: 五维子评分 + 亮点 + 改进
    composite_score = Column(Float, nullable=True)    # 综合分 = (self + ai) / 2

    # 复盘笔记
    note = Column(Text)
    template_used = Column(String(20), default="beginner")

    created_at = Column(DateTime, server_default=func.now())
    updated_at = Column(DateTime, server_default=func.now(), onupdate=func.now())

    def __repr__(self):
        return f"<DailyReview(date={self.review_date}, composite={self.composite_score})>"


class PredictionRecord(Base):
    """股价预测记录表"""
    __tablename__ = "prediction_records"

    id = Column(Integer, primary_key=True, autoincrement=True)
    symbol = Column(String(20), nullable=False, index=True)
    prediction_date = Column(Date, nullable=False, index=True)
    target_date = Column(Date, index=True)
    forward_days = Column(Integer, default=5)
    direction = Column(String(10))               # UP / DOWN
    confidence = Column(Float)                   # 0~1
    predicted_prices = Column(Text)              # JSON: [p1, p2, ..., pN]
    predicted_return = Column(Float)
    lstm_detail = Column(Text)                   # JSON
    xgb_detail = Column(Text)                    # JSON
    actual_prices = Column(Text)                 # JSON: 回填实际价格
    actual_return = Column(Float)
    outcome = Column(String(10))                 # WIN / LOSS
    created_at = Column(DateTime, server_default=func.now())

    __table_args__ = (
        Index('idx_pred_symbol_date', 'symbol', 'prediction_date'),
    )

    def __repr__(self):
        return f"<PredictionRecord(symbol={self.symbol}, date={self.prediction_date}, dir={self.direction})>"


class TrainedModelRecord(Base):
    """训练模型记录表"""
    __tablename__ = "trained_models"

    id = Column(Integer, primary_key=True, autoincrement=True)
    symbol = Column(String(20), nullable=False, index=True)
    model_type = Column(String(20))              # lstm / xgboost / ensemble
    train_period = Column(String(20))
    data_points = Column(Integer)
    train_start = Column(Date)
    train_end = Column(Date)
    val_metrics = Column(Text)                   # JSON
    model_path = Column(String(500))
    status = Column(String(20), default="ready") # ready / training / failed
    created_at = Column(DateTime, server_default=func.now())

    def __repr__(self):
        return f"<TrainedModelRecord(symbol={self.symbol}, type={self.model_type}, status={self.status})>"


class UserSettings(Base):
    """用户设置表（键值对存储）"""
    __tablename__ = "user_settings"

    key = Column(String(50), primary_key=True)
    value = Column(String(200), nullable=False)
    description = Column(String(200))
    updated_at = Column(DateTime, server_default=func.now(), onupdate=func.now())

    def __repr__(self):
        return f"<UserSettings(key={self.key}, value={self.value})>"


# ==================== 新闻分析模块 ====================

class NewsArticle(Base):
    """新闻原文表"""
    __tablename__ = "news_articles"

    id = Column(Integer, primary_key=True, autoincrement=True)
    article_id = Column(String(64), unique=True, nullable=False, index=True)  # hash(source+url) 去重
    symbol = Column(String(20), nullable=True, index=True)  # 关联股票，通用新闻为 null
    market = Column(String(10), nullable=False)  # a_share / general
    title = Column(String(500), nullable=False)
    content = Column(Text)  # 正文（A股有，Finnhub 仅 summary）
    summary = Column(Text)  # 摘要
    source = Column(String(100))  # 来源
    url = Column(String(1000))  # 原文链接
    image_url = Column(String(1000))  # 配图
    language = Column(String(5), default="zh")  # zh / en
    published_at = Column(DateTime, index=True)  # 发布时间
    fetched_at = Column(DateTime, server_default=func.now())  # 抓取时间

    __table_args__ = (
        Index('idx_news_symbol_published', 'symbol', 'published_at'),
        Index('idx_news_market_published', 'market', 'published_at'),
    )

    def __repr__(self):
        return f"<NewsArticle(id={self.article_id}, title={self.title[:30]})>"


class NewsSentiment(Base):
    """BERT 情感分析结果表"""
    __tablename__ = "news_sentiments"

    id = Column(Integer, primary_key=True, autoincrement=True)
    article_id = Column(String(64), ForeignKey("news_articles.article_id"), unique=True, nullable=False, index=True)
    sentiment = Column(String(10), nullable=False)  # positive / negative / neutral
    confidence = Column(Float, nullable=False)  # 置信度 0-1
    prob_positive = Column(Float)
    prob_negative = Column(Float)
    prob_neutral = Column(Float)
    model_used = Column(String(100))  # 模型名
    analyzed_at = Column(DateTime, server_default=func.now())

    def __repr__(self):
        return f"<NewsSentiment(article={self.article_id}, sentiment={self.sentiment}, conf={self.confidence})>"


class NewsAnalysis(Base):
    """OpenAI 新闻深度分析结果表"""
    __tablename__ = "news_analyses"

    id = Column(Integer, primary_key=True, autoincrement=True)
    analysis_id = Column(String(50), unique=True, nullable=False, index=True)
    analysis_type = Column(String(20), nullable=False)  # single / report
    article_id = Column(String(64), nullable=True)  # 单篇关联，报告为 null
    symbol = Column(String(20), nullable=True)  # 关联股票
    content = Column(Text)  # Markdown 分析内容
    model_used = Column(String(50))  # gpt-4o 等
    token_count = Column(Integer)  # token 用量
    status = Column(String(20), nullable=False, default="generating")  # generating / completed / failed
    created_at = Column(DateTime, server_default=func.now())

    def __repr__(self):
        return f"<NewsAnalysis(id={self.analysis_id}, type={self.analysis_type}, status={self.status})>"


# ==================== 自动化交易模块 ====================

class PendingOrder(Base):
    """待确认订单表 — 自动化流水线产生的待用户确认订单"""
    __tablename__ = "pending_orders"

    id = Column(Integer, primary_key=True, autoincrement=True)
    order_id = Column(String(50), unique=True, nullable=False, index=True)
    symbol = Column(String(20), nullable=False, index=True)
    name = Column(String(100))
    signal_type = Column(String(10), nullable=False)  # BUY / SELL
    strategy = Column(String(50))
    strength = Column(Float)

    # 建议交易参数
    suggested_price = Column(Float)
    suggested_quantity = Column(Integer)
    stop_loss = Column(Float)
    take_profit = Column(Float)
    reasons = Column(Text)  # JSON: [{indicator, detail}, ...]

    # 订单状态: PENDING → CONFIRMED → EXECUTING → FILLED / REJECTED / EXPIRED / FAILED
    status = Column(String(20), nullable=False, default="PENDING", index=True)
    broker_type = Column(String(20), default="paper")  # paper / easytrader

    # 执行结果
    actual_price = Column(Float)
    actual_quantity = Column(Integer)
    commission = Column(Float)
    reject_reason = Column(Text)

    # 风控
    risk_check_passed = Column(Integer, default=0)  # 0/1
    risk_check_detail = Column(Text)  # JSON

    # 来源与过期
    scan_source = Column(String(30))  # daily / intraday / position_check
    expire_at = Column(DateTime)
    order_expire_minutes = Column(Integer, default=30)

    # 时间戳
    confirmed_at = Column(DateTime)
    created_at = Column(DateTime, server_default=func.now(), index=True)
    updated_at = Column(DateTime, server_default=func.now(), onupdate=func.now())

    __table_args__ = (
        Index('idx_pending_symbol_status', 'symbol', 'status'),
        Index('idx_pending_created', 'created_at'),
    )

    def __repr__(self):
        return f"<PendingOrder(order_id={self.order_id}, symbol={self.symbol}, signal={self.signal_type}, status={self.status})>"


class AutomationConfig(Base):
    """自动化配置表 — 定义扫描任务参数"""
    __tablename__ = "automation_configs"

    id = Column(Integer, primary_key=True, autoincrement=True)
    config_id = Column(String(50), unique=True, nullable=False, index=True)
    name = Column(String(100), nullable=False)
    enabled = Column(Integer, default=1)  # 0/1

    # 扫描类型
    scan_type = Column(String(30), nullable=False)  # daily / intraday / position_check
    frequency_minutes = Column(Integer, default=5)

    # 策略与股票池
    strategies = Column(Text)  # JSON: ["MACD", "KDJ", ...]
    watchlist = Column(Text)   # JSON: ["600519", "000858", ...]
    min_strength = Column(Float, default=0.6)

    # 交易参数
    broker_type = Column(String(20), default="paper")  # paper / easytrader
    position_size_pct = Column(Float, default=0.10)  # 每笔仓位占比
    order_expire_minutes = Column(Integer, default=30)

    # 运行状态
    last_run_at = Column(DateTime)
    next_run_at = Column(DateTime)

    created_at = Column(DateTime, server_default=func.now())
    updated_at = Column(DateTime, server_default=func.now(), onupdate=func.now())

    def __repr__(self):
        return f"<AutomationConfig(config_id={self.config_id}, name={self.name}, enabled={self.enabled})>"


class AutomationLog(Base):
    """自动化运行日志表 — 每次扫描执行的记录"""
    __tablename__ = "automation_logs"

    id = Column(Integer, primary_key=True, autoincrement=True)
    config_id = Column(String(50), nullable=False, index=True)
    run_type = Column(String(30))  # daily / intraday / position_check
    status = Column(String(20), nullable=False, default="running")  # running / completed / failed

    # 统计
    symbols_scanned = Column(Integer, default=0)
    signals_found = Column(Integer, default=0)
    orders_created = Column(Integer, default=0)
    duration_seconds = Column(Float)

    # 详情
    error_message = Column(Text)
    detail = Column(Text)  # JSON

    started_at = Column(DateTime, server_default=func.now())
    completed_at = Column(DateTime)

    __table_args__ = (
        Index('idx_autolog_config', 'config_id'),
        Index('idx_autolog_started', 'started_at'),
    )

    def __repr__(self):
        return f"<AutomationLog(config_id={self.config_id}, run_type={self.run_type}, status={self.status})>"


# ==================== Alpha Lab 模块 ====================

class ClosedTrade(Base):
    """已平仓交易记录表 — 将买入和卖出配对，追踪完整交易周期"""
    __tablename__ = "closed_trades"

    id = Column(Integer, primary_key=True, autoincrement=True)
    symbol = Column(String(20), nullable=False, index=True)
    name = Column(String(100))

    # 买入侧
    buy_trade_id = Column(Integer, nullable=False, index=True)  # ManualTrade.id
    buy_date = Column(Date, nullable=False, index=True)
    buy_price = Column(Float, nullable=False)  # 加权平均成本
    buy_quantity = Column(Integer, nullable=False)
    buy_signal_strategy = Column(String(100))  # AI 策略名
    buy_signal_strength = Column(Float)  # 信号强度
    ai_stop_loss = Column(Float)
    ai_take_profit = Column(Float)

    # 大盘环境
    market_env = Column(String(20))  # bullish / neutral / bearish
    market_env_detail = Column(Text)  # JSON: 三指标投票详情

    # 卖出侧
    sell_trade_id = Column(Integer, nullable=False, index=True)  # ManualTrade.id
    sell_date = Column(Date, nullable=False, index=True)
    sell_price = Column(Float, nullable=False)
    sell_quantity = Column(Integer, nullable=False)
    sell_reason = Column(String(30))  # take_profit / stop_loss / manual_close

    # 收益统计
    holding_days = Column(Integer)
    pnl = Column(Float)  # 绝对盈亏（含佣金）
    pnl_pct = Column(Float)  # 收益率 %
    total_commission = Column(Float, default=0)

    # 基准对比
    benchmark_return_pct = Column(Float)  # 同期沪深300收益率 %
    excess_return_pct = Column(Float)  # 超额收益率 %

    created_at = Column(DateTime, server_default=func.now())

    __table_args__ = (
        Index('idx_closed_symbol_sell_date', 'symbol', 'sell_date'),
        Index('idx_closed_sell_reason', 'sell_reason'),
        Index('idx_closed_market_env', 'market_env'),
    )

    def __repr__(self):
        return f"<ClosedTrade(symbol={self.symbol}, buy={self.buy_date}, sell={self.sell_date}, pnl={self.pnl_pct}%)>"


class BatchBacktest(Base):
    """批量回测任务表"""
    __tablename__ = "batch_backtests"

    batch_id = Column(String(50), primary_key=True)
    name = Column(String(200))
    mode = Column(String(20), nullable=False)               # multi_symbol / multi_strategy / param_optimize
    status = Column(String(20), nullable=False, index=True)  # pending / running / completed / failed
    start_date = Column(Date, nullable=False)
    end_date = Column(Date, nullable=False)
    initial_capital = Column(Float, nullable=False)
    market = Column(String(20), default="a_share")
    config = Column(Text, nullable=False)                   # JSON: 模式特定配置
    total_tasks = Column(Integer, default=0)
    completed_tasks = Column(Integer, default=0)
    failed_tasks = Column(Integer, default=0)
    child_task_ids = Column(Text)                           # JSON list
    created_at = Column(DateTime, server_default=func.now(), index=True)
    started_at = Column(DateTime)
    completed_at = Column(DateTime)
    error_message = Column(Text)

    def __repr__(self):
        return f"<BatchBacktest(batch_id={self.batch_id}, mode={self.mode}, status={self.status})>"


class FinancialData(Base):
    """财务基本面数据表 — 按季度存储核心财务指标"""
    __tablename__ = "financial_data"

    id = Column(Integer, primary_key=True, autoincrement=True)
    symbol = Column(String(20), nullable=False, index=True)
    report_date = Column(Date, nullable=False, index=True)  # 报告期 (e.g. 2024-03-31)

    # 盈利能力
    eps = Column(Float)                  # 摊薄每股收益(元)
    eps_weighted = Column(Float)         # 加权每股收益(元)
    roe = Column(Float)                  # 净资产收益率(%)
    roe_weighted = Column(Float)         # 加权净资产收益率(%)
    roa = Column(Float)                  # 总资产利润率(%)
    gross_margin = Column(Float)         # 销售毛利率(%)
    net_margin = Column(Float)           # 销售净利率(%)
    operating_margin = Column(Float)     # 营业利润率(%)

    # 每股指标
    bvps = Column(Float)                 # 每股净资产(元)
    ocfps = Column(Float)               # 每股经营性现金流(元)
    capital_reserve_ps = Column(Float)   # 每股资本公积金(元)
    undistributed_ps = Column(Float)     # 每股未分配利润(元)

    # 成长能力
    revenue_yoy = Column(Float)          # 主营业务收入增长率(%)
    net_profit_yoy = Column(Float)       # 净利润增长率(%)
    net_asset_yoy = Column(Float)        # 净资产增长率(%)
    total_asset_yoy = Column(Float)      # 总资产增长率(%)

    # 偿债能力
    current_ratio = Column(Float)        # 流动比率
    quick_ratio = Column(Float)          # 速动比率
    debt_ratio = Column(Float)           # 资产负债率(%)
    equity_ratio = Column(Float)         # 股东权益比率(%)

    # 运营效率
    inventory_turnover = Column(Float)       # 存货周转率(次)
    inventory_turnover_days = Column(Float)  # 存货周转天数
    receivable_turnover_days = Column(Float) # 应收账款周转天数
    total_asset_turnover = Column(Float)     # 总资产周转率(次)

    # 费用相关
    cost_expense_ratio = Column(Float)   # 成本费用利润率(%)
    expense_ratio = Column(Float)        # 三项费用比重

    # 估值 & 规模（快照型，拉取时的值）
    total_assets = Column(Float)         # 总资产(元)
    revenue = Column(Float)              # 主营业务利润(元)

    # 元数据
    data_source = Column(String(20), default="sina")  # sina / eastmoney
    created_at = Column(DateTime, server_default=func.now())
    updated_at = Column(DateTime, server_default=func.now(), onupdate=func.now())

    __table_args__ = (
        Index('idx_financial_symbol_date', 'symbol', 'report_date', unique=True),
    )

    def __repr__(self):
        return f"<FinancialData(symbol={self.symbol}, date={self.report_date}, roe={self.roe}%)>"


class AlphaLabSession(Base):
    """Alpha Lab 会话表"""
    __tablename__ = "alpha_lab_sessions"

    id = Column(String(50), primary_key=True)                # alab_xxxxxxxxxxxx
    target_symbols = Column(Text, nullable=False)            # JSON: ["600519.SH", "AAPL"]
    optimization_goal = Column(String(20), nullable=False)   # sharpe/return/win_rate/drawdown
    data_start = Column(String(10), nullable=False)
    data_end = Column(String(10), nullable=False)
    max_iterations = Column(Integer, default=15)
    initial_capital = Column(Float, default=1000000.0)
    constraints = Column(Text)                               # JSON

    # 运行状态
    status = Column(String(20), default="running", index=True)  # running/paused/completed/failed
    total_iterations = Column(Integer, default=0)
    current_phase = Column(String(10), default="explore")    # explore/refine

    # 最佳结果
    best_iteration = Column(Integer)
    best_sharpe = Column(Float)
    best_composite_score = Column(Float)

    # 成本追踪
    total_tokens = Column(Integer, default=0)
    cost_usd = Column(Float, default=0.0)

    created_at = Column(DateTime, server_default=func.now())
    updated_at = Column(DateTime, server_default=func.now(), onupdate=func.now())
    completed_at = Column(DateTime)

    def __repr__(self):
        return f"<AlphaLabSession(id={self.id}, status={self.status}, best_sharpe={self.best_sharpe})>"


class AlphaLabStrategy(Base):
    """Alpha Lab 生成的策略"""
    __tablename__ = "alpha_lab_strategies"

    id = Column(String(80), primary_key=True)                # alab_xxx_iter1
    session_id = Column(String(50), ForeignKey("alpha_lab_sessions.id"), nullable=False, index=True)
    iteration = Column(Integer, nullable=False)

    # 策略代码
    code = Column(Text, nullable=False)
    ai_reasoning = Column(Text)

    # 回测指标（JSON）
    train_metrics = Column(Text)
    val_metrics = Column(Text)

    # 评估
    overfit_score = Column(Float)
    composite_score = Column(Float, index=True)
    walk_forward_results = Column(Text)                      # JSON

    # 状态
    status = Column(String(20), default="completed")         # completed/failed/rejected
    error_message = Column(Text)
    execution_time = Column(Float)                           # 秒

    # 部署
    deployed = Column(Integer, default=0)
    deployed_at = Column(DateTime)

    created_at = Column(DateTime, server_default=func.now())

    __table_args__ = (
        Index('idx_alab_strat_session_score', 'session_id', 'composite_score'),
    )

    def __repr__(self):
        return f"<AlphaLabStrategy(id={self.id}, iter={self.iteration}, score={self.composite_score})>"


class AlphaLabLog(Base):
    """Alpha Lab 运行日志"""
    __tablename__ = "alpha_lab_logs"

    id = Column(Integer, primary_key=True, autoincrement=True)
    session_id = Column(String(50), ForeignKey("alpha_lab_sessions.id"), nullable=False, index=True)
    iteration = Column(Integer)
    level = Column(String(10), default="INFO")
    event = Column(String(50), nullable=False)
    message = Column(Text)
    details = Column(Text)                                   # JSON

    created_at = Column(DateTime, server_default=func.now())

    __table_args__ = (
        Index('idx_alab_log_session', 'session_id', 'created_at'),
    )

    def __repr__(self):
        return f"<AlphaLabLog(session={self.session_id}, event={self.event})>"


class Watchlist(Base):
    """自选股表"""
    __tablename__ = "watchlist"

    id = Column(Integer, primary_key=True, autoincrement=True)
    symbol = Column(String(20), nullable=False, index=True)
    name = Column(String(100))
    group_name = Column(String(50), default="默认分组", index=True)  # 分组
    note = Column(Text)                                              # 备注
    sort_order = Column(Integer, default=0)
    created_at = Column(DateTime, server_default=func.now())

    __table_args__ = (
        Index('idx_watchlist_group_symbol', 'group_name', 'symbol', unique=True),
    )

    def __repr__(self):
        return f"<Watchlist(symbol={self.symbol}, group={self.group_name})>"


class PriceAlert(Base):
    """价格预警表"""
    __tablename__ = "price_alerts"

    id = Column(Integer, primary_key=True, autoincrement=True)
    alert_id = Column(String(50), nullable=False, unique=True, index=True)  # pa_xxxxxxxxxxxx
    symbol = Column(String(20), nullable=False, index=True)
    name = Column(String(100))
    # price_above=突破价上穿 / price_below=跌破价下穿 / pct_change=当日涨跌幅达到
    alert_type = Column(String(20), nullable=False)
    threshold = Column(Float, nullable=False)        # 阈值（价格 或 涨跌幅%）
    status = Column(String(20), default="active", index=True)  # active/triggered/paused
    triggered_at = Column(DateTime)
    triggered_price = Column(Float)
    message = Column(Text)
    repeat = Column(Integer, default=0)              # 0=仅触发一次 1=反复触发
    last_notified_at = Column(DateTime)             # 防抖用
    created_at = Column(DateTime, server_default=func.now())
    updated_at = Column(DateTime, server_default=func.now(), onupdate=func.now())

    __table_args__ = (
        Index('idx_alert_symbol_status', 'symbol', 'status'),
    )

    def __repr__(self):
        return f"<PriceAlert(symbol={self.symbol}, type={self.alert_type}, threshold={self.threshold})>"


class StockValuation(Base):
    """估值快照表 — 全市场每日估值因子（与季度财报 financial_data 解耦）"""
    __tablename__ = "stock_valuations"

    id = Column(Integer, primary_key=True, autoincrement=True)
    symbol = Column(String(20), nullable=False, index=True)
    snapshot_date = Column(Date, nullable=False, index=True)

    pe = Column(Float)              # 市盈率（静态，f9）
    pe_ttm = Column(Float)          # 市盈率 TTM（f115）
    pb = Column(Float)              # 市净率（f23）
    ps = Column(Float)              # 市销率
    total_mv = Column(Float)        # 总市值（元，f20）
    circ_mv = Column(Float)         # 流通市值（元，f21）
    dividend_yield = Column(Float)  # 股息率(%)

    data_source = Column(String(20), default="eastmoney")
    created_at = Column(DateTime, server_default=func.now())
    updated_at = Column(DateTime, server_default=func.now(), onupdate=func.now())

    __table_args__ = (
        Index('idx_valuation_symbol_date', 'symbol', 'snapshot_date', unique=True),
    )

    def __repr__(self):
        return f"<StockValuation(symbol={self.symbol}, date={self.snapshot_date}, pe={self.pe}, pb={self.pb})>"

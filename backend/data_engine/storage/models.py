"""
数据库模型定义
"""
from sqlalchemy import Column, String, Float, Integer, DateTime, Date, Index, ForeignKey, Text
from sqlalchemy.sql import func
from common.market_time import utc_now
from data_engine.storage.database import Base

# `default=utc_now` —— 列默认值一律 **naive UTC**，见 docs/CODING_STANDARDS.md §11。
#
# ## 这里曾经修反过一次（2026-07-22 纠正）
#
# 原来是 `_local_now()`（本地时间），理由写的是「server_default=func.now() 在 SQLite
# 上落 UTC，而本项目业务时间都是本地时间，同表混存差 8 小时」。**症状看对了，方向选反了。**
#
# 同一行内口径一致确实是必须的，但该被拉齐的是**本地那一侧**：
#   - 全库 40 张表的 `created_at` 本来就是 `server_default=func.now()` = UTC，
#     `_local_now` 反而是唯一的两个异类；
#   - crypto 数据链（`daily_quotes`(crypto) / `crypto_bars` / `crypto_metrics` /
#     `crypto_fills` / 两个 APScheduler）本来就全是 UTC；
#   - 本地时间只在「服务器时区 == 用户时区」时才对，换台机器就全错，而且跨市场
#     聚合时没有任何办法对齐。
#
# 「今天」按谁算的问题，答案不是「服务器本地」而是「**该市场自己的时区**」——
# 那是 `common.market_time.market_today(market)` 的职责，不该由存储层的默认值来表达。


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


class LimitUpPool(Base):
    """每日涨停/炸板/跌停板行情快照（一天一次，盘后写入，三池合一用 pool_type 区分）"""
    __tablename__ = "limit_up_pool"

    id = Column(Integer, primary_key=True, autoincrement=True)
    trade_date = Column(Date, nullable=False, index=True)
    symbol = Column(String(20), nullable=False, index=True)
    name = Column(String(100))
    pool_type = Column(String(10), nullable=False)  # zt(今日涨停) / zb(炸板) / dt(跌停) / previous(昨日涨停)

    change_pct = Column(Float)          # 涨跌幅 %（previous 池代表"次日表现"）
    price = Column(Float)               # 最新价
    limit_price = Column(Float)         # 涨停价/跌停价（zt 池无此字段）
    amount = Column(Float)              # 成交额
    turnover = Column(Float)            # 换手率 %
    amplitude = Column(Float)           # 振幅 %（zb/previous 池有，zt/dt 池无）
    speed = Column(Float)               # 涨速 %（zb/previous 池有）
    circulating_mv = Column(Float)      # 流通市值
    total_mv = Column(Float)            # 总市值

    seal_amount = Column(Float)         # 封板资金（zt/dt 池有）
    first_seal_time = Column(String(10))   # 首次封板时间 HHMMSS（zt/zb 池），或 previous 池的"昨日封板时间"
    last_seal_time = Column(String(10))    # 最后封板时间（zt/dt 池）
    break_count = Column(Integer)       # 炸板次数（zt/zb 池）
    consecutive_boards = Column(Integer)  # 连板数（zt 池"连板数"/previous 池"昨日连板数"）
    zt_stat_days = Column(Integer)      # 涨停统计：窗口天数（近 N 个交易日）
    zt_stat_count = Column(Integer)     # 涨停统计：窗口内涨停次数

    industry = Column(String(50))       # 所属行业（题材热度代理）

    created_at = Column(DateTime, server_default=func.now())

    __table_args__ = (
        Index('idx_limitup_date_type', 'trade_date', 'pool_type'),
        Index('idx_limitup_symbol_date', 'symbol', 'trade_date'),
    )

    def __repr__(self):
        return f"<LimitUpPool(symbol={self.symbol}, date={self.trade_date}, type={self.pool_type})>"


class LimitUpPrediction(Base):
    """涨停候选池打分结果（一天一次，盘后跑规则打分模型后写入）。

    候选 universe 只来自"当前未封板、能正常买入"的股票（强势股池/自建准涨停扫描/
    昨涨停今仍强势），已封板的今日涨停池不进这张表，只作为环境变量输入打分。
    """
    __tablename__ = "limit_up_predictions"

    id = Column(Integer, primary_key=True, autoincrement=True)
    predict_date = Column(Date, nullable=False, index=True)   # 预测生成日（今日盘后）
    target_date = Column(Date, nullable=False, index=True)    # 预测目标日（下一交易日）
    symbol = Column(String(20), nullable=False, index=True)
    name = Column(String(100))

    score = Column(Float, nullable=False)   # 综合打分 0-1，对齐 Signal.strength 语义
    rank = Column(Integer)                  # 当日候选池内排名（按 score 降序）
    source = Column(String(20))             # 候选来源：strong(强势股池) / quasi(自建准涨停扫描) / continuation(昨涨停今仍强势)

    momentum_score = Column(Float)      # 动能强度分 0-100
    capital_score = Column(Float)       # 资金强度分 0-100
    theme_score = Column(Float)         # 题材热度分 0-100
    sentiment_score = Column(Float)     # 大盘情绪分 0-100（当日全候选股共享）
    continuation_score = Column(Float)  # 强势延续分 0-100

    reasons = Column(Text)   # JSON: [{"indicator": "...", "detail": "..."}...]，对齐 Signal.reasons 风格

    # 实际结果回填（次日收盘后补，用于后续复盘/权重校准，MVP 阶段不强制要求）
    actual_result = Column(String(10))   # limit_up / up / flat / down
    actual_change_pct = Column(Float)

    model_version = Column(String(20), default="rule_v1")  # 预留：未来接 ML 时区分版本
    created_at = Column(DateTime, server_default=func.now())

    __table_args__ = (
        Index('idx_ltp_target_date', 'target_date'),
        Index('idx_ltp_predict_date_symbol', 'predict_date', 'symbol', unique=True),
        Index('idx_ltp_score', 'score'),
    )

    def __repr__(self):
        return f"<LimitUpPrediction(symbol={self.symbol}, target_date={self.target_date}, score={self.score})>"


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


class TradingCalendar(Base):
    """交易日历 —— 「这一天该不该有数据」的真源。

    ## 为什么单独一张表，不落进 daily_quotes

    日历是靠**基准指数自证**建立的（指数那天有 bar ⟺ 那天是交易日）。如果把
    `^HSI` / `^GSPC` 的 bar 写进 `daily_quotes(market=hk_stock)`，它会**污染覆盖率
    统计的分子** —— `health._day_counts` 数的是 `distinct symbol`，而分母
    （`StockInfo`）里根本没有这两个指数。分子分母口径不对称，正是「日线覆盖率
    300% bug」那一类事故的机理（见 docs/GOTCHAS.md）。

    而且它本来也不是行情，是日历：只需要「哪天开市」这一个 bit，不需要 OHLCV。

    ## crypto 不入表

    7×24 无休市，交易日 = 自然日，纯计算即可，存进来只是浪费行。
    `trading_calendar.trading_days()` 对 crypto 直接生成自然日序列。
    """
    __tablename__ = "trading_calendar"

    id = Column(Integer, primary_key=True, autoincrement=True)
    market = Column(String(20), nullable=False, index=True)
    cal_date = Column(Date, nullable=False, index=True)
    # 这一天是怎么被确认为交易日的："index:000001.SH" / "index:^HSI" / "quotes"（从
    # 存量日线自举）/ "manual"。排障时能一眼看出日历是哪来的。
    source = Column(String(40), nullable=False)
    created_at = Column(DateTime, default=utc_now)

    __table_args__ = (
        Index("idx_trading_calendar_market_date", "market", "cal_date", unique=True),
    )

    def __repr__(self):
        return f"<TradingCalendar({self.market} {self.cal_date})>"


class DataGap(Base):
    """数据缺口 —— 「这一天本该有数据但没有」。

    ## 为什么要落库而不是算完就扔

    1. **`permanent` 状态必须持久化**。项目里没有第三方交易日历，判「哪天是假期」
       靠的是「补了 N 次仍然一行都拉不到」的反证。这个结论不存下来，每次后端启动
       都会重试同一个补不上的洞 —— 而港美股一次误补要白打 10-15 分钟 Yahoo。
       **换句话说：这张表 = 实测出来的交易日历补丁。**
    2. 前端缺口区要展示「缺哪几天」，不能每次都重扫全库。
    3. 补齐进度要可续（补到一半被停止，下次接着补）。

    ## status 状态机

        open ──(开始补)──> filling ──(补到数据)──> filled
                              │
                              └──(attempts >= MAX)──> permanent（认定为假期/源无数据）

        suspected —— 没有日历可依据、只靠「工作日」启发式推断出来的疑似缺口。
                     ⛔ **只展示，绝不自动补**（误判代价不对称，见 00-PLAN §四）。
    """
    __tablename__ = "data_gaps"

    id = Column(Integer, primary_key=True, autoincrement=True)
    # 资产 key（registry 里的 DataAsset.key，如 "daily.a_share"）
    asset = Column(String(40), nullable=False, index=True)
    market = Column(String(20), nullable=False, index=True)
    gap_date = Column(Date, nullable=False, index=True)

    # open / filling / filled / permanent / suspected
    status = Column(String(20), nullable=False, default="open", index=True)
    # certain（基准指数自证）/ suspected（工作日启发式）
    confidence = Column(String(20), nullable=False, default="certain")

    attempts = Column(Integer, nullable=False, default=0)
    # 检出时该日实际有多少只票（0 = 整天空，>0 = 残缺）。排障时区分「没跑」和「跑残了」
    observed_count = Column(Integer)
    expected_count = Column(Integer)

    detected_at = Column(DateTime, default=utc_now)
    last_attempt_at = Column(DateTime)
    filled_at = Column(DateTime)
    note = Column(Text)

    __table_args__ = (
        Index("idx_data_gap_asset_date", "asset", "gap_date", unique=True),
        Index("idx_data_gap_status", "status", "market"),
    )

    def __repr__(self):
        return f"<DataGap({self.asset} {self.gap_date} {self.status})>"


class Signal(Base):
    """交易信号表"""
    __tablename__ = "signals"

    id = Column(Integer, primary_key=True, autoincrement=True)
    symbol = Column(String(20), nullable=False, index=True)
    name = Column(String(100))
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
    name = Column(String(100))
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
    name = Column(String(100))
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


# AutomationConfig / AutomationLog（A 股自动交易调度器的配置/日志表）已于 2026-07-21 随
# A 股自动交易整块退役而删除（券商 easytrader 不可用，改 crypto 半自动策略引擎）。
# 旧表 automation_configs / automation_logs 留库无妨、不迁移；PendingOrder 保留（复盘仍读）。


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


class PositionReconciliation(Base):
    """持仓对账记录表 —— 每次"系统持仓 vs 券商真实持仓"对账的快照与处理结果（可审计）"""
    __tablename__ = "position_reconciliations"

    id = Column(Integer, primary_key=True, autoincrement=True)
    reconcile_date = Column(Date, nullable=False, index=True)  # 对账基准日
    source = Column(String(20), default="manual")             # manual / csv
    status = Column(String(20), default="applied")            # applied（已应用纠正）

    # 差异汇总: {"match": n, "qty_mismatch": n, "cost_mismatch": n, "system_only": n, "broker_only": n}
    summary = Column(Text)
    # 逐股差异明细快照（JSON: [{symbol, system_qty, actual_qty, system_avg, actual_avg, kind, ...}]）
    details = Column(Text)
    # 本次应用的调整交易: JSON [{symbol, side, quantity, price, manual_trade_id}]
    adjustments = Column(Text)
    note = Column(Text)

    created_at = Column(DateTime, server_default=func.now())

    def __repr__(self):
        return f"<PositionReconciliation(date={self.reconcile_date}, status={self.status})>"


class DecisionLog(Base):
    """决策留痕表 —— 每条 AI 产生的买卖建议的可复现、可归因记录。

    活跃 source：advisor / cockpit / moneybill / moneybill_recommend / report_picks /
    crypto / crypto_cockpit / crypto_earn。

    ⚠️ 这张表里躺着**三类语义完全不同**的行，靠 `entry_kind` 区分（见该列注释）——
    只有 `advice` 那类进后验评估。规则信号已有 Signal 表故不重复记。
    """
    __tablename__ = "decision_logs"

    id = Column(Integer, primary_key=True, autoincrement=True)
    decision_id = Column(String(40), unique=True, index=True)   # UUID
    created_at = Column(DateTime, server_default=func.now(), index=True)

    source = Column(String(20), nullable=False, index=True)     # advisor / cockpit / moneybill
    # 记的是**哪一类事**（P0-4）。取值见 common/decision_kind.py，只有 advice 进
    # 后验评估 / 胜率 / 置信度校准：
    #   advice    —— AI 的可证伪断言（「买茅台，入场 1650」）。P0-1 评的就是它。
    #   execution —— 订单回执（成交 / 挂单 / 补录真实成交）。**已发生的事实，没有对错可评**。
    #   ops       —— 非交易操作（加自选股 / 建预警 / 改设置 / 编策略）。连价格都没有。
    # ⛔ **别再用 source 去区分这件事**：source 是「谁写的」，entry_kind 是「写的是什么」。
    # 靠 source 命名约定分类正是 P0-4 那颗炸弹的成因 —— crypto 的成交回执与 AI 建议
    # 共用一个 source 域，39 条 entry=100 的假成交排着队等被评成 +64900% 的 win。
    entry_kind = Column(String(12), default="advice")
    symbol = Column(String(20), index=True)
    name = Column(String(100))
    action = Column(String(12))          # BUY / SELL / HOLD / AGGREGATE
    recommendation = Column(String(12))  # BUY / HOLD / SELL（驾驶舱评级）
    confidence = Column(Float)           # 驾驶舱综合分(0-100) 或其它置信度

    # 建议动作细节
    entry_price = Column(Float)
    stop_loss = Column(Float)
    take_profit = Column(Float)
    position_pct = Column(Float)

    # 溯源
    model_id = Column(String(50))        # 模型名 或 'rule:cockpit_scorer'
    prompt_version = Column(String(30))
    input_snapshot = Column(Text)        # JSON：产生该建议时喂进去的数据
    reasons = Column(Text)               # JSON / 文本：理由
    output_text = Column(Text)           # LLM 全文 / 执行摘要
    output_summary = Column(Text)        # JSON：结构化结果（五维分/执行详情等）

    # 成本
    prompt_tokens = Column(Integer, default=0)
    completion_tokens = Column(Integer, default=0)
    total_tokens = Column(Integer, default=0)
    cost_usd = Column(Float, default=0.0)
    latency_ms = Column(Integer)

    # 关联 / 交易执行
    session_id = Column(String(50), index=True)
    turn_start_idx = Column(Integer)           # 定位 agent_traces/{session_id}.jsonl 里产生该决策的那个 turn
    executed = Column(Integer, default=0)      # moneybill 是否真的成交 0/1
    risk_passed = Column(Integer)              # moneybill 风控是否通过 0/1

    # 后验评估（P0-1）—— 由 decision_log.backfill_outcomes() 回填，判定内核见
    # common/outcome_eval.py。上面所有列是「当时怎么说的」，下面这些是「后来对不对」。
    # ⚠️ 上面的列**不可篡改**（decision_log._IMMUTABLE_REFRESH_FIELDS 钉死）——
    # 改了当时的止损再去算胜率，等于给自己发奖状。
    return_5d = Column(Float)                  # 收益率%，符号已按方向归一（SELL 跌对了也是正）
    return_20d = Column(Float)
    outcome_5d = Column(String(10))            # win / loss / neutral
    outcome_20d = Column(String(10))
    hit_stop = Column(Integer)                 # 0/1；None = 建议里没写止损位，无从判起
    hit_target = Column(Integer)
    first_hit = Column(String(12))             # stop_loss / take_profit / ambiguous / none
    first_hit_days = Column(Integer)
    outcome_status = Column(String(20), index=True)  # completed/pending/unable；NULL=还没评过
    unable_reason = Column(String(30))         # 没法评的原因；可重试性见 outcome_eval.is_retryable
    engine_version = Column(String(30))        # 判定口径版本；不打戳历史结果会随代码演进悄悄漂移
    evaluated_at = Column(DateTime)

    __table_args__ = (
        # 吃这个索引的是 get_decision_stats 的胜率聚合（MoneyBill 每次问胜率的热路径），
        # 不是回填扫描（那个是低选择度全表扫，几百行无所谓）。等值列在前、范围列在后。
        # ⚠️ 这里只对 create_all 建的新库生效（测试内存库走这条）；**存量 market.db
        # 一个字节都不会动**，生产库的索引靠 database.py:init_db() 里手写的
        # CREATE INDEX IF NOT EXISTS。两处都要有，漏一处就只有一半环境有索引。
        Index("idx_decision_outcome_scan", "outcome_status", "created_at"),
    )

    def __repr__(self):
        return f"<DecisionLog(source={self.source}, symbol={self.symbol}, action={self.action})>"


# ── 加密货币情报层（排雷/择时）专用表 ──────────────────────────────────────
# 行情复用 DailyQuote/StockInfo（market='crypto'）；下面三张装股票没有的加密独有维度。
# symbol 统一带 `.BN` 后缀（与 DailyQuote 一致）；市场级指标用 symbol='MARKET' 哨兵。

class CryptoMetric(Base):
    """加密时间序列指标 —— 装衍生品/链上/情绪等按日的单值指标。

    宽表按 (symbol, date, metric) 唯一。metric 例：fear_greed / btc_dominance /
    stablecoin_supply / tvl / funding_rate / open_interest / long_short_ratio。
    市场级指标（恐慌贪婪/主导率/稳定币供应）用 symbol='MARKET'。
    """
    __tablename__ = "crypto_metrics"

    id = Column(Integer, primary_key=True, autoincrement=True)
    symbol = Column(String(20), nullable=False, index=True)   # BTCUSDT.BN 或 'MARKET'
    market = Column(String(20), nullable=False, default="crypto", index=True)
    date = Column(Date, nullable=False, index=True)
    metric = Column(String(40), nullable=False, index=True)
    value = Column(Float)
    source = Column(String(30))                               # binance/defillama/coingecko/alternative.me/github
    created_at = Column(DateTime, server_default=func.now())

    __table_args__ = (
        Index("idx_crypto_metric_uniq", "symbol", "date", "metric", unique=True),
    )

    def __repr__(self):
        return f"<CryptoMetric({self.symbol} {self.date} {self.metric}={self.value})>"


class TokenUnlock(Base):
    """代币解锁时间表 —— 大量解锁常是砸盘前兆（排雷层核心信号之一）。

    主流币（BTC/ETH）无解锁，此表主要给山寨。按 (symbol, unlock_date, category) 唯一。
    """
    __tablename__ = "token_unlocks"

    id = Column(Integer, primary_key=True, autoincrement=True)
    symbol = Column(String(20), nullable=False, index=True)
    unlock_date = Column(Date, nullable=False, index=True)
    amount = Column(Float)                                    # 解锁数量（币）
    pct_of_supply = Column(Float)                             # 占流通供应百分比
    category = Column(String(40))                             # team/investors/ecosystem/...
    source = Column(String(30), default="defillama")
    created_at = Column(DateTime, server_default=func.now())
    updated_at = Column(DateTime, server_default=func.now(), onupdate=func.now())

    __table_args__ = (
        Index("idx_token_unlock_uniq", "symbol", "unlock_date", "category", unique=True),
    )

    def __repr__(self):
        return f"<TokenUnlock({self.symbol} {self.unlock_date} {self.pct_of_supply}%)>"


class CryptoAsset(Base):
    """代币经济学静态快照 —— 供应机制/稀释风险/集中度（排雷层「会不会被稀释」）。

    一 symbol 一行，每次刷新覆盖。max_supply=None 表示无上限（无限增发，稀释风险）。
    """
    __tablename__ = "crypto_assets"

    symbol = Column(String(20), primary_key=True)            # BTCUSDT.BN
    base_asset = Column(String(20))                          # BTC
    name = Column(String(100))
    coingecko_id = Column(String(60))
    circulating_supply = Column(Float)
    max_supply = Column(Float)                               # None = 无上限
    total_supply = Column(Float)
    market_cap = Column(Float)
    fdv = Column(Float)                                      # 全稀释市值
    inflation_flag = Column(Integer)                         # 1=可能增发/无上限；0=固定总量
    github_repo = Column(String(120))                        # owner/repo，供开发活跃度用
    updated_at = Column(DateTime, server_default=func.now(), onupdate=func.now())

    def __repr__(self):
        return f"<CryptoAsset({self.symbol} mcap={self.market_cap})>"


class CryptoBar(Base):
    """加密日内 K 线（4h/1h）—— 多周期确认专用，**不与 `daily_quotes` 混住**。

    为什么单独一张表：`daily_quotes` 是 A股/港股/美股/crypto 四市场共用的**日线**表，
    主键语义是 (symbol, date)，塞日内 K 线会破坏它的日线语义与全部下游统计。加密是
    7×24 市场、策略每 30 分钟 tick，需要比日线更细的周期来定扣扳机时机。

    `taker_buy_ratio` 是币安 kline 自带的主动买入占比（>0.5 买方主动吃单），免费的
    现货买压真数据，多数实现都把它丢了。
    """
    __tablename__ = "crypto_bars"

    id = Column(Integer, primary_key=True, autoincrement=True)
    symbol = Column(String(20), nullable=False, index=True)   # BTCUSDT.BN
    interval = Column(String(10), nullable=False, index=True)  # 4h / 1h
    open_time = Column(DateTime, nullable=False, index=True)   # UTC 开盘时刻

    open = Column(Float, nullable=False)
    high = Column(Float, nullable=False)
    low = Column(Float, nullable=False)
    close = Column(Float, nullable=False)
    volume = Column(Float, nullable=False)

    quote_volume = Column(Float)          # 成交额（USDT）
    trades = Column(Integer)
    taker_buy_ratio = Column(Float)       # 主动买入量/总量，None=零成交（不造假中性）
    created_at = Column(DateTime, server_default=func.now())

    __table_args__ = (
        Index("idx_crypto_bar_uniq", "symbol", "interval", "open_time", unique=True),
    )

    def __repr__(self):
        return f"<CryptoBar({self.symbol} {self.interval} {self.open_time} close={self.close})>"


class CryptoTrade(Base):
    """币安现货成交台账 —— crypto 独立成交记录（与 A 股 ManualTrade 分书本）。

    只在**真实成交**（filled_quantity > 0）时落一行；挂盘未成交的限价单不记。
    用途：给 RiskManager 的「连亏 3 次暂停」硬风控回放已实现盈亏（币安不给成本价，
    盈亏靠本表加权成本另算），后续 crypto 持仓/对账也复用。数量是小数（0.0015 BTC），
    故 quantity 用 Float（区别于 ManualTrade 面向 A 股整手的 Integer）。
    """
    __tablename__ = "crypto_trades"

    id = Column(Integer, primary_key=True, autoincrement=True)
    symbol = Column(String(20), nullable=False, index=True)   # BTCUSDT.BN
    side = Column(String(10), nullable=False)                 # BUY / SELL
    price = Column(Float, nullable=False)                     # 成交价（USDT）
    quantity = Column(Float, nullable=False)                  # 成交币量（小数）
    amount = Column(Float, nullable=False)                    # 成交额 = price * quantity
    commission = Column(Float, default=0)
    order_id = Column(String(50), nullable=True, index=True)  # 币安 orderId
    trade_date = Column(Date, nullable=False, index=True)
    # ── 来源归因（S1）：这笔单是从哪儿来的。真源 `common/trade_source.py` ──
    # 🔴 **必须写在成交台账本身**，不能靠事后 join：策略排的单只在 `CryptoPendingOrder`
    # 里留桥接，而那张表的 FILLED 行 24 小时后就被 `pending.cleanup()` 删掉 ——
    # 超期之后「这笔成交属于哪条策略」永久查不回来。
    # `source_ref`：strategy → 策略号 `CS-…`；ai_advice → decision_id（S4 接）。
    source_kind = Column(String(16), default="unknown", index=True)
    source_ref = Column(String(64))
    created_at = Column(DateTime, server_default=func.now())

    __table_args__ = (
        Index("idx_crypto_trade_symbol_date", "symbol", "trade_date"),
        Index("idx_crypto_trade_source", "source_kind", "source_ref"),
    )

    def __repr__(self):
        return f"<CryptoTrade({self.symbol} {self.side} {self.quantity}@{self.price})>"


class CryptoFill(Base):
    """币安**原始成交明细**（`GET /api/v3/myTrades` 的逐笔回执）—— 持仓成本的唯一真源。

    ## 为什么要这张表（而不是复用 CryptoTrade）

    币安**没有**任何返回「持仓成本」的端点：`/api/v3/account`、资金钱包、Simple Earn
    三处全是纯余额。币安 App 里那个成本价是**客户端自己按成交历史回放算出来的**，
    我们也只能这么做（官方社区专帖 "Determining Cost Basis from Trade History" 是同一结论）。

    `crypto_trades`（`CryptoTrade`）记的是「**本系统下的单**」，语义是台账；Jason 在币安
    App 里手动买卖的、以及接入本系统之前的历史，它一概不知道 —— 拿它算成本会漏掉大半。
    本表存的是币安侧的客观事实，两张表**语义不同、不可互相替代**，故分立。

    ## 幂等

    `(source, symbol, trade_id)` 唯一。全量回填按 `fromId` 翻页可以反复跑，撞键即跳过。
    ⚠️ `myTrades` 的 `startTime/endTime` **跨度不能超 24 小时**，所以拉全历史必须走
    `fromId` 翻页，不能用时间窗切片。

    ## 覆盖边界（`source` 字段的意义）

    - `spot`    ：`/api/v3/myTrades` 的现货成交，绝大多数交易走这里。
    - `convert` ：币安「闪兑」**不进 myTrades**，得单独拉 `/sapi/v1/convert/tradeFlow`。
    链上充值、空投、理财利息、法币一键买币等来源**本就没有成本**，谁也算不出来 ——
    这类缺口由 `cost_basis` 的覆盖度三态如实标注，绝不拿现价冒充成本。
    """
    __tablename__ = "crypto_fills"

    id = Column(Integer, primary_key=True, autoincrement=True)
    source = Column(String(12), nullable=False, default="spot")   # spot / convert
    symbol = Column(String(20), nullable=False, index=True)       # BTCUSDT.BN
    trade_id = Column(String(40), nullable=False)                 # 币安 tradeId（同 symbol 内唯一）
    order_id = Column(String(50), index=True)

    price = Column(Float, nullable=False)
    quantity = Column(Float, nullable=False)      # 成交币量（base）
    quote_qty = Column(Float)                     # 成交额（quote），币安直接给，不自己乘
    commission = Column(Float, default=0.0)
    commission_asset = Column(String(20))         # ⚠️ 可能是 base/quote/BNB，单位不同不可混加
    is_buyer = Column(Integer, nullable=False)    # 1=买入 0=卖出

    trade_time = Column(DateTime, nullable=False, index=True)   # **UTC**（币安 ms 时间戳）
    created_at = Column(DateTime, server_default=func.now())

    __table_args__ = (
        Index("idx_crypto_fill_uniq", "source", "symbol", "trade_id", unique=True),
        Index("idx_crypto_fill_symbol_time", "symbol", "trade_time"),
    )

    def __repr__(self):
        side = "BUY" if self.is_buyer else "SELL"
        return f"<CryptoFill({self.symbol} {side} {self.quantity}@{self.price})>"


class CryptoStrategy(Base):
    """crypto 半自动交易策略（规则对象）—— MoneyBill 把 Jason 人话编译成的确定性 DSL。

    需求3（半自动）：后台引擎按 tick 读取、评估、**自动产决策+排队待确认**（`CryptoPendingOrder`），
    每笔仍由 Jason 逐笔点确认才成交——CLAUDE.md 逐笔确认红线原样保留、不动。
    DSL 子对象以 JSON 串存 Text 列（对齐仓库惯例，无 JSON 列类型）。
    lifecycle：draft → backtested →（arm）→ armed →（护栏触发）paused_by_guardrail / retired。
    ⛔ live 前必须 `backtest_passed=1`（含真实费率的净费回测通过）才允许 arm。
    """
    __tablename__ = "crypto_strategies"

    id = Column(Integer, primary_key=True, autoincrement=True)
    strategy_id = Column(String(40), nullable=False, unique=True, index=True)  # CS-<ts>-<hex6>
    # ── 版本化（S2）：**一个版本一行**，`family_id` 把同一条策略的历代版本串起来 ──
    # 为什么不是「单行 + 版本历史表」：`crypto_strategy_runs` 和 `CryptoTrade.source_ref`
    # 都是按 `strategy_id` 链的，一版一行 → 战绩天然跟着版本走，「v1 vs v2 谁强」直接
    # 就是两条策略比（正是 S3 竞技场要的形状）。反之要给 runs 和 trades 各加 version 字段。
    # 存量行迁移时 `family_id = strategy_id`、`version = 1`。
    family_id = Column(String(40), index=True)
    version = Column(Integer, default=1)
    forked_from = Column(String(40))                   # 从哪个版本 fork 出来的（血缘）
    # ⭐ 裁决 8（S3）：Jason 手写的策略永久置顶当**基准线**。
    # **没有基准线的胜率是自说自话** —— 只有 AI 能写策略的话，就永远不知道 AI 有没有价值。
    # 基准线不参与切换（永不被淘汰、也永不上位），只在排行里当尺子。
    is_benchmark = Column(Integer, default=0)
    name = Column(String(100), nullable=False)
    description_nl = Column(Text)                      # Jason 原始人话（审计留痕）
    enabled = Column(Integer, default=0, index=True)   # 默认 0，绝不建时自动武装
    mode = Column(String(10), default="paper")         # paper | live
    # draft|backtested|armed|paused_by_guardrail|superseded|retired
    #   superseded 同 family 的新版本 arm 之后，旧版本的终态（**不删，战绩要留着比**）
    #   retired    软退役：停跑、清待确认单，但 `crypto_strategy_runs` 一行不删
    status = Column(String(24), default="draft")
    strategy_kind = Column(String(12), default="swing")  # swing | arb | long_hold
    interval_minutes = Column(Integer, default=30)     # 每策略 tick 节奏

    # ── DSL 子对象（JSON 串）──
    universe = Column(Text)          # {"symbols":[...], "screen_filter":{...}|null}
    entry_rules = Column(Text)       # {"when":{...}, "cooldown_minutes":60}
    exit_rules = Column(Text)        # {"when":{...}, "use_atr_stop":true, "use_take_profit":true}
    position_policy = Column(Text)   # {"target_pct_source":..., "max_position_pct":..., ...}
    cost_model = Column(Text)        # {"taker_fee_pct":0.001, "slippage_pct":0.0005, "min_net_edge_pct":...}
    guardrails = Column(Text)        # {"per_order_notional_usdt":..., "max_orders_per_day":..., ...}

    capital_basis = Column(String(20), default="config")  # config | real_total_value

    # ── 上线前回测闸（arm 的前置硬条件）──
    last_backtest_at = Column(DateTime)
    backtest_net_return = Column(Float)                # 净费回报（小数，0.12=12%）
    backtest_metrics = Column(Text)                    # {"sharpe":..,"max_drawdown":..,"win_rate":..,"degraded":..}
    backtest_passed = Column(Integer, default=0)

    # ── 运行态 ──
    last_run_at = Column(DateTime)
    next_run_at = Column(DateTime)
    last_signal_at = Column(DateTime)
    consecutive_guardrail_trips = Column(Integer, default=0)
    halted_reason = Column(String(200))

    created_at = Column(DateTime, server_default=func.now())
    updated_at = Column(DateTime, server_default=func.now(), onupdate=func.now())

    __table_args__ = (
        Index("idx_crypto_strategy_enabled_mode", "enabled", "mode"),
        Index("idx_crypto_strategy_family_version", "family_id", "version"),
    )

    def __repr__(self):
        return (f"<CryptoStrategy({self.strategy_id} {self.name} v{self.version} "
                f"{self.mode}/{self.status})>")


class CryptoArenaSwitch(Base):
    """策略切换留痕（S3）—— **事后复盘「这次换对了吗」的唯一依据**。

    切换那一刻四道门槛各是多少、挑战者当时有几个、判定用的是哪套阈值，全部冻结在这里。
    不存的话，三个月后回头看只能看到「7 月 28 日换了一次」，而**换得对不对永远说不清** ——
    那正好也是「AI 提议准不准」这个统计的一部分（`00-PLAN §4` 问题 2：样本本来就少）。

    ⚠️ 这张表**只记发生过的切换**。规则的「建议切/建议不切」不落库 —— 那只是意见，
    每次调用都能重算，存下来只会变成一堆过期结论。
    """
    __tablename__ = "crypto_arena_switches"

    id = Column(Integer, primary_key=True, autoincrement=True)
    family_id = Column(String(40), index=True)
    from_strategy_id = Column(String(40))              # 被换下的（可能为空：首次上位）
    to_strategy_id = Column(String(40), nullable=False)
    # JSON：切换那一刻四道门槛的完整判定快照（含各自的实测值与阈值）
    gates_snapshot = Column(Text)
    challenger_count = Column(Integer)                 # 当时有几个挑战者（多重比较修正的输入）
    note = Column(Text)
    created_at = Column(DateTime, default=utc_now)     # naive UTC（见文件头）

    def __repr__(self):
        return f"<CryptoArenaSwitch({self.from_strategy_id} → {self.to_strategy_id})>"


class CryptoStrategyProposal(Base):
    """AI 的**策略变更提案** —— 整个策略竞技场最漂亮的一环（S2）。

    格式是 Jason 定的：目前策略的不足 / 想怎么调整 / 为什么 /
    **预期收益是多少 / 预期胜率是多少**。

    ⭐ 后两项让提案成为**可证伪断言**：

        提案：均线 20→10，预期 30 天 +8%、胜率 55%
          ↓ Jason 批准，跑 30 天
        实际：+3%、48%  →  AI 这次判断记一次 miss

    **这是直接拿账户余额给 AI 打分**，比「cockpit 说 BUY 后来涨没涨」跟钱的关系近得多。
    后验评估在 S4（它没有 symbol/entry_price，⛔ 别硬塞进 `common/outcome_eval.py`）。

    提案里带的是**完整的新 spec**，不是 patch —— AI 已经会写 `CryptoStrategySpec`，
    再学一套 JSON-Patch 语法只增加出错面。而**差异由我们确定性算出来**
    （`crypto_strategy/proposals.py::diff_specs`），不让 AI 描述自己改了什么：
    它可能说漏说错，而 Jason 要审的正是「到底改了哪几个数」。
    """
    __tablename__ = "crypto_strategy_proposals"

    id = Column(Integer, primary_key=True, autoincrement=True)
    proposal_id = Column(String(40), nullable=False, unique=True, index=True)  # PS-<ts>-<hex6>
    family_id = Column(String(40), index=True)
    base_strategy_id = Column(String(40), nullable=False, index=True)   # 在哪个版本的基础上改

    # ── 提案正文（Jason 指定的五段）──
    shortfall = Column(Text, nullable=False)         # 目前策略的不足
    change_summary = Column(Text, nullable=False)    # 想怎么调整（人话）
    rationale = Column(Text, nullable=False)         # 为什么
    # ⭐ 可证伪断言：**必填**。没有断言的提案不是提案，是感想。
    expected_return_pct = Column(Float, nullable=False)   # 预期收益（小数，0.08 = +8%）
    expected_win_rate = Column(Float, nullable=False)     # 预期胜率（0-1）
    horizon_days = Column(Integer, nullable=False)        # 多少天内兑现（后验窗口）

    new_spec = Column(Text, nullable=False)          # JSON：完整的新 CryptoStrategySpec
    diff = Column(Text)                              # JSON：确定性算出的人话差异列表

    # proposed | applied | rejected | superseded_by_newer
    status = Column(String(24), default="proposed", index=True)
    applied_strategy_id = Column(String(40))         # 批准后生成的新版本
    decided_at = Column(DateTime)
    decision_note = Column(Text)                     # Jason 拒绝/批准时的一句话

    # ── 后验：断言兑现了吗（S4）──
    # ⛔ **不复用 `common/outcome_eval.py`**：那个内核评的是「一条建议的入场价 vs 未来
    # 价」，而提案没有 symbol、没有 entry_price、没有 bar，窗口也是自然日不是交易日。
    # 硬塞进去只会把已经被 P0-4 焊过两遍的内核再撬开一次。判定在
    # `crypto_strategy/proposal_outcome.py`，版本号独立。
    actual_return_pct = Column(Float)                # 窗口内实际收益（小数）
    actual_win_rate = Column(Float)                  # 窗口内实际胜率（0-1）
    outcome = Column(String(12), index=True)         # hit | miss | pending | unable
    # 🔴 **证据的成色**：live_fills=真金白银 / paper_simulated=理想撮合的模拟账。
    # 不存这一列的话，「纸面跑出来的 hit」和「真钱赚出来的 hit」在库里、在
    # `list_strategy_proposals`、在 scorecard 的分桶里**长得一模一样** ——
    # 而项目自己的铁律是「`basis` 一定要跟着数字一起读」。
    outcome_basis = Column(String(20))
    unable_reason = Column(String(40))               # 算不出来时的原因
    evaluated_at = Column(DateTime)
    engine_version = Column(String(32))              # 判定口径版本，改判定必须 bump

    # naive UTC（见文件头）
    created_at = Column(DateTime, default=utc_now)

    def __repr__(self):
        return (f"<CryptoStrategyProposal({self.proposal_id} on {self.base_strategy_id} "
                f"{self.status})>")


class CryptoStrategyRun(Base):
    """crypto 策略引擎逐 tick 审计日志 —— 自主系统必须可回溯每一次决策。

    每个策略每 tick 落一行：评估了哪些币、命中什么条件、毛/净边际、护栏与风控结果、
    paper 还是 live、下了哪些单。paper 模式**只写本表不写 CryptoTrade**（保持真实连亏台账干净）。
    """
    __tablename__ = "crypto_strategy_runs"

    id = Column(Integer, primary_key=True, autoincrement=True)
    strategy_id = Column(String(40), nullable=False, index=True)
    # evaluated|order_placed|skipped|blocked_risk|blocked_guardrail|blocked_cost|error
    status = Column(String(24), nullable=False)
    mode = Column(String(10))                          # paper | live（本 tick 快照）
    symbols_evaluated = Column(Integer, default=0)
    orders_placed = Column(Integer, default=0)
    decision_detail = Column(Text)                     # JSON：每币命中条件/composite/毛边际/往返成本/净边际/护栏/风控
    # 🔴 **不是币安 orderId** —— 这里存的是 `engine._run_one` 攒的 `staged_refs`，
    # 也就是待确认单的 `CPO-<ts>-<hex6>` 引用（live）；paper 为 null。列名是历史遗留。
    # ⛔ 别拿它去 join `CryptoTrade.order_id`，**一条都对不上**。策略盈亏归因走
    # `CryptoTrade.source_kind/source_ref`（S1，真源 `common/trade_source.py`）。
    executed_order_ids = Column(Text)
    pnl_realized_today = Column(Float)                 # 当日已实现盈亏快照
    fees_today = Column(Float)                         # 当日累计手续费快照（费用漂移护栏）
    error_message = Column(Text)
    # naive UTC（见文件头）：和同行的 completed_at、策略卡上的 last_run_at 同口径，
    # 也和 `_today_counts` 用 `market_day_bounds(CRYPTO)` 切的「当日」窗口同口径。
    # 展示由前端转本地（`utils/datetime.ts`），后端不再自作主张存本地时间。
    started_at = Column(DateTime, default=utc_now)
    completed_at = Column(DateTime)

    __table_args__ = (
        Index("idx_crypto_strategy_run_sid_started", "strategy_id", "started_at"),
    )

    def __repr__(self):
        return f"<CryptoStrategyRun({self.strategy_id} {self.status} orders={self.orders_placed})>"


class CryptoPendingOrder(Base):
    """crypto 半自动待确认单 —— 引擎产决策后**排队等 Jason 逐笔确认**，不自动成交。

    需求3（半自动）：引擎自动产决策 + 自动排队 → WS 推到前端/桌面 → Jason REST 点确认
    → confirm 时**再跑一次风控** → 走 `execution.py` 真成交。⛔ 逐笔人工确认是 CLAUDE.md
    永久红线，本表就是它在自主策略路径上的代码承载物——**引擎永不自动成交**。
    quantity 用 Float（小数币量），对齐 CryptoTrade。
    """
    __tablename__ = "crypto_pending_orders"

    id = Column(Integer, primary_key=True, autoincrement=True)
    order_ref = Column(String(40), nullable=False, unique=True, index=True)  # CPO-<ts>-<hex6>
    strategy_id = Column(String(40), index=True)      # 来自哪条策略
    symbol = Column(String(20), nullable=False, index=True)
    side = Column(String(10), nullable=False)         # BUY / SELL
    # 存**意图**不冻价：BUY 冻结目标 USDT 金额，确认时按现价重算币量（行情漂移仓位金额仍准）；
    # SELL 冻结要卖的币量（平仓）。quantity 为决策时估算（展示用），确认时以现价/现持仓重算。
    quote_amount = Column(Float)                      # BUY 目标名义金额（USDT）——真正冻结的意图
    quantity = Column(Float, nullable=False)          # 决策时估算币量（展示；确认时重算）
    price = Column(Float)                             # **决策时价格**，仅用于确认时算漂移与展示
    est_notional = Column(Float)                     # 预估名义金额（USDT）
    # PENDING → EXECUTING → FILLED / UNFILLED / FAILED / STALE（拒绝与过期是直接删行）
    #   FILLED   成交（fill_quantity > 0，可能是部分成交，余量被撤/过期）
    #   UNFILLED 已受理但**零成交**——不是成交也不是失败，绝不复用 FILLED
    #   FAILED   已确认交易所没受理，可安全重排
    #   STALE    ⚠️ 成交与否**未知**（下单后失联/后端中途重启），等人工去币安对账。
    #            系统绝不自行判它成没成交，且 `has_open` 会一直挡住重排，防重复下单。
    status = Column(String(12), default="PENDING", index=True)
    reason = Column(Text)                            # JSON：命中的进/出场条件 + 净边际等决策依据
    risk_snapshot = Column(Text)                     # JSON：产单时的风控预检结果
    net_edge = Column(Float)                         # 买单的净边际（扣完往返成本）
    # ⚠️ naive UTC（见文件头）：要和同表的 expires_at/confirmed_at（`pending.py` 用
    # `utc_now()` 写）以及引擎按 `market_day_bounds(CRYPTO)` 切的「当日」窗口同口径。
    created_at = Column(DateTime, default=utc_now)
    expires_at = Column(DateTime)                    # 过期时间（到点自动 EXPIRED，防陈单误成交）
    confirmed_at = Column(DateTime)
    executed_order_id = Column(String(50))           # 币安 orderId（成交后）
    fill_price = Column(Float)
    fill_quantity = Column(Float)
    error_message = Column(Text)

    __table_args__ = (
        Index("idx_crypto_pending_status", "status", "created_at"),
    )

    def __repr__(self):
        return f"<CryptoPendingOrder({self.order_ref} {self.symbol} {self.side} {self.status})>"

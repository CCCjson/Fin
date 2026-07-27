"""
数据库连接管理模块
"""
from sqlalchemy import text
from sqlalchemy.orm import scoped_session, declarative_base
import os
from pathlib import Path

from common.db import make_session_factory, make_sqlite_engine

# 创建基类
Base = declarative_base()

# 项目根目录（backend/）
_BACKEND_DIR = Path(__file__).resolve().parent.parent.parent
_DEFAULT_DB = _BACKEND_DIR / "data" / "market.db"

# 数据库 URL
DATABASE_URL = os.getenv("DATABASE_URL", f"sqlite:///{_DEFAULT_DB}")

# 确保数据目录存在
_DEFAULT_DB.parent.mkdir(parents=True, exist_ok=True)

# 引擎构造（NullPool + WAL + busy_timeout）统一在 common/db.py
engine = make_sqlite_engine(DATABASE_URL)
SessionLocal = make_session_factory(engine)

# 线程安全的会话
ScopedSession = scoped_session(SessionLocal)


def backfill_stock_info(conn) -> None:
    """回填 stock_info 的 stock_type / exchange（幂等，init_db 每次启动都跑）。

    exchange 只对 A股有意义：港股不作交易所细分，美股恒为 NULL。
    历史实现按代码前缀回填且**未按 market 过滤**，导致港股 5 位码撞上 A股前缀
    规则——`89988.HK`（阿里巴巴）被标成 `BJ`（北交所），4699 只港股全部受污染。
    现改为：先清非 A股的脏值，再直接取 A股 symbol 自带的后缀。

    不再按代码前缀猜的原因：前缀规则对指数存在无解歧义
    （`000001.SH` 是上证指数，`000001.SZ` 是平安银行，同码不同所）。

    Args:
        conn: 已开启事务的 SQLAlchemy Connection。
    """
    conn.execute(text(
        "UPDATE stock_info SET stock_type = 'stock' WHERE stock_type IS NULL"
    ))
    conn.execute(text(
        "UPDATE stock_info SET exchange = NULL WHERE market != 'a_share'"
    ))
    for suffix in ("SH", "SZ", "BJ"):
        conn.execute(text(
            "UPDATE stock_info SET exchange = :ex "
            "WHERE exchange IS NULL AND market = 'a_share' AND symbol LIKE :pat"
        ), {"ex": suffix, "pat": f"%.{suffix}"})


def init_db():
    """初始化数据库，创建所有表"""
    from data_engine.storage import models  # 导入模型
    Base.metadata.create_all(bind=engine)

    # 自动迁移：为已有 daily_reviews 表添加 index_snapshot 列
    from sqlalchemy import text, inspect
    insp = inspect(engine)
    if "daily_reviews" in insp.get_table_names():
        cols = {c["name"] for c in insp.get_columns("daily_reviews")}
        if "index_snapshot" not in cols:
            with engine.begin() as conn:
                conn.execute(text("ALTER TABLE daily_reviews ADD COLUMN index_snapshot TEXT"))
            print("✓ daily_reviews 表已添加 index_snapshot 列")

    # 自动迁移：为 manual_trades 表添加 source_type、pending_order_id 列 + 回填
    if "manual_trades" in insp.get_table_names():
        mt_cols = {c["name"] for c in insp.get_columns("manual_trades")}
        added_mt = False
        if "source_type" not in mt_cols:
            with engine.begin() as conn:
                conn.execute(text("ALTER TABLE manual_trades ADD COLUMN source_type VARCHAR(20)"))
            added_mt = True
            print("✓ manual_trades 表已添加 source_type 列")
        if "pending_order_id" not in mt_cols:
            with engine.begin() as conn:
                conn.execute(text("ALTER TABLE manual_trades ADD COLUMN pending_order_id VARCHAR(50)"))
            added_mt = True
            print("✓ manual_trades 表已添加 pending_order_id 列")

        # 添加索引
        mt_indexes = {idx["name"] for idx in insp.get_indexes("manual_trades")}
        if "ix_manual_trades_source_type" not in mt_indexes:
            with engine.begin() as conn:
                conn.execute(text(
                    "CREATE INDEX IF NOT EXISTS ix_manual_trades_source_type "
                    "ON manual_trades (source_type)"
                ))
        if "ix_manual_trades_pending_order_id" not in mt_indexes:
            with engine.begin() as conn:
                conn.execute(text(
                    "CREATE INDEX IF NOT EXISTS ix_manual_trades_pending_order_id "
                    "ON manual_trades (pending_order_id)"
                ))

        # 回填历史数据
        if added_mt:
            with engine.begin() as conn:
                # 自动化订单：note LIKE '[自动化]%' → source_type='automation'，提取订单号
                conn.execute(text("""
                    UPDATE manual_trades
                    SET source_type = 'automation'
                    WHERE note LIKE '[自动化]%' AND source_type IS NULL
                """))
                # 手动下单：note LIKE '[手动下单]%' → source_type='manual_broker'
                conn.execute(text("""
                    UPDATE manual_trades
                    SET source_type = 'manual_broker'
                    WHERE note LIKE '[手动下单]%' AND source_type IS NULL
                """))
                # 其余 → source_type='manual_entry'
                conn.execute(text("""
                    UPDATE manual_trades
                    SET source_type = 'manual_entry'
                    WHERE source_type IS NULL
                """))
            print("✓ manual_trades 历史数据 source_type 回填完成")

            # 回填 pending_order_id：从 note 中提取 "订单号=PO-xxx"
            with engine.begin() as conn:
                from sqlalchemy import text as sql_text
                rows = conn.execute(text(
                    "SELECT id, note FROM manual_trades "
                    "WHERE source_type = 'automation' AND pending_order_id IS NULL AND note IS NOT NULL"
                )).fetchall()
                import re
                for row in rows:
                    match = re.search(r"订单号=(PO-[A-Za-z0-9\-]+)", row[1] or "")
                    if match:
                        conn.execute(text(
                            "UPDATE manual_trades SET pending_order_id = :oid WHERE id = :tid"
                        ), {"oid": match.group(1), "tid": row[0]})
            print("✓ manual_trades 历史数据 pending_order_id 回填完成")

    # 自动迁移：为 signal_tracking 表添加 (symbol, signal_date) 复合索引
    if "signal_tracking" in insp.get_table_names():
        existing_indexes = {idx["name"] for idx in insp.get_indexes("signal_tracking")}
        if "idx_tracking_symbol_date" not in existing_indexes:
            with engine.begin() as conn:
                conn.execute(text(
                    "CREATE INDEX IF NOT EXISTS idx_tracking_symbol_date "
                    "ON signal_tracking (symbol, signal_date)"
                ))
            print("✓ signal_tracking 表已添加 idx_tracking_symbol_date 复合索引")

    # 自动迁移：为 backtest_tasks 表添加 batch_id 列
    if "backtest_tasks" in insp.get_table_names():
        bt_cols = {c["name"] for c in insp.get_columns("backtest_tasks")}
        if "batch_id" not in bt_cols:
            with engine.begin() as conn:
                conn.execute(text("ALTER TABLE backtest_tasks ADD COLUMN batch_id VARCHAR(50)"))
                conn.execute(text(
                    "CREATE INDEX IF NOT EXISTS ix_backtest_tasks_batch_id "
                    "ON backtest_tasks (batch_id)"
                ))
            print("✓ backtest_tasks 表已添加 batch_id 列")

    # 自动迁移：为 decision_logs 表添加后验评估列（P0-1，判定内核见 common/outcome_eval.py）
    if "decision_logs" in insp.get_table_names():
        _dl_new = [
            ("return_5d", "FLOAT"), ("return_20d", "FLOAT"),
            ("outcome_5d", "VARCHAR(10)"), ("outcome_20d", "VARCHAR(10)"),
            ("hit_stop", "INTEGER"), ("hit_target", "INTEGER"),
            ("first_hit", "VARCHAR(12)"), ("first_hit_days", "INTEGER"),
            ("outcome_status", "VARCHAR(20)"), ("unable_reason", "VARCHAR(30)"),
            ("engine_version", "VARCHAR(30)"), ("evaluated_at", "DATETIME"),
        ]
        dl_cols = {c["name"] for c in insp.get_columns("decision_logs")}
        _dl_added = [c for c, _ in _dl_new if c not in dl_cols]
        if _dl_added:
            with engine.begin() as conn:
                for _col, _typ in _dl_new:
                    if _col not in dl_cols:
                        conn.execute(text(f"ALTER TABLE decision_logs ADD COLUMN {_col} {_typ}"))
            print(f"✓ decision_logs 表已添加后验评估列: {_dl_added}")
        # 刻意**不**回填 outcome_status='pending'：存量行该标什么状态是评估内核的判断
        # （advisor 该 unable/no_action、report_picks 该按龄分流），在 SQL 里手写等于
        # 把内核逻辑复制一份，engine_version 也没法戳。留 NULL 让首次回填自然分流。
        # 自动迁移：entry_kind（P0-4）—— 「这一行记的是哪一类事」。
        # 取值与语义的单一真源在 common/decision_kind.py。
        if "entry_kind" not in dl_cols:
            with engine.begin() as conn:
                conn.execute(text("ALTER TABLE decision_logs ADD COLUMN entry_kind VARCHAR(12)"))
            print("✓ decision_logs 表已添加 entry_kind 列")
        # 存量行归类。**与 P0-1 那批 outcome 列刻意相反：这次要回填。**
        # 那次留 NULL 是因为「该标什么状态」是评估内核的判断，写进 SQL 等于把内核
        # 复制一份；这次「这行是建议还是回执」是**写入点的既成事实**，SQL 里判得准，
        # 而且不回填就等于把 40% 的脏行继续留在评估候选集里 —— 炸弹不拆。
        #
        # `WHERE entry_kind IS NULL` 让它天然幂等，且**永不覆盖**后来写入的分类。
        with engine.begin() as conn:
            _classified = conn.execute(text("""
                UPDATE decision_logs SET entry_kind = CASE
                    -- crypto：全是 execution.py 的成交/挂单回执，一条建议都没有
                    WHEN source = 'crypto' THEN 'execution'
                    WHEN source = 'crypto_earn' THEN 'ops'
                    -- moneybill：全部出自 confirm_gate（「每一次经确认的工具调用」）。
                    -- 有 action 的是下单（place_order / place_crypto_order），其余是
                    -- 加自选股/建预警/编策略这类非交易操作。
                    WHEN source = 'moneybill' AND trim(coalesce(action, '')) <> '' THEN 'execution'
                    WHEN source = 'moneybill' THEN 'ops'
                    -- advisor / cockpit / crypto_cockpit / moneybill_recommend /
                    -- report_picks —— 这些才是 AI 的可证伪断言
                    ELSE 'advice'
                END
                WHERE entry_kind IS NULL
            """)).rowcount
        if _classified:
            print(f"✓ decision_logs 已回填 entry_kind 分类: {_classified} 行")

        # 存量 action 大小写归一（P0-4 批次2）。库里曾同时存着 `BUY`(69) 和 `buy`(6)，
        # 而消费方全是**精确匹配**（report_engine/picks_log.py 的 `action == "BUY"`、
        # decision_log.query_decisions 的等值过滤）—— 小写行在它们眼里不存在。
        #
        # 写入期已由 `outcome_eval.normalize_action` 收口，这条只管存量。
        # `WHERE action <> upper(trim(action))` 让它**天然幂等**（改完条件就不成立）。
        #
        # ⚠️ 这**不违反**「原始决策不可篡改」（`decision_log._IMMUTABLE_REFRESH_FIELDS`）：
        # 那条铁律管的是**回填评估路径**（改了当时的止损再去算胜率 = 给自己发奖状），
        # 而大小写归一是**无损**变换，不改变任何语义。
        with engine.begin() as conn:
            _upcased = conn.execute(text("""
                UPDATE decision_logs SET action = upper(trim(action))
                WHERE action IS NOT NULL AND action <> upper(trim(action))
            """)).rowcount
        if _upcased:
            print(f"✓ decision_logs 已归一 action 大小写: {_upcased} 行")

        dl_idx = {i["name"] for i in insp.get_indexes("decision_logs")}
        if "idx_decision_outcome_scan" not in dl_idx:
            with engine.begin() as conn:
                conn.execute(text(
                    "CREATE INDEX IF NOT EXISTS idx_decision_outcome_scan "
                    "ON decision_logs (outcome_status, created_at)"
                ))
            print("✓ decision_logs 表已添加 idx_decision_outcome_scan 复合索引")

    # 自动迁移：crypto_trades 的来源归因两列（S1）—— 「这笔单是从哪儿来的」。
    # 取值真源 common/trade_source.py。
    #
    # ⚠️ 存量行**刻意回填成 `unknown` 而不是猜**：策略排的单只在 `CryptoPendingOrder`
    # 里留过桥接，而那张表的 FILLED 行 24 小时后就被清理了 —— 对存量成交，「它属于哪条
    # 策略」这个信息**是真的没了**。SQL 里按时间/symbol 去猜等于凭空捏造某条策略的战绩，
    # 而战绩要拿来决定切不切换策略。宁可让它显示「N 笔来源不明」。
    if "crypto_trades" in insp.get_table_names():
        _ct_cols = {c["name"] for c in insp.get_columns("crypto_trades")}
        _ct_new = [("source_kind", "VARCHAR(16)"), ("source_ref", "VARCHAR(64)")]
        _ct_added = [c for c, _ in _ct_new if c not in _ct_cols]
        if _ct_added:
            with engine.begin() as conn:
                for _col, _typ in _ct_new:
                    if _col not in _ct_cols:
                        conn.execute(text(f"ALTER TABLE crypto_trades ADD COLUMN {_col} {_typ}"))
            print(f"✓ crypto_trades 表已添加来源归因列: {_ct_added}")
        # `WHERE source_kind IS NULL` 天然幂等，且**永不覆盖**写入点显式标好的值。
        with engine.begin() as conn:
            _tagged = conn.execute(text(
                "UPDATE crypto_trades SET source_kind = 'unknown' WHERE source_kind IS NULL"
            )).rowcount
        if _tagged:
            print(f"✓ crypto_trades 存量行标记来源不明: {_tagged} 行")
        # ⚠️ **ALTER TABLE 不会建索引，`create_all` 对已存在的表整表跳过** ——
        # 于是模型里声明的索引在**老库上永远不存在**，而全新库（测试库）里有，
        # schema drift 对测试完全隐形。必须显式建（同上面 decision_logs 的做法）。
        with engine.begin() as conn:
            conn.execute(text("CREATE INDEX IF NOT EXISTS idx_crypto_trade_source "
                              "ON crypto_trades (source_kind, source_ref)"))
            conn.execute(text("CREATE INDEX IF NOT EXISTS ix_crypto_trades_source_kind "
                              "ON crypto_trades (source_kind)"))

    # 自动迁移：crypto_strategies 的版本化两列（S2）。
    # 存量行 = 每条策略自成一族的第一版：`family_id = strategy_id`、`version = 1`。
    # 这么回填是**无损**的：此前根本没有「同一条策略的多个版本」这个概念。
    if "crypto_strategies" in insp.get_table_names():
        _cs_cols = {c["name"] for c in insp.get_columns("crypto_strategies")}
        _cs_new = [("family_id", "VARCHAR(40)"), ("version", "INTEGER"),
                   ("forked_from", "VARCHAR(40)")]
        _cs_added = [c for c, _ in _cs_new if c not in _cs_cols]
        if _cs_added:
            with engine.begin() as conn:
                for _col, _typ in _cs_new:
                    if _col not in _cs_cols:
                        conn.execute(text(
                            f"ALTER TABLE crypto_strategies ADD COLUMN {_col} {_typ}"))
            print(f"✓ crypto_strategies 表已添加版本化列: {_cs_added}")
        with engine.begin() as conn:
            _fam = conn.execute(text(
                "UPDATE crypto_strategies SET family_id = strategy_id "
                "WHERE family_id IS NULL")).rowcount
            conn.execute(text(
                "UPDATE crypto_strategies SET version = 1 WHERE version IS NULL"))
            # ⚠️ 同 crypto_trades 那条：ALTER 不建索引，create_all 对已存在的表整表跳过。
            conn.execute(text("CREATE INDEX IF NOT EXISTS idx_crypto_strategy_family_version "
                              "ON crypto_strategies (family_id, version)"))
        if _fam:
            print(f"✓ crypto_strategies 已回填 family_id/version: {_fam} 行")

    # 自动迁移：回填 stock_info 表的 stock_type 和 exchange
    if "stock_info" in insp.get_table_names():
        with engine.begin() as conn:
            backfill_stock_info(conn)

    # 自动迁移：为 orders/trades/signals 表添加 name 列，并从 stock_info 回填历史快照
    for _tbl in ("orders", "trades", "signals"):
        if _tbl in insp.get_table_names():
            _cols = {c["name"] for c in insp.get_columns(_tbl)}
            if "name" not in _cols:
                with engine.begin() as conn:
                    conn.execute(text(f"ALTER TABLE {_tbl} ADD COLUMN name VARCHAR(100)"))
                    conn.execute(text(
                        f"UPDATE {_tbl} SET name = ("
                        f"SELECT stock_info.name FROM stock_info "
                        f"WHERE stock_info.symbol = {_tbl}.symbol"
                        f") WHERE name IS NULL"
                    ))
                print(f"✓ {_tbl} 表已添加 name 列并回填历史数据")

    # 自动迁移：确保 financial_data 表存在（新增表会由 create_all 自动创建）

    print("✓ 数据库初始化完成")


def get_db():
    """获取数据库会话（用于依赖注入）"""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def get_session():
    """获取数据库会话（直接使用）"""
    return SessionLocal()

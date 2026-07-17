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
        dl_idx = {i["name"] for i in insp.get_indexes("decision_logs")}
        if "idx_decision_outcome_scan" not in dl_idx:
            with engine.begin() as conn:
                conn.execute(text(
                    "CREATE INDEX IF NOT EXISTS idx_decision_outcome_scan "
                    "ON decision_logs (outcome_status, created_at)"
                ))
            print("✓ decision_logs 表已添加 idx_decision_outcome_scan 复合索引")

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

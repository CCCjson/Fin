"""
数据库连接管理模块
"""
from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker, scoped_session, declarative_base
from sqlalchemy.pool import NullPool
import os
from pathlib import Path

# 创建基类
Base = declarative_base()

# 项目根目录（backend/）
_BACKEND_DIR = Path(__file__).resolve().parent.parent.parent
_DEFAULT_DB = _BACKEND_DIR / "data" / "market.db"

# 数据库 URL
DATABASE_URL = os.getenv("DATABASE_URL", f"sqlite:///{_DEFAULT_DB}")

# 确保数据目录存在
_DEFAULT_DB.parent.mkdir(parents=True, exist_ok=True)

# 创建引擎（NullPool：每个 session 独立连接，彻底隔离并发写入冲突）
engine = create_engine(
    DATABASE_URL,
    connect_args={"check_same_thread": False} if "sqlite" in DATABASE_URL else {},
    poolclass=NullPool if "sqlite" in DATABASE_URL else None,
    echo=False  # 设为 True 可以看到 SQL 语句
)

# SQLite 专用：开启 WAL 模式 + 等待锁超时 30s，允许多线程并发读写
if "sqlite" in DATABASE_URL:
    @event.listens_for(engine, "connect")
    def set_sqlite_pragmas(dbapi_conn, _):
        cur = dbapi_conn.cursor()
        cur.execute("PRAGMA journal_mode=WAL")
        cur.execute("PRAGMA busy_timeout=30000")
        cur.close()

# 创建会话工厂
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

# 线程安全的会话
ScopedSession = scoped_session(SessionLocal)


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

    # 自动迁移：回填 stock_info 表的 stock_type 和 exchange
    if "stock_info" in insp.get_table_names():
        with engine.begin() as conn:
            # stock_type 为空的默认设为 stock
            conn.execute(text(
                "UPDATE stock_info SET stock_type = 'stock' WHERE stock_type IS NULL"
            ))
            # exchange 为空的根据 symbol 推断
            conn.execute(text(
                "UPDATE stock_info SET exchange = 'SH' "
                "WHERE exchange IS NULL AND symbol LIKE '6%'"
            ))
            conn.execute(text(
                "UPDATE stock_info SET exchange = 'SZ' "
                "WHERE exchange IS NULL AND (symbol LIKE '0%' OR symbol LIKE '3%')"
            ))
            conn.execute(text(
                "UPDATE stock_info SET exchange = 'BJ' "
                "WHERE exchange IS NULL AND (symbol LIKE '4%' OR symbol LIKE '8%')"
            ))

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

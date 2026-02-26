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

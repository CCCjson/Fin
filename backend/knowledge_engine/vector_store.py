"""
sqlite-vec / vec0 虚拟表封装 — 全项目唯一直接碰 vec0 的地方。

核心决策（见方案）：
- vec0 是虚拟表，SQLAlchemy ORM 管不了，且建表前必须先 load_extension。
- 因此这里用**独立 sqlite3 短连接**（开 enable_load_extension + sqlite_vec.load），
  与 SQLAlchemy 引擎共享同一个 knowledge.db 文件但走自己的连接，对 ORM 零侵入。
  WAL 模式下读写并发安全。
- KnowledgeChunk.id（INT 自增）直接作为 vec_chunks 的 rowid，向量表只存 (rowid, embedding)，
  KNN 返回 rowid 一对一回 join knowledge_chunks 取文本与 doc_id。
"""
import sqlite3
import threading
from typing import List, Tuple

from loguru import logger

from knowledge_engine.config import get_db_path, get_embed_dim

_TABLE = "vec_chunks"
_load_lock = threading.Lock()


def _connect() -> sqlite3.Connection:
    """开一条加载了 sqlite-vec 扩展的独立连接。"""
    import sqlite_vec

    conn = sqlite3.connect(get_db_path(), timeout=30.0)
    conn.enable_load_extension(True)
    sqlite_vec.load(conn)
    conn.enable_load_extension(False)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=30000")
    return conn


def check_extension() -> str:
    """探测 sqlite-vec 是否可加载，返回版本号；失败抛出清晰错误（启动时调用）。"""
    try:
        conn = _connect()
        (ver,) = conn.execute("SELECT vec_version()").fetchone()
        conn.close()
        return ver
    except Exception as e:  # noqa: BLE001
        raise RuntimeError(
            f"sqlite-vec 扩展加载失败：{e}。请确认已 `pip install sqlite-vec` 且当前 "
            f"Python 的 sqlite3 支持 enable_load_extension。"
        ) from e


def ensure_vec_table() -> None:
    """幂等创建 vec0 虚拟表，维度跟 embedding 模型走（写死在建表语句里）。"""
    with _load_lock:
        dim = get_embed_dim()
        conn = _connect()
        try:
            conn.execute(
                f"CREATE VIRTUAL TABLE IF NOT EXISTS {_TABLE} USING vec0(embedding float[{dim}])"
            )
            conn.commit()
        finally:
            conn.close()
        logger.info(f"vec0 向量表 {_TABLE} 就绪（维度 {dim}）")


def upsert(rows: List[Tuple[int, List[float]]]) -> int:
    """批量写入 (chunk_id, embedding)。chunk_id 作为 rowid，重复 rowid 先删后插。"""
    import sqlite_vec

    if not rows:
        return 0
    conn = _connect()
    try:
        for rid, vec in rows:
            conn.execute(f"DELETE FROM {_TABLE} WHERE rowid = ?", (rid,))
            conn.execute(
                f"INSERT INTO {_TABLE}(rowid, embedding) VALUES (?, ?)",
                (rid, sqlite_vec.serialize_float32(vec)),
            )
        conn.commit()
        return len(rows)
    finally:
        conn.close()


def knn(query_vec: List[float], k: int) -> List[Tuple[int, float]]:
    """KNN 检索，返回 [(chunk_id, distance), ...]，按距离升序。"""
    import sqlite_vec

    conn = _connect()
    try:
        res = conn.execute(
            f"SELECT rowid, distance FROM {_TABLE} "
            f"WHERE embedding MATCH ? ORDER BY distance LIMIT ?",
            (sqlite_vec.serialize_float32(query_vec), k),
        ).fetchall()
        return [(int(r[0]), float(r[1])) for r in res]
    finally:
        conn.close()


def delete(chunk_ids: List[int]) -> int:
    """按 chunk_id 删除向量（删文档时清理）。"""
    if not chunk_ids:
        return 0
    conn = _connect()
    try:
        conn.executemany(f"DELETE FROM {_TABLE} WHERE rowid = ?", [(i,) for i in chunk_ids])
        conn.commit()
        return len(chunk_ids)
    finally:
        conn.close()


def count() -> int:
    """向量表行数（统计用）。"""
    conn = _connect()
    try:
        (n,) = conn.execute(f"SELECT count(*) FROM {_TABLE}").fetchone()
        return int(n)
    finally:
        conn.close()

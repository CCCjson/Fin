"""迁移脚本 migrate_to_utc 的护栏测试 —— 幂等、dry-run 不写、news 逐行按 market。

这个脚本是整个时区工程唯一不可逆的动作（跑第二遍 = 再减 8 小时）。测试盯死：
  1. dry-run 一行不改；
  2. --apply 把本地列减一个 offset 变 UTC；
  3. 记进 schema_migrations，第二遍被挡；
  4. news_articles 只转非 crypto 行，crypto 行原样。
"""
import importlib.util as _ilu
import pathlib as _pl
import sqlite3
from datetime import datetime, timedelta, timezone

import pytest

_spec = _ilu.spec_from_file_location(
    "migrate_to_utc",
    _pl.Path(__file__).resolve().parents[2] / "scripts" / "migrate_to_utc.py")
M = _ilu.module_from_spec(_spec)
_spec.loader.exec_module(M)


def _mkdb(tmp_path):
    """建一个含关键列的小库，塞可判定的值。"""
    p = tmp_path / "t.db"
    c = sqlite3.connect(p)
    c.executescript("""
        CREATE TABLE data_update_logs (id INTEGER PRIMARY KEY,
            started_at DATETIME, completed_at DATETIME);
        CREATE TABLE decision_logs (id INTEGER PRIMARY KEY, evaluated_at DATETIME);
        CREATE TABLE news_articles (id INTEGER PRIMARY KEY, market TEXT,
            published_at DATETIME);
        CREATE TABLE realtime_snapshots (id INTEGER PRIMARY KEY, snapshot_time DATETIME);
    """)
    # 本地墙钟值（模拟 datetime.now() 写入的）
    c.execute("INSERT INTO data_update_logs VALUES (1, '2026-07-22 17:53:45', '2026-07-22 17:57:00')")
    c.execute("INSERT INTO decision_logs VALUES (1, '2026-07-22 15:50:00')")
    c.execute("INSERT INTO news_articles VALUES (1, 'general', '2026-07-22 19:36:00')")
    c.execute("INSERT INTO news_articles VALUES (2, 'crypto', '2026-07-22 10:00:00')")  # 已 UTC
    c.execute("INSERT INTO realtime_snapshots VALUES (1, '2026-07-07 13:07:37')")
    c.commit()
    c.close()
    return str(p)


def _val(db, sql):
    c = sqlite3.connect(db)
    try:
        return c.execute(sql).fetchone()[0]
    finally:
        c.close()


@pytest.fixture(autouse=True)
def _fixed_offset(monkeypatch):
    """把「本地 offset」固定成 +8，测试不依赖跑测试的机器时区。"""
    monkeypatch.setattr(M, "_local_offset_hours", lambda: 8.0)


def test_dry_run_changes_nothing(tmp_path):
    db = _mkdb(tmp_path)
    M.run(db, dry_run=True)
    assert _val(db, "SELECT started_at FROM data_update_logs WHERE id=1") == "2026-07-22 17:53:45"
    # dry-run 不该建/写 schema_migrations 记录
    c = sqlite3.connect(db)
    got = c.execute("SELECT COUNT(*) FROM schema_migrations").fetchone()[0]
    c.close()
    assert got == 0


def test_apply_converts_local_to_utc(tmp_path):
    db = _mkdb(tmp_path)
    M.run(db, dry_run=False)
    # 北京 17:53 → UTC 09:53
    assert _val(db, "SELECT started_at FROM data_update_logs WHERE id=1") == "2026-07-22 09:53:45"
    assert _val(db, "SELECT evaluated_at FROM decision_logs WHERE id=1") == "2026-07-22 07:50:00"
    assert _val(db, "SELECT snapshot_time FROM realtime_snapshots WHERE id=1") == "2026-07-07 05:07:37"


def test_news_only_non_crypto_rows_move(tmp_path):
    db = _mkdb(tmp_path)
    M.run(db, dry_run=False)
    # general 行：北京 19:36 → UTC 11:36
    assert _val(db, "SELECT published_at FROM news_articles WHERE id=1") == "2026-07-22 11:36:00"
    # crypto 行：本就是 UTC，原样不动
    assert _val(db, "SELECT published_at FROM news_articles WHERE id=2") == "2026-07-22 10:00:00"


def test_second_run_is_blocked(tmp_path):
    db = _mkdb(tmp_path)
    M.run(db, dry_run=False)
    once = _val(db, "SELECT started_at FROM data_update_logs WHERE id=1")
    # 第二遍必须被 schema_migrations 挡住，值不再变（否则又减 8 小时）
    M.run(db, dry_run=False)
    twice = _val(db, "SELECT started_at FROM data_update_logs WHERE id=1")
    assert once == twice == "2026-07-22 09:53:45"


def test_instant_is_preserved(tmp_path):
    """转换保持的是同一个绝对时刻，只是换了时区皮。"""
    db = _mkdb(tmp_path)
    before_local = datetime(2026, 7, 22, 17, 53, 45, tzinfo=timezone(timedelta(hours=8)))
    M.run(db, dry_run=False)
    after = datetime.fromisoformat(
        _val(db, "SELECT started_at FROM data_update_logs WHERE id=1")).replace(tzinfo=timezone.utc)
    assert after == before_local

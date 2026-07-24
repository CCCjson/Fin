"""一次性迁移：把「本地时间(UTC+8)写入」的历史 DateTime 列转成 naive UTC。

## 为什么

见 docs/CODING_STANDARDS.md §11。全库统一 naive UTC 后，任何「按当日切窗口」的比较
才不会差 8 小时。写入侧代码同批改成 `utc_now()`；本脚本补齐**历史行**。

## ⛔ 只跑一次 —— 幂等锁

跑第二遍 = 再减 8 小时 = 不可逆损坏。用 `schema_migrations` 表记录已执行的 migration
名，撞名即跳过。`--dry-run` 只打印每列受影响行数、一行不改。

## 口径怎么定的（逐列看代码事实，不靠数据启发式）

- **不迁**：`server_default=func.now()` / `default=utc_now` / `func.now()` 赋值的列
  —— SQLite 落的本就是 UTC。误迁 = 反向错 8 小时。
- **不迁**：`crypto_bars.open_time` / `crypto_fills.trade_time`（币安 ms 时间戳转的 UTC）、
  `alpha_lab_sessions.completed_at`（`func.now()`）、`news_articles.published_at` 里
  `market='crypto'` 的行（`crypto_news._ms_to_dt` 给的 UTC）。
- **不迁·混合列**：既有 server_default 又被 Python `datetime.now()` 写过的 `*.updated_at`
  （daily_quotes / signal_tracking / stock_valuations …）—— 历史行逐行分不清哪种口径，
  重写会把本来对的 UTC 行也搞错。只保证未来写入统一（写入侧已改 utc_now），历史留痕。
- **迁**：只被 Python `datetime.now()` 本地写、无 server_default 的列（下方 `_LOCAL_COLS`）。

## 偏移不写死

按服务器**当前**本地 offset 算（`datetime.now().astimezone()`），不硬编码 -8。
换台机器重跑也对（虽然正常只该在写入这些行的那台机器上跑一次）。
"""
from __future__ import annotations

import argparse
import sqlite3
import sys
from datetime import datetime

MIGRATION_NAME = "2026-07-24-datetime-columns-to-utc"

# (表, 列) —— 纯本地写、需要转 UTC 的列。逐列对着写入代码确认过（见模块 docstring）。
_LOCAL_COLS: list[tuple[str, str]] = [
    ("backtest_tasks", "started_at"),
    ("backtest_tasks", "completed_at"),
    ("batch_backtests", "started_at"),
    ("batch_backtests", "completed_at"),
    ("data_update_logs", "started_at"),
    ("data_update_logs", "completed_at"),
    ("decision_logs", "evaluated_at"),
    ("orders", "filled_at"),
    ("orders", "submitted_at"),
    ("orders", "cancelled_at"),
    ("pending_orders", "expire_at"),
    ("pending_orders", "confirmed_at"),
    ("realtime_snapshots", "snapshot_time"),
    ("realtime_quotes", "timestamp"),
    ("crypto_strategies", "last_backtest_at"),
    ("crypto_strategies", "last_run_at"),
    ("crypto_strategies", "last_signal_at"),
    ("crypto_strategies", "next_run_at"),
    ("crypto_strategy_runs", "started_at"),
    ("crypto_strategy_runs", "completed_at"),
]

# news_articles.published_at 逐行按 market 分：crypto 行是 UTC（不动），其余是本地（转）。
_NEWS_TABLE = "news_articles"
_NEWS_COL = "published_at"


def _local_offset_hours() -> float:
    off = datetime.now().astimezone().utcoffset()
    return (off.total_seconds() / 3600.0) if off else 0.0


def _table_has(conn: sqlite3.Connection, table: str, col: str) -> bool:
    try:
        cols = {r[1] for r in conn.execute(f'PRAGMA table_info("{table}")')}
    except sqlite3.Error:
        return False
    return col in cols


def _ensure_migrations_table(conn: sqlite3.Connection) -> None:
    conn.execute("""
        CREATE TABLE IF NOT EXISTS schema_migrations (
            name TEXT PRIMARY KEY,
            applied_at TEXT NOT NULL
        )
    """)


def _already_applied(conn: sqlite3.Connection) -> bool:
    _ensure_migrations_table(conn)
    row = conn.execute("SELECT 1 FROM schema_migrations WHERE name = ?",
                       (MIGRATION_NAME,)).fetchone()
    return row is not None


def run(db_path: str, dry_run: bool) -> int:
    conn = sqlite3.connect(db_path)
    try:
        if _already_applied(conn):
            print(f"⛔ migration「{MIGRATION_NAME}」已执行过，跳过。"
                  f"（再跑一遍会把时间再减一次，绝不允许）")
            return 0

        off_h = _local_offset_hours()
        delta = f"{-off_h:+g} hours"      # 本地 → UTC：减去本地 offset
        print(f"服务器本地 offset = UTC{off_h:+g}；转换用 datetime(col, '{delta}')")
        print(f"模式：{'DRY-RUN（只统计，不改）' if dry_run else '真实执行'}\n")

        total = 0
        for table, col in _LOCAL_COLS:
            if not _table_has(conn, table, col):
                continue
            n = conn.execute(
                f'SELECT COUNT("{col}") FROM "{table}" WHERE "{col}" IS NOT NULL'
            ).fetchone()[0]
            if n == 0:
                continue
            total += n
            print(f"  {table}.{col:<18} {n:>8} 行")
            if not dry_run:
                conn.execute(
                    f'UPDATE "{table}" SET "{col}" = datetime("{col}", ?) '
                    f'WHERE "{col}" IS NOT NULL', (delta,))

        # news_articles.published_at —— 只转非 crypto 行
        if _table_has(conn, _NEWS_TABLE, _NEWS_COL):
            n = conn.execute(
                f'SELECT COUNT("{_NEWS_COL}") FROM "{_NEWS_TABLE}" '
                f'WHERE "{_NEWS_COL}" IS NOT NULL AND market != ?', ("crypto",)
            ).fetchone()[0]
            crypto_n = conn.execute(
                f'SELECT COUNT("{_NEWS_COL}") FROM "{_NEWS_TABLE}" '
                f'WHERE "{_NEWS_COL}" IS NOT NULL AND market = ?', ("crypto",)
            ).fetchone()[0]
            total += n
            print(f"  {_NEWS_TABLE}.{_NEWS_COL:<18} {n:>8} 行（非 crypto；"
                  f"另有 {crypto_n} crypto 行本就是 UTC，不动）")
            if not dry_run:
                conn.execute(
                    f'UPDATE "{_NEWS_TABLE}" SET "{_NEWS_COL}" = datetime("{_NEWS_COL}", ?) '
                    f'WHERE "{_NEWS_COL}" IS NOT NULL AND market != ?', (delta, "crypto"))

        print(f"\n合计受影响 {total} 行。")

        if dry_run:
            print("（dry-run，未写库。加 --apply 真正执行。）")
            return 0

        conn.execute("INSERT INTO schema_migrations (name, applied_at) VALUES (?, ?)",
                     (MIGRATION_NAME, datetime.now().astimezone().isoformat()))
        conn.commit()
        print(f"✅ 已执行并记入 schema_migrations（{MIGRATION_NAME}）。")
        return 0
    finally:
        conn.close()


def main() -> int:
    ap = argparse.ArgumentParser(description="把本地时间历史列迁成 naive UTC（只跑一次）")
    ap.add_argument("--db", default="data/market.db", help="market.db 路径")
    ap.add_argument("--apply", action="store_true",
                    help="真正执行（默认 dry-run 只统计）")
    args = ap.parse_args()
    return run(args.db, dry_run=not args.apply)


if __name__ == "__main__":
    sys.exit(main())

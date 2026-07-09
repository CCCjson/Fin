"""
全市场财务基本面数据补齐脚本

遍历 stock_info 中所有 A 股个股（stock_type='stock'），逐只从 akshare（新浪
财务分析指标）拉取财务数据并写入 financial_data 表。

特性:
  - 只处理个股（ETF / 指数无财报，自动跳过）
  - 限速 + 指数退避重试，降低被新浪接口限频的概率
  - 断点续跑: 进度记录在 scripts/financial_progress.json，重跑自动跳过已完成/已有数据的股票
  - loguru 进度日志，打印 [i/N]

用法:
    cd backend
    conda run -n quant python scripts/backfill_financial.py

可选环境变量:
    FIN_START_YEAR   起始年份（默认 2015）
    FIN_SLEEP        每只之间的间隔秒数（默认 0.5）
    FIN_MAX_RETRY    单只最大重试次数（默认 3）
"""
import os
import sys
import json
import time
import socket
from pathlib import Path

# 全局 socket 超时：akshare 调新浪接口不设超时，碰到挂起的连接会无限阻塞。
# 设默认超时后，挂起请求会在 N 秒后抛异常，由下方 fetch 重试逻辑兜住。
socket.setdefaulttimeout(float(os.getenv("FIN_SOCKET_TIMEOUT", "30")))

# 确保 backend/ 在 Python 路径中
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from loguru import logger

from data_engine.storage.database import get_session
from data_engine.storage.models import StockInfo
from data_engine.storage.repository import FinancialRepository
from data_engine.fetchers.financial import FinancialFetcher

# ── 配置 ──
START_YEAR = os.getenv("FIN_START_YEAR", "2015")
SLEEP_BETWEEN = float(os.getenv("FIN_SLEEP", "0.5"))
MAX_RETRY = int(os.getenv("FIN_MAX_RETRY", "3"))
PROGRESS_FILE = Path(__file__).parent / "financial_progress.json"
FLUSH_EVERY = 20  # 每处理 N 只刷新一次进度文件


def _load_progress() -> dict:
    if PROGRESS_FILE.exists():
        try:
            with open(PROGRESS_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
                data.setdefault("done", [])
                data.setdefault("failed", [])
                return data
        except (json.JSONDecodeError, OSError) as e:
            logger.warning(f"进度文件读取失败，重新开始: {e}")
    return {"done": [], "failed": []}


def _save_progress(progress: dict) -> None:
    tmp = PROGRESS_FILE.with_suffix(".json.tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(progress, f, ensure_ascii=False, indent=2)
    tmp.replace(PROGRESS_FILE)


def main() -> None:
    start = time.time()
    logger.info("=" * 60)
    logger.info("全市场财务数据补齐开始")
    logger.info(f"start_year={START_YEAR} sleep={SLEEP_BETWEEN}s max_retry={MAX_RETRY}")
    logger.info("=" * 60)

    session = get_session()
    fin_repo = FinancialRepository(session)
    fetcher = FinancialFetcher()

    # 1) 目标股票：只取个股（ETF/指数无财报）
    rows = (
        session.query(StockInfo.symbol)
        .filter(StockInfo.stock_type == "stock", StockInfo.is_active == 1)
        .order_by(StockInfo.symbol)
        .all()
    )
    all_symbols = [r[0] for r in rows]
    logger.info(f"目标个股共 {len(all_symbols)} 只")

    # 2) 断点续跑：跳过进度文件里已完成的 + DB 里已有财务数据的
    progress = _load_progress()
    done_set = set(progress["done"])
    db_existing = set(fin_repo.get_symbols_with_data())
    skip_set = done_set | db_existing
    todo = [s for s in all_symbols if s not in skip_set]
    logger.info(
        f"已完成 {len(done_set)} 只 / DB已有 {len(db_existing)} 只 / "
        f"本次待处理 {len(todo)} 只"
    )

    total = len(todo)
    saved_total = 0
    fail_count = 0

    for i, symbol in enumerate(todo, 1):
        df = None
        for attempt in range(1, MAX_RETRY + 1):
            try:
                df = fetcher.fetch_financial_data(symbol, start_year=START_YEAR)
                break
            except Exception as e:  # noqa: BLE001 — 外部接口异常需兜底重试
                wait = SLEEP_BETWEEN * (2 ** attempt)
                logger.warning(
                    f"[{i}/{total}] {symbol} 第 {attempt}/{MAX_RETRY} 次失败: {e}，"
                    f"{wait:.1f}s 后重试"
                )
                time.sleep(wait)

        if df is None or df.empty:
            fail_count += 1
            if symbol not in progress["failed"]:
                progress["failed"].append(symbol)
            logger.error(f"[{i}/{total}] {symbol} 无数据，记为失败")
        else:
            saved = None
            for attempt in range(1, MAX_RETRY + 1):
                try:
                    saved = fin_repo.save_financial_data(symbol, df)
                    break
                except Exception as e:  # noqa: BLE001 — 入库异常需兜底重试
                    # 关键: flush 出错后 session 进入损坏状态，必须 rollback
                    # 才能恢复，否则后续每只 save 都会级联失败
                    session.rollback()
                    wait = SLEEP_BETWEEN * (2 ** attempt)
                    logger.warning(
                        f"[{i}/{total}] {symbol} 入库第 {attempt}/{MAX_RETRY} 次失败: "
                        f"{e}，{wait:.1f}s 后重试"
                    )
                    time.sleep(wait)
            if saved is not None:
                saved_total += saved
                progress["done"].append(symbol)
                if symbol in progress["failed"]:
                    progress["failed"].remove(symbol)
                logger.success(
                    f"[{i}/{total}] {symbol} 保存 {saved} 条新记录 "
                    f"(累计新增 {saved_total})"
                )
            else:
                fail_count += 1
                if symbol not in progress["failed"]:
                    progress["failed"].append(symbol)
                logger.error(f"[{i}/{total}] {symbol} 入库重试耗尽，记为失败")

        if i % FLUSH_EVERY == 0:
            _save_progress(progress)
            logger.info(
                f"进度 {i}/{total} | 已完成累计 {len(progress['done'])} | "
                f"失败 {len(progress['failed'])}"
            )

        time.sleep(SLEEP_BETWEEN)

    _save_progress(progress)
    session.close()

    elapsed = time.time() - start
    logger.info("=" * 60)
    logger.success(
        f"完成! 本次处理 {total} 只 | 新增记录 {saved_total} 条 | "
        f"失败 {fail_count} 只 | 耗时 {elapsed / 60:.1f} min"
    )
    logger.info(f"累计已完成 {len(progress['done'])} 只，失败清单见 {PROGRESS_FILE}")
    logger.info("失败的股票可直接重跑本脚本自动续传")
    logger.info("=" * 60)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        logger.warning("收到中断信号，进度已保存，可重跑续传")

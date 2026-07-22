"""
全市场财务数据流式更新器

复用 FinancialFetcher.fetch_batch_iter（直连新浪 + 多 IP 并发），
NDJSON 事件协议对齐 DailyUpdater.update_stream，供
POST /data/financial/backfill/stream 桥接输出。
"""
import json
import threading
from datetime import datetime, timedelta
from typing import Generator, List

from loguru import logger
from sqlalchemy import func

from acquisition.markets.financial import FinancialFetcher
from common.market import A_SHARE
from common.market_time import market_today
from data_engine.storage.database import get_session
from data_engine.storage.models import DataUpdateLog, FinancialData, StockInfo
from data_engine.storage.repository import FinancialRepository

# 进程级防双跑锁：全量回补要几十分钟，双跑白烧代理配额
_RUN_LOCK = threading.Lock()


class FinancialUpdater:
    """全市场财务数据增量/全量更新器"""

    def update_stream(
        self,
        mode: str = "incremental",
        stale_days: int = 150,
        start_year: str = "2015",
        workers: int = 3,
    ) -> Generator[str, None, None]:
        """
        流式更新全市场财务数据

        Args:
            mode: incremental=跳过近 stale_days 天内有报告期的股票；full=全量
            stale_days: 增量模式的新鲜阈值（默认 150 天，覆盖季报披露滞后）
            start_year: 拉取起始年份
            workers: 并发 worker 数（1-4）

        Yields:
            NDJSON 事件行:
            - {"event":"start","total":N,"skipped_fresh":M,"mode":...}
            - {"event":"progress","current":i,"total":N,"symbol":...,"saved":n,
               "success":s,"failed":f,"new_records":r}
            - {"event":"complete","success":s,"failed":f,"new_records":r,
               "skipped":M,"duration_seconds":d}
            - {"event":"error","message":...}
        """
        if not _RUN_LOCK.acquire(blocking=False):
            yield json.dumps(
                {"event": "error", "message": "财务数据回补已在运行中，请等待完成"},
                ensure_ascii=False,
            ) + "\n"
            return
        try:
            yield from self._run(mode, stale_days, start_year, workers)
        finally:
            _RUN_LOCK.release()

    def _run(
        self,
        mode: str,
        stale_days: int,
        start_year: str,
        workers: int,
    ) -> Generator[str, None, None]:
        start_time = datetime.now()
        session = get_session()
        try:
            # 目标股票：只取个股（ETF/指数无财报）
            rows = (
                session.query(StockInfo.symbol)
                .filter(StockInfo.stock_type == "stock", StockInfo.is_active == 1)
                .order_by(StockInfo.symbol)
                .all()
            )
            all_symbols: List[str] = [r[0] for r in rows]

            # 增量模式：一条 group-by 查出已新鲜的股票并跳过
            skipped = 0
            todo = all_symbols
            if mode == "incremental":
                cutoff = market_today(A_SHARE) - timedelta(days=stale_days)
                fresh_rows = (
                    session.query(FinancialData.symbol)
                    .group_by(FinancialData.symbol)
                    .having(func.max(FinancialData.report_date) >= cutoff)
                    .all()
                )
                fresh = {r[0] for r in fresh_rows}
                todo = [s for s in all_symbols if s not in fresh]
                skipped = len(all_symbols) - len(todo)

            logger.info(
                f"财务更新启动: mode={mode} 全市场 {len(all_symbols)} 只，"
                f"跳过新鲜 {skipped} 只，待处理 {len(todo)} 只 (workers={workers})"
            )
            yield json.dumps({
                "event": "start",
                "total": len(todo),
                "skipped_fresh": skipped,
                "mode": mode,
            }, ensure_ascii=False) + "\n"

            success = 0
            failed = 0
            new_records = 0

            if todo:
                repo = FinancialRepository(session)
                fetcher = FinancialFetcher()
                total = len(todo)
                emit_interval = max(1, total // 200)

                i = 0
                for sym, df in fetcher.fetch_batch_iter(todo, start_year=start_year, workers=workers):
                    if sym is None:
                        # 心跳标记：worker 仍在跑但暂无终态结果，只为避免长时间
                        # 无输出，不计入 progress 分子
                        yield json.dumps({
                            "event": "progress",
                            "current": i,
                            "total": total,
                            "symbol": "",
                            "success": success,
                            "failed": failed,
                            "new_records": new_records,
                            "heartbeat": True,
                        }, ensure_ascii=False) + "\n"
                        continue

                    i += 1
                    saved = 0
                    if df is None or df.empty:
                        failed += 1
                    else:
                        try:
                            saved = repo.save_financial_data(sym, df)
                            new_records += saved
                            success += 1
                        except Exception as e:
                            # flush 出错后 session 进入损坏状态，必须 rollback
                            # 否则后续每只 save 都会级联失败
                            try:
                                session.rollback()
                            except Exception:
                                pass
                            failed += 1
                            logger.warning(f"{sym} 财务数据入库失败: {e}")

                    if i % emit_interval == 0 or i == total:
                        yield json.dumps({
                            "event": "progress",
                            "current": i,
                            "total": total,
                            "symbol": sym,
                            "saved": saved,
                            "success": success,
                            "failed": failed,
                            "new_records": new_records,
                        }, ensure_ascii=False) + "\n"

            end_time = datetime.now()
            duration = (end_time - start_time).total_seconds()

            try:
                log = DataUpdateLog(
                    market="a_share",
                    update_type="financial",
                    symbols_count=success,
                    records_count=new_records,
                    status="success" if failed == 0 else "partial",
                    started_at=start_time,
                    completed_at=end_time,
                    duration_seconds=duration,
                )
                session.add(log)
                session.commit()
            except Exception as e:
                logger.warning(f"保存财务更新日志失败: {e}")
                try:
                    session.rollback()
                except Exception:
                    pass

            logger.info(
                f"财务更新完成: 成功 {success}, 失败 {failed}, "
                f"新记录 {new_records}, 跳过 {skipped}, 耗时 {duration:.1f}s"
            )
            yield json.dumps({
                "event": "complete",
                "success": success,
                "failed": failed,
                "new_records": new_records,
                "skipped": skipped,
                "duration_seconds": round(duration, 1),
            }, ensure_ascii=False) + "\n"
        finally:
            session.close()

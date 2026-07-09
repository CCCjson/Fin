"""
全市场估值快照回填 — 调东财全市场实时接口，顺带拿 PE/PB/市值入 StockValuation 表。

可作为脚本运行（conda run -n quant python scripts/backfill_valuation.py），
也提供 refresh_all_valuations() 供 screener 的 /refresh-valuation 端点复用。
"""
import sys
from datetime import date, datetime
from pathlib import Path
from typing import Callable, Dict, Optional

from loguru import logger

# 允许脚本方式运行
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from data_engine.storage.database import get_session
from data_engine.storage.repository import ValuationRepository


def _to_float(v) -> Optional[float]:
    if v is None:
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def refresh_all_valuations(progress_callback: Optional[Callable[[int, int], None]] = None) -> Dict:
    """
    拉全市场实时行情，提取估值因子 upsert 进 StockValuation（snapshot_date=今天）。

    Returns: {total, saved, updated}
    """
    from data_engine.fetchers.realtime import fetch_a_share_realtime
    from data_engine.storage.models import DataUpdateLog

    logger.info("开始全市场估值快照刷新...")
    start_time = datetime.now()
    rows = fetch_a_share_realtime()
    today = date.today()
    total = len(rows)
    saved = updated = 0
    error_message = None

    session = get_session()
    try:
        repo = ValuationRepository(session)
        try:
            for i, r in enumerate(rows, 1):
                symbol = r.get("symbol")
                if not symbol:
                    continue
                values = {
                    "pe": _to_float(r.get("pe_ratio")),
                    "pe_ttm": _to_float(r.get("pe_ttm")),
                    "pb": _to_float(r.get("pb_ratio")),
                    "total_mv": _to_float(r.get("total_mv")),
                    "circ_mv": _to_float(r.get("circ_mv")),
                }
                # 全为空则跳过
                if all(v is None for v in values.values()):
                    continue
                is_new = repo.upsert_snapshot(symbol, today, values)
                if is_new:
                    saved += 1
                else:
                    updated += 1
                if progress_callback and i % 200 == 0:
                    progress_callback(i, total)
        except Exception as e:  # noqa: BLE001 — 记日志用，不吞掉，随后重新抛出
            error_message = str(e)
            raise
        finally:
            end_time = datetime.now()
            try:
                log = DataUpdateLog(
                    market="a_share",
                    update_type="valuation",
                    symbols_count=total,
                    records_count=saved + updated,
                    status="failed" if error_message else ("success" if (saved + updated) else "partial"),
                    error_message=error_message,
                    started_at=start_time,
                    completed_at=end_time,
                    duration_seconds=(end_time - start_time).total_seconds(),
                )
                session.add(log)
                session.commit()
            except Exception as log_err:
                logger.warning(f"保存估值更新日志失败: {log_err}")
                session.rollback()
    finally:
        session.close()

    result = {"total": total, "saved": saved, "updated": updated}
    logger.info(f"估值快照刷新完成: {result}")
    return result


if __name__ == "__main__":
    print(refresh_all_valuations(
        progress_callback=lambda i, t: print(f"  进度 {i}/{t}")
    ))

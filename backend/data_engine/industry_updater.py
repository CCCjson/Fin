"""
把 A 股所属行业回填进 `StockInfo.industry`。

分层：取数在 `acquisition.markets.industry`，本模块只做编排与落库
（CODING_STANDARDS §0 —— data_engine 只调度与存储）。

行业分类几乎不变，是**低频任务**：手工跑或月度跑一次即可。

    conda run -n quant python -m data_engine.industry_updater
"""
from loguru import logger
from sqlalchemy.exc import SQLAlchemyError

from acquisition.markets.industry import fetch_a_share_industry_map
from data_engine.storage.database import get_session
from data_engine.storage.models import StockInfo


def backfill_industry(*, limit: int | None = None) -> dict[str, int]:
    """拉全市场行业映射并写入 `StockInfo.industry`。

    只更新**有变化**的行（新填 or 改名），避免把 5000 多行的 `updated_at`
    全部刷一遍。库里没有的 symbol 直接跳过——本函数不负责建 StockInfo 行。

    Returns:
        `{"fetched": 拉到的只数, "updated": 实际写库的行数, "missing": 库里没有的只数}`
    """
    mapping = fetch_a_share_industry_map(limit=limit)
    if not mapping:
        logger.warning("行业映射为空，不写库")
        return {"fetched": 0, "updated": 0, "missing": 0}

    session = get_session()
    try:
        rows = (session.query(StockInfo)
                .filter(StockInfo.symbol.in_(list(mapping.keys()))).all())
        found = {r.symbol for r in rows}

        updated = 0
        for row in rows:
            new = mapping[row.symbol]
            if row.industry != new:
                row.industry = new
                updated += 1

        session.commit()
    except SQLAlchemyError:
        session.rollback()
        raise
    finally:
        session.close()

    missing = len(mapping) - len(found)
    logger.info(f"行业回填完成：拉到 {len(mapping)} 只，写库 {updated} 行，"
                f"库里没有 {missing} 只")
    return {"fetched": len(mapping), "updated": updated, "missing": missing}


if __name__ == "__main__":
    import sys

    logger.remove()
    logger.add(sys.stdout, level="INFO",
               format="<green>{time:HH:mm:ss}</green> | <level>{message}</level>")
    result = backfill_industry()
    logger.info(f"结果: {result}")

"""
把 A 股所属行业回填进 `StockInfo.industry`。

分层：取数在 `acquisition.markets.industry`（多源链：东财→巨潮），本模块只做
编排与落库（CODING_STANDARDS §0 —— data_engine 只调度与存储）。

行业分类几乎不变，是**低频任务**：手工跑或月度跑一次即可。

    # ⚠️ 必须带 --no-capture-output + python -u，否则 conda run 会把进度日志
    # 缓冲到进程结束才一次性吐出来（实测：33 分钟的任务全程 0 字节输出）
    conda run --no-capture-output -n quant python -u -m data_engine.industry_updater
    conda run --no-capture-output -n quant python -u -m data_engine.industry_updater cninfo --resume

两个源的分类口径不同（东财=申万二级「银行Ⅱ」/ 巨潮=证监会二级「货币金融服务」），
所以**一轮只用一个源**。换源时先清空 industry 列，绝不让两套口径混在一起——
混了行业集中度 HHI 就是垃圾。上一轮用的源记在 `_SOURCE_MARKER`。
"""
import json
import sys
import time
from pathlib import Path

from loguru import logger
from sqlalchemy.exc import SQLAlchemyError

from acquisition.markets.industry import (
    IndustrySource,
    resolve_source,
    stream_a_share_industry,
)
from data_engine.storage.database import get_session
from data_engine.storage.models import StockInfo

_BACKEND_DIR = Path(__file__).resolve().parent.parent
_SOURCE_MARKER = _BACKEND_DIR / "data" / "industry_source.json"
_BATCH_SIZE = 200


def _read_last_source() -> str | None:
    try:
        return str(json.loads(_SOURCE_MARKER.read_text(encoding="utf-8"))["source"])
    except (OSError, ValueError, KeyError):
        return None


def _write_last_source(source: str, fetched_this_run: int) -> None:
    _SOURCE_MARKER.parent.mkdir(parents=True, exist_ok=True)
    tmp = _SOURCE_MARKER.with_suffix(".json.tmp")
    tmp.write_text(json.dumps({
        # fetched_this_run = 本轮拉到几条，**不是**库里总数（resume 时只补缺口）
        "source": source, "fetched_this_run": fetched_this_run,
        "updated_at": time.strftime("%Y-%m-%d %H:%M:%S"),
    }, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(_SOURCE_MARKER)


def _stock_only(q):
    """只要**个股**：指数和 ETF 没有「所属行业」。

    指数尤其危险——它和个股**同码不同所**。剥掉后缀去查巨潮会串到别人身上：
    `000001.SH`（上证指数）→ 裸码 `000001` → 平安银行 →「货币金融服务」。
    实测污染了 5 只指数：上证50 拿到康佳集团的行业、科创50 拿到国城矿业的、
    中证500 拿到厦门港务的。规范 §1 早记着这个同码歧义，我第一版没防住。
    """
    return q.filter(StockInfo.market == "a_share",
                    StockInfo.is_active == 1,
                    StockInfo.stock_type == "stock")


def _active_a_share_symbols(session, *, only_missing: bool = False) -> list[str]:
    q = _stock_only(session.query(StockInfo.symbol))
    if only_missing:
        q = q.filter((StockInfo.industry.is_(None)) | (StockInfo.industry == ""))
    return [r[0] for r in q.order_by(StockInfo.symbol).all()]


def _clear_industry() -> int:
    """换源时清空旧口径。返回清掉的行数。

    这里**故意不加** `_stock_only`：它还要负责清掉历史上误写到指数行的脏数据。
    """
    session = get_session()
    try:
        n = (session.query(StockInfo)
             .filter(StockInfo.market == "a_share",
                     StockInfo.industry.isnot(None),
                     StockInfo.industry != "")
             .update({StockInfo.industry: None}, synchronize_session=False))
        session.commit()
        return int(n)
    except SQLAlchemyError:
        session.rollback()
        raise
    finally:
        session.close()


def _commit_batch(batch: dict[str, str]) -> int:
    """写一批，只 UPDATE 真有变化的行。返回实际改动行数。

    `_stock_only` 在这里再挡一道：候选集已经排除了指数/ETF，但取数层若因为
    同码歧义吐回一个指数 symbol，落库这层也不能让它进去。
    """
    if not batch:
        return 0
    session = get_session()
    try:
        rows = _stock_only(session.query(StockInfo)) \
            .filter(StockInfo.symbol.in_(list(batch.keys()))).all()
        changed = 0
        for row in rows:
            new = batch[row.symbol]
            if row.industry != new:
                row.industry = new
                changed += 1
        session.commit()
        return changed
    except SQLAlchemyError:
        session.rollback()
        raise
    finally:
        session.close()


def backfill_industry(*, source: IndustrySource = "auto",
                      resume: bool = False,
                      limit: int | None = None,
                      batch_size: int = _BATCH_SIZE) -> dict:
    """拉行业映射并**分批**写入 `StockInfo.industry`。

    分批 commit 而非一次性：全市场逐只查要跑半小时，一次性攒完再写意味着
    中途挂掉半小时全白费，过程还完全不可观测。

    Args:
        source: `auto` 先探东财、不通换巨潮。
        resume: 只补 `industry` 为空的票。换源时旧口径会被清空，于是每只都算缺
            行业，resume 自动退化为全量重跑（口径永远不会混）。
        limit: 只处理前 N 只（调试用）。

    Returns:
        `{"source", "fetched", "updated", "skipped_resume", "elapsed_seconds"}`
    """
    session = get_session()
    try:
        all_symbols = _active_a_share_symbols(session)
    finally:
        session.close()

    # 源必须在**建 stream 之前**定下来：它决定要不要清空旧口径、resume 是否合法，
    # 以及巨潮源该喂哪批 symbol。`resolve_source` 里那次探测只发一个请求，
    # 之后建 stream 时传显式 source，不会再探第二次。
    used = resolve_source(all_symbols, source=source)
    last = _read_last_source()

    if last is not None and last != used:
        # 换源必须清空旧口径：两套分类体系混在一列里，行业集中度 HHI 就是垃圾。
        # 清空之后每只票都算「缺行业」，所以 resume **自动**退化成全量重跑
        # ——不需要额外的守卫（写过一个，变异测试证明它什么也没多做）。
        cleared = _clear_industry()
        logger.warning(f"换源 {last} → {used}：已清空 {cleared} 行旧口径的 industry"
                       f"{'；resume 随之退化为全量重跑' if resume else ''}")

    todo = all_symbols
    skipped = 0
    if resume:
        session = get_session()
        try:
            todo = _active_a_share_symbols(session, only_missing=True)
        finally:
            session.close()
        skipped = len(all_symbols) - len(todo)
        logger.info(f"resume：跳过已有行业的 {skipped} 只，待补 {len(todo)} 只")
        if not todo:
            logger.info("没有缺行业的票，无需回填")
            return {"source": used, "fetched": 0, "updated": 0,
                    "skipped_resume": skipped, "elapsed_seconds": 0.0}

    _, stream = stream_a_share_industry(todo, source=used, limit=limit)  # type: ignore[arg-type]

    # 标记必须**在第一批落库之前**写：跑一半崩掉的任务也会留下数据（分批 commit
    # 的代价），下一轮得认得出库里躺的是哪套口径，才知道该不该清空。
    # 只在结束时写的话，崩溃的半截任务会让 marker 停在上一轮，`--resume` 就会
    # 拿新源去补缺口 —— 两套分类混进同一列。
    _write_last_source(used, 0)

    logger.info(f"行业回填开始（源={used}，本轮 {len(todo)} 只）")
    started = time.time()

    batch: dict[str, str] = {}
    fetched = updated = 0
    for symbol, industry in stream:
        batch[symbol] = industry
        fetched += 1
        if len(batch) >= batch_size:
            updated += _commit_batch(batch)
            logger.info(f"已落库 {fetched} 只（改动 {updated} 行）")
            batch = {}
    updated += _commit_batch(batch)

    elapsed = time.time() - started
    _write_last_source(used, fetched)
    logger.info(f"行业回填完成（源={used}）：拉到 {fetched} 只，写库 {updated} 行，"
                f"耗时 {elapsed / 60:.1f} 分钟")
    return {"source": used, "fetched": fetched, "updated": updated,
            "skipped_resume": skipped, "elapsed_seconds": round(elapsed, 1)}


if __name__ == "__main__":
    logger.remove()
    logger.add(sys.stdout, level="INFO",
               format="<green>{time:HH:mm:ss}</green> | <level>{message}</level>")

    args = [a for a in sys.argv[1:] if not a.startswith("-")]
    src: IndustrySource = args[0] if args else "auto"  # type: ignore[assignment]
    result = backfill_industry(source=src, resume="--resume" in sys.argv)
    logger.info(f"结果: {result}")

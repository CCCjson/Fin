"""
内部数据源 — 把主库 market.db 已有结构化数据转成可检索 Document（P0 打底，零抓取）。

目前覆盖：financial_data（季度财务指标）→ 一条记录转一段自然语言模板 → 单 chunk 不切。
后续可扩展 news_articles / analysis_reports。

注意：读的是**主库** market.db（data_engine.storage），写的是**知识库** knowledge.db。
"""
from typing import List, Optional, Dict

from loguru import logger

from data_engine.storage.database import get_session as get_main_session
from data_engine.storage.models import FinancialData, StockInfo
from knowledge_engine.ingest import IngestPipeline


def _fmt(v, unit: str = "") -> str:
    return f"{v:.2f}{unit}" if isinstance(v, (int, float)) else "—"


def _financial_to_text(name: str, symbol: str, fd: FinancialData) -> str:
    """把一条季度财务记录拼成自然语言段落（便于语义检索）。"""
    period = fd.report_date.strftime("%Y年%m月%d日") if fd.report_date else "未知报告期"
    return (
        f"{name}（{symbol}）{period} 财务指标："
        f"每股收益 {_fmt(fd.eps, '元')}，净资产收益率ROE {_fmt(fd.roe, '%')}，"
        f"总资产利润率ROA {_fmt(fd.roa, '%')}，销售毛利率 {_fmt(fd.gross_margin, '%')}，"
        f"销售净利率 {_fmt(fd.net_margin, '%')}，每股净资产 {_fmt(fd.bvps, '元')}。"
        f"成长能力：主营收入同比 {_fmt(fd.revenue_yoy, '%')}，净利润同比 {_fmt(fd.net_profit_yoy, '%')}。"
        f"偿债能力：流动比率 {_fmt(fd.current_ratio)}，资产负债率 {_fmt(fd.debt_ratio, '%')}。"
        f"运营效率：总资产周转率 {_fmt(fd.total_asset_turnover, '次')}。"
    )


def ingest_financial(symbols: Optional[List[str]] = None, limit_per_symbol: int = 8) -> Dict:
    """
    把财务数据摄入知识库。
    symbols=None 时全市场（量大慎用）；建议先传几个 symbol 验证。
    limit_per_symbol: 每只股票取最近 N 个报告期。
    """
    pipeline = IngestPipeline()
    ingested, skipped = 0, 0

    with get_main_session() as ms:
        q = ms.query(FinancialData)
        if symbols:
            q = q.filter(FinancialData.symbol.in_(symbols))
        # 名称映射
        name_rows = ms.query(StockInfo.symbol, StockInfo.name)
        if symbols:
            name_rows = name_rows.filter(StockInfo.symbol.in_(symbols))
        names = dict(name_rows.all())

        # 按 symbol 取最近 N 期
        targets = symbols or [r[0] for r in ms.query(FinancialData.symbol).distinct().all()]
        records = []
        for sym in targets:
            rows = (ms.query(FinancialData)
                    .filter(FinancialData.symbol == sym)
                    .order_by(FinancialData.report_date.desc())
                    .limit(limit_per_symbol).all())
            records.extend(rows)
        # detach
        for r in records:
            ms.expunge(r)

    for fd in records:
        name = names.get(fd.symbol, fd.symbol)
        text = _financial_to_text(name, fd.symbol, fd)
        native_id = f"{fd.symbol}:{fd.report_date}"
        res = pipeline.ingest_text(
            source_type="financial",
            title=f"{name}({fd.symbol}) {fd.report_date} 财务指标",
            text=text,
            native_id=native_id,
            language="zh",
            metadata={"symbol": fd.symbol, "report_date": str(fd.report_date)},
            # 一条记录就是一片，整段不切
            pre_chunked=[{"seq": 0, "text": text, "token_count": None}],
        )
        if res["is_new"]:
            ingested += 1
        else:
            skipped += 1

    logger.info(f"内部财务数据摄入完成：新增 {ingested}，跳过 {skipped}")
    return {"ingested": ingested, "skipped": skipped, "total": len(records)}

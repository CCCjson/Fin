"""
cninfo 巨潮资讯 A股法定披露原文源 → 知识文档（source_type=financial）。

先探站结论（2026-07）：
- akshare `stock_zh_a_disclosure_report_cninfo(symbol, market, category, start_date, end_date)`
  返回公告列表：代码/简称/公告标题/公告时间/公告链接（详情页，含 announcementId）。
- PDF 直取：`http://static.cninfo.com.cn/finalpage/{日期}/{announcementId}.PDF`，
  **无盾无 cookie**，curl_cffi 直接 200 拿全文（年报可达 200-300 页）。

取舍：全文年报几百页、大量财报附注表格属 RAG 低价值 → **限页数**（默认前 60 页，
覆盖 董事会报告/管理层讨论MD&A/业务概况/风险因素 这些精华），避免库爆。
国内源：公告列表查询走 net.domestic.domestic_akshare 包一层（快代理轮换+直连兜底），
不依赖 apply_proxy_env——那个只在判定为「direct 模式」时清代理 env，Clash 存活时
不会覆盖 .env 里可能配错端口的残留代理值，会导致 ProxyError（已现场验证过这个坑）。
PDF 直取本身走 curl_cffi 显式 `proxies={"http": "", "https": ""}`，不受影响。
"""
import re
from datetime import datetime
from typing import List, Optional, Dict, Any

from loguru import logger

from knowledge_engine.ingest import IngestPipeline


def _parse_date(s: str) -> Optional[datetime]:
    """'YYYY-MM-DD' 前10位解析，脏日期/空值返回 None，不让摄入因此失败。"""
    try:
        return datetime.strptime(str(s)[:10], "%Y-%m-%d")
    except (ValueError, TypeError):
        return None


def _fetch_cninfo_pdf_text(announcement_id: str, date: str, max_pages: int = 60) -> str:
    """按 announcementId + 日期拼 static PDF 直取正文（前 max_pages 页）。"""
    if not announcement_id or not date:
        return ""
    url = f"http://static.cninfo.com.cn/finalpage/{date}/{announcement_id}.PDF"
    try:
        from curl_cffi import requests as cffi
        import fitz
        s = cffi.Session(impersonate="chrome120")
        r = s.get(url, timeout=40, proxies={"http": "", "https": ""})
        if r.status_code != 200 or r.content[:4] != b"%PDF":
            return ""
        doc = fitz.open(stream=r.content, filetype="pdf")
        n = min(max_pages, doc.page_count)
        text = "\n".join(doc[i].get_text() for i in range(n))
        doc.close()
        return text
    except Exception as e:  # noqa: BLE001
        logger.debug(f"cninfo PDF 抽文失败 {url}: {e}")
        return ""


def ingest_cninfo_filings(
    symbols: List[str],
    categories: Optional[List[str]] = None,
    per_category: int = 1,
    max_pages: int = 60,
    start_date: str = "20240101",
    end_date: str = "20261231",
) -> Dict[str, Any]:
    """
    摄入一批股票的 cninfo 法定披露原文。
    symbols: 代码列表，'000001' 或 '000001.SZ'。
    categories: 披露类别，默认 ['年报','半年报']（akshare category 值）。
    per_category: 每类取最近 N 份（默认 1）。
    max_pages: 每份 PDF 抽前 N 页（默认 60，取精华避免库爆）。
    """
    import akshare as ak
    from net.domestic import domestic_akshare

    categories = categories or ["年报", "半年报"]
    pipeline = IngestPipeline()
    ingested = skipped = failed = 0

    for sym in symbols:
        code = sym.split(".")[0]
        for cat in categories:
            try:
                df = domestic_akshare(
                    ak.stock_zh_a_disclosure_report_cninfo,
                    symbol=code, market="沪深京", category=cat,
                    start_date=start_date, end_date=end_date,
                )
            except Exception as e:  # noqa: BLE001
                logger.warning(f"cninfo 公告拉取失败 {sym}/{cat}: {e}")
                failed += 1
                continue
            if df is None or df.empty:
                continue

            for _, row in df.head(per_category).iterrows():
                title = row.get("公告标题", "")
                date = str(row.get("公告时间", ""))[:10]
                link = row.get("公告链接", "") or ""
                m = re.search(r"announcementId=(\d+)", link)
                aid = m.group(1) if m else ""
                body = _fetch_cninfo_pdf_text(aid, date, max_pages=max_pages)
                if not body or len(body) < 500:
                    failed += 1
                    logger.debug(f"cninfo 正文太短/失败：{sym} {title}")
                    continue

                name = row.get("简称", "")
                header = f"{name}（{code}）{date} 法定披露《{title}》：\n\n"
                res = pipeline.ingest_text(
                    source_type="financial",
                    title=f"{name}《{title}》",
                    text=header + body,
                    url=f"http://static.cninfo.com.cn/finalpage/{date}/{aid}.PDF",
                    native_id=f"cninfo:{aid}",
                    published_at=_parse_date(date),
                    language="zh",
                    metadata={"symbol": code, "name": name, "category": cat,
                              "date": date, "source": "cninfo", "announcement_id": aid},
                )
                if res["is_new"]:
                    ingested += 1
                else:
                    skipped += 1
                logger.info(f"cninfo 摄入 {sym} {cat}: {title}（{len(body)}字 status={res['status']}）")

    logger.info(f"cninfo 法定披露摄入完成：新增 {ingested}，跳过 {skipped}，失败 {failed}")
    return {"ingested": ingested, "skipped": skipped, "failed": failed}

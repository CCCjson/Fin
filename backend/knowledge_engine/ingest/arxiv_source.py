"""
arXiv 官方 API 摄入源 → 知识文档（source_type=paper）。

2026-07 全量补齐：替代原先 web_source.ingest_papers()（DDG 搜"site:arxiv.org"再抓正文
的模拟方式）——官方 API 免费、无需 key，能按分类(q-fin.*)+提交日期精确过滤，且能
可靠拿到 published_at（DDG 方式根本拿不到），查全率/精准度都远好于搜索引擎命中。

★ arXiv 官方限速要求：请求间隔 ≥3 秒，写死不做成可调参数，避免误改违反使用条款。
★ 走 acquisition.websearch.session 里已有的海外代理策略（resolve_overseas：
  先探本地直连，不通走 Shadowrocket），跟 read_url/DDG 同一套连接方式，不新起。
"""
import time
from datetime import datetime, timedelta
from typing import List, Optional, Dict, Any
from urllib.parse import urlencode

from loguru import logger
from lxml import etree

from knowledge_engine.ingest import IngestPipeline
from acquisition.websearch.session import make_plain_session, bounded_get
from acquisition.websearch.fetch import read_url

ARXIV_API = "http://export.arxiv.org/api/query"
ARXIV_RATE_LIMIT_SECONDS = 3.0  # arXiv 官方要求，不做成配置项

_ATOM_NS = {"atom": "http://www.w3.org/2005/Atom", "arxiv": "http://arxiv.org/schemas/atom"}

DEFAULT_CATEGORIES = ["q-fin.PM", "q-fin.ST", "q-fin.TR", "q-fin.CP", "q-fin.RM"]
DEFAULT_KEYWORDS = [
    "alpha factor", "stock prediction", "quantitative trading",
    "factor investing", "portfolio optimization",
]


def _build_search_query(categories: List[str], keywords: Optional[List[str]],
                        start_date: str, end_date: str) -> str:
    cat_expr = " OR ".join(f"cat:{c}" for c in categories)
    parts = [f"({cat_expr})"]
    if keywords:
        kw_expr = " OR ".join(f'abs:"{kw}"' for kw in keywords)
        parts.append(f"({kw_expr})")
    date_expr = f"submittedDate:[{start_date}000000 TO {end_date}235959]"
    parts.append(date_expr)
    return " AND ".join(parts)


def _parse_entry(entry) -> Dict[str, Any]:
    def text(path: str, ns=_ATOM_NS) -> str:
        el = entry.find(path, ns)
        return (el.text or "").strip() if el is not None else ""

    id_url = text("atom:id")
    arxiv_id = id_url.rsplit("/", 1)[-1] if id_url else ""
    authors = [
        (a.find("atom:name", _ATOM_NS).text or "").strip()
        for a in entry.findall("atom:author", _ATOM_NS)
        if a.find("atom:name", _ATOM_NS) is not None
    ]
    categories = [
        c.get("term") for c in entry.findall("atom:category", _ATOM_NS) if c.get("term")
    ]
    return {
        "arxiv_id": arxiv_id,
        "title": " ".join(text("atom:title").split()),
        "summary": " ".join(text("atom:summary").split()),
        "published": text("atom:published"),
        "updated": text("atom:updated"),
        "authors": authors,
        "categories": categories,
    }


def _parse_arxiv_date(s: str) -> Optional[datetime]:
    if not s:
        return None
    try:
        return datetime.strptime(s, "%Y-%m-%dT%H:%M:%SZ")
    except ValueError:
        return None


def _fetch_page(query: str, start: int, max_results: int) -> List[Dict[str, Any]]:
    params = {
        "search_query": query, "start": start, "max_results": max_results,
        "sortBy": "submittedDate", "sortOrder": "descending",
    }
    url = f"{ARXIV_API}?{urlencode(params)}"
    session = make_plain_session()
    cap = bounded_get(session, url, mode="bytes", timeout=30)  # XML 封顶 50MB
    if cap.status >= 400 or cap.status == 0:
        raise RuntimeError(f"arxiv HTTP {cap.status}")
    root = etree.fromstring(cap.data or b"")
    return [_parse_entry(e) for e in root.findall("atom:entry", _ATOM_NS)]


def ingest_arxiv_papers(
    categories: Optional[List[str]] = None,
    keywords: Optional[List[str]] = None,
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
    max_results: int = 300,
    full_text: bool = False,
) -> Dict[str, Any]:
    """
    从 arXiv 官方 API 摄入量化金融相关论文（source_type='paper'）。
    categories: arXiv 分类列表，默认 DEFAULT_CATEGORIES（q-fin.* 五个子类）。
    keywords: 叠加摘要关键词过滤，默认 DEFAULT_KEYWORDS（不传则不叠加，只按分类拉全部）。
    start_date/end_date: 'YYYYMMDD'，提交日期窗口，默认 start=今天-4年，end=今天。
    max_results: 单次最多拉取篇数（分页请求，每页 ≤100）。
    full_text: True 时额外抓 PDF 全文（走现有 read_url，PDF直链无反爬），默认 False
        （abstract 摘要通常已够 RAG 检索/alpha提炼用，全文另需要更多存储和切片开销）。
    """
    categories = categories or DEFAULT_CATEGORIES
    keywords = keywords if keywords is not None else DEFAULT_KEYWORDS
    today = datetime.now()
    start_dt = datetime.strptime(start_date, "%Y%m%d") if start_date else today - timedelta(days=365 * 4)
    end_dt = datetime.strptime(end_date, "%Y%m%d") if end_date else today

    query = _build_search_query(categories, keywords, start_dt.strftime("%Y%m%d"), end_dt.strftime("%Y%m%d"))
    pipeline = IngestPipeline()
    ingested = skipped = failed = 0
    docs: List[Dict[str, Any]] = []

    page_size = min(100, max_results)
    start = 0
    while start < max_results:
        batch_size = min(page_size, max_results - start)
        try:
            entries = _fetch_page(query, start, batch_size)
        except Exception as e:  # noqa: BLE001
            logger.warning(f"arXiv API 请求失败 start={start}: {e}")
            break
        time.sleep(ARXIV_RATE_LIMIT_SECONDS)
        if not entries:
            break

        for entry in entries:
            arxiv_id = entry["arxiv_id"]
            if not arxiv_id:
                continue
            published_at = _parse_arxiv_date(entry["published"])
            text = entry["summary"]
            if full_text:
                pdf_url = f"https://arxiv.org/pdf/{arxiv_id}"
                full = read_url(pdf_url).get("text", "")
                if full:
                    text = f"{entry['title']}\n\n{entry['summary']}\n\n【全文】\n{full}"

            res = pipeline.ingest_text(
                source_type="paper",
                title=entry["title"],
                text=text,
                url=f"https://arxiv.org/abs/{arxiv_id}",
                native_id=f"arxiv:{arxiv_id}",
                authors="; ".join(entry["authors"]),
                published_at=published_at,
                language="en",
                metadata={"arxiv_id": arxiv_id, "categories": entry["categories"],
                          "updated": entry["updated"]},
            )
            if res["is_new"]:
                ingested += 1
            else:
                skipped += 1
            docs.append({"doc_id": res["doc_id"], "title": entry["title"],
                         "arxiv_id": arxiv_id, "status": res["status"]})

        start += len(entries)
        if len(entries) < batch_size:
            break  # 已到结果末尾

    logger.info(f"arXiv 论文摄入完成：新增 {ingested}，跳过 {skipped}，失败 {failed}")
    return {"ingested": ingested, "skipped": skipped, "failed": failed, "docs": docs}

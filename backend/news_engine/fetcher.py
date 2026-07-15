"""
新闻抓取器 — AkShare (A股) + Finnhub (全球市场)
"""
import hashlib
import os
from datetime import datetime, timedelta
from typing import Dict, Generator, List, Optional

from dotenv import load_dotenv
from common.market import to_bare_code
from loguru import logger
from sqlalchemy.dialects.sqlite import insert as sqlite_insert

from acquisition.markets.finnhub_news import (
    fetch_company_news,
    fetch_general_news as _finnhub_general_news,
)
from data_engine.storage.database import get_session
from data_engine.storage.models import NewsArticle

load_dotenv(override=True)


def _make_article_id(source: str, url: str) -> str:
    """生成文章唯一 ID：MD5(source + url)"""
    return hashlib.md5(f"{source}:{url}".encode()).hexdigest()


class NewsFetcher:
    """新闻抓取服务"""

    def __init__(self) -> None:
        self._finnhub_key = os.getenv("FINNHUB_API_KEY", "")

    # Finnhub 出网（company_news/general_news + 海外代理注入）已收进
    # acquisition.markets.finnhub_news（13.4-2 债1）；本类只做业务映射，不再自持 SDK。

    # ==================== A 股个股新闻 ====================

    def fetch_a_share_news(self, symbol: str) -> List[Dict]:
        """
        抓取 A 股个股新闻（AkShare: stock_news_em）。
        symbol: 纯数字代码，如 '300059'
        """
        return self._fetch_em_stock_news(symbol, market="a_share", log_label="A 股")

    def fetch_hk_stock_news(self, symbol: str) -> List[Dict]:
        """
        抓取港股个股新闻。ak.stock_news_em 本质是东财按代码关键词全文检索，
        对纯数字港股代码（如 '00700'）同样能命中真实相关新闻（已现场验证：
        symbol='00700' 返回腾讯控股相关新闻）——复用与 A 股完全相同的调用+解析
        路径，只是去后缀规则不同（.HK 而不是 .SH/.SZ）。
        注意：这条路径不适用于美股，同一函数对字母 ticker 返回的是泛化噪声，
        美股走 fetch_us_stock_news（Finnhub company_news）。
        symbol: 如 '00700.HK'
        """
        return self._fetch_em_stock_news(symbol, market="hk_stock", log_label="港股")

    def _fetch_em_stock_news(self, symbol: str, *, market: str, log_label: str) -> List[Dict]:
        import akshare as ak
        from net import domestic_akshare

        raw_symbol = to_bare_code(symbol)
        logger.info(f"正在抓取{log_label}新闻: {raw_symbol}")

        try:
            # 新闻是低频接口，先试本地直连省额度；直连被掐断才上快代理轮换 IP
            # （Jason 2026-07-10 拍板）。prefer_direct 是显式的第 0 轮，不是降级——
            # 直连失败后取不到 IP 依然抛 ProxyExhaustedError，绝不回头再直连。
            df = domestic_akshare(ak.stock_news_em, symbol=raw_symbol, prefer_direct=True)
        except Exception as e:
            logger.error(f"AkShare 新闻抓取失败: {e}")
            return []

        articles: List[Dict] = []
        for _, row in df.iterrows():
            title = str(row.get("新闻标题", "")).strip()
            content = str(row.get("新闻内容", "")).strip()
            url = str(row.get("新闻链接", "")).strip()
            source = str(row.get("文章来源", "东方财富")).strip()
            pub_time = row.get("发布时间", None)

            if not title:
                continue

            published_at = None
            if pub_time is not None:
                try:
                    published_at = datetime.strptime(str(pub_time), "%Y-%m-%d %H:%M:%S")
                except (ValueError, TypeError):
                    published_at = datetime.now()

            article_id = _make_article_id(source, url or title)
            articles.append({
                "article_id": article_id,
                "symbol": raw_symbol,
                "market": market,
                "title": title,
                "content": content,
                "summary": content[:200] if content else title,
                "source": source,
                "url": url,
                "image_url": None,
                "language": "zh",
                "published_at": published_at or datetime.now(),
            })

        logger.info(f"{log_label}新闻抓取完成: {len(articles)} 条")
        return articles

    # ==================== 美股个股新闻 (Finnhub company_news) ====================

    def fetch_us_stock_news(self, symbol: str, days: int = 7) -> List[Dict]:
        """
        抓取美股个股新闻（Finnhub: company_news，按 ticker 精确过滤，
        不是 fetch_general_news 那种整体市场新闻）。
        symbol: 纯 ticker，不带后缀，如 'AAPL'（内部存储格式本就如此，
        USStockFetcher.validate_symbol 正则 ^[A-Z]{1,5}$ 确认过）。
        """
        if not self._finnhub_key:
            logger.warning("FINNHUB_API_KEY 未配置，跳过美股个股新闻抓取")
            return []

        raw_symbol = to_bare_code(symbol).strip().upper()  # 美股 ticker 可带点（BRK.B），裸 split 会砍成 BRK
        logger.info(f"正在抓取美股个股新闻: {raw_symbol}")

        end = datetime.now()
        start = end - timedelta(days=days)
        news_list = fetch_company_news(
            raw_symbol,
            date_from=start.strftime("%Y-%m-%d"),
            date_to=end.strftime("%Y-%m-%d"),
        )

        articles: List[Dict] = []
        for item in news_list:
            title = item.get("headline", "").strip()
            if not title:
                continue

            url = item.get("url", "")
            source = item.get("source", "Finnhub")
            article_id = _make_article_id(source, url or title)

            published_at = None
            ts = item.get("datetime")
            if ts:
                try:
                    published_at = datetime.fromtimestamp(ts)
                except (ValueError, OSError):
                    published_at = datetime.now()

            articles.append({
                "article_id": article_id,
                "symbol": raw_symbol,
                "market": "us_stock",
                "title": title,
                "content": item.get("summary", ""),
                "summary": item.get("summary", ""),
                "source": source,
                "url": url,
                "image_url": item.get("image", None),
                "language": "en",
                "published_at": published_at or datetime.now(),
            })

        logger.info(f"美股个股新闻抓取完成 {raw_symbol}: {len(articles)} 条")
        return articles

    # ==================== 全球市场新闻 (Finnhub) ====================

    def fetch_general_news(self) -> List[Dict]:
        """抓取全球市场新闻（Finnhub general news）"""
        if not self._finnhub_key:
            logger.warning("FINNHUB_API_KEY 未配置，跳过全球新闻抓取")
            return []

        logger.info("正在抓取全球市场新闻 (Finnhub)...")

        news_list = _finnhub_general_news()

        articles: List[Dict] = []
        for item in news_list:
            title = item.get("headline", "").strip()
            if not title:
                continue

            url = item.get("url", "")
            source = item.get("source", "Finnhub")
            article_id = _make_article_id(source, url or title)

            published_at = None
            ts = item.get("datetime")
            if ts:
                try:
                    published_at = datetime.fromtimestamp(ts)
                except (ValueError, OSError):
                    published_at = datetime.now()

            articles.append({
                "article_id": article_id,
                "symbol": None,
                "market": "general",
                "title": title,
                "content": item.get("summary", ""),
                "summary": item.get("summary", ""),
                "source": source,
                "url": url,
                "image_url": item.get("image", None),
                "language": "en",
                "published_at": published_at or datetime.now(),
            })

        logger.info(f"全球新闻抓取完成: {len(articles)} 条")
        return articles

    # ==================== 大盘综合新闻（展示态，不入库） ====================
    # 13.4-2 S7b：从 report_engine/web_searcher.py 迁入。这几路产出「展示态」dict
    # （title/body/source/time/category/lang/url），供晨报/新闻子 agent/报告市场纵览
    # 直接消费，**不写库**（写库走 fetch_*+store_articles 那套入库态）。

    def collect_market_news(self, limit: int = 10) -> List[Dict]:
        """大盘综合新闻聚合：东财 A股要闻(350)+全球财经(356)+国际时事(351)+Finnhub 英文，
        按标题前 20 字去重。返回展示态 [{title,body,source,time,category,lang,url}]。"""
        all_news: List[Dict] = []
        all_news += self._fetch_em_column_news("350", "domestic", limit)
        all_news += self._fetch_em_column_news("356", "global", limit)
        all_news += self._fetch_em_column_news("351", "global", limit)
        all_news += self._finnhub_general_display(limit)

        seen: set = set()
        deduped: List[Dict] = []
        for n in all_news:
            key = n["title"][:20]
            if key not in seen:
                seen.add(key)
                deduped.append(n)

        by_cat = {c: sum(1 for n in deduped if n["category"] == c)
                  for c in ("domestic", "global", "finnhub")}
        logger.info(f"大盘综合新闻聚合: 国内{by_cat['domestic']} + 全球{by_cat['global']} "
                    f"+ Finnhub{by_cat['finnhub']} = {len(deduped)} 条")
        return deduped

    def _fetch_em_column_news(self, column: str, category: str, limit: int = 10) -> List[Dict]:
        """东财指定频道新闻（getNewsByColumns，走 net.domestic_json 铁律：快代理→轮换重试，
        绝不静默直连）。column: 350=A股要闻 / 356=全球财经 / 351=国际时事。"""
        import time

        from net import domestic_json

        url = "https://np-listapi.eastmoney.com/comm/web/getNewsByColumns"
        params = {
            "client": "web", "biz": "web_news_col", "column": column,
            "page_size": str(limit), "req_trace": str(int(time.time() * 1000)),
        }
        try:
            data = domestic_json(url, params=params, timeout=15, prefer_direct=True)
            news_list = ((data or {}).get("data") or {}).get("list", [])
        except Exception as e:  # noqa: BLE001 — 单频道失败按空处理，不拖垮其它路
            logger.warning(f"东财新闻 column={column} 失败: {e}")
            return []

        results: List[Dict] = []
        for item in news_list:
            title = item.get("title", "")
            if not title:
                continue
            results.append({
                "title": title,
                "body": (item.get("summary") or item.get("digest") or "")[:200],
                "source": item.get("mediaName") or "",
                "time": item.get("showTime") or "",
                "category": category,
                "lang": "zh",
                "url": item.get("url") or item.get("uniqueUrl") or "",
            })
        logger.info(f"东财新闻 column={column}: {len(results)} 条")
        return results[:limit]

    def _finnhub_general_display(self, limit: int = 10) -> List[Dict]:
        """Finnhub 全球新闻的展示态视图——复用 fetch_general_news（SDK，海外直连安全），
        映射成展示 dict，避免第二份裸 Finnhub 实现。"""
        out: List[Dict] = []
        for a in self.fetch_general_news()[:limit]:
            pub = a.get("published_at")
            out.append({
                "title": a.get("title", ""),
                "body": (a.get("summary") or "")[:300],
                "source": a.get("source") or "",
                "time": pub.strftime("%Y-%m-%d %H:%M") if pub else "",
                "category": "finnhub",
                "lang": "en",
                "url": a.get("url") or "",
            })
        return out

    def search_global_headlines(self, queries: List[str], max_results: int = 5) -> List[Dict]:
        """用真·联网搜索（DuckDuckGo）补充外部头条，产出 category='websearch' 条目，
        与 collect_market_news 同构、可直接并入。失败返回 []，不拖垮其它路。"""
        from acquisition.websearch import web_search

        out: List[Dict] = []
        seen: set = set()
        for q in queries:
            try:
                hits = web_search(q, max_results=max_results)
            except Exception:  # noqa: BLE001
                logger.warning(f"search_global_headlines 搜索失败: {q}")
                continue
            for h in hits:
                key = (h.get("title") or "")[:20]
                if not key or key in seen:
                    continue
                seen.add(key)
                out.append({
                    "title": h.get("title", ""),
                    "body": (h.get("snippet") or h.get("text") or "")[:300],
                    "source": (h.get("source", "") or "").replace("ddg_", "") or "web",
                    "time": "",
                    "category": "websearch",
                    "lang": "en",
                    "url": h.get("url", ""),
                })
        logger.info(f"联网头条补充: {len(out)} 条")
        return out

    # ==================== 去重存库 ====================

    def store_articles(self, articles: List[Dict]) -> "tuple[int, List[str]]":
        """把已抓取的文章列表去重写入数据库。返回 (新增数, 新增 article_id 列表)。

        供 fetch_and_store 的流式路径，以及 news_scheduler 的后台批量抓取路径共用——
        两边都是先拿到 List[Dict]（字段同 NewsArticle 列），再统一走这条入库逻辑。
        """
        if not articles:
            return 0, []

        session = get_session()
        new_ids: List[str] = []
        # fetched_at 由 server_default=func.now() 写入，SQLite 存的是 UTC——去重窗口也必须
        # 用 utcnow()，否则跟本地时间(CST=UTC+8)比会把窗口凭空缩短 8 小时。
        dup_window = datetime.utcnow() - timedelta(hours=48)
        try:
            for art in articles:
                existing = session.query(NewsArticle).filter_by(article_id=art["article_id"]).first()
                if existing:
                    continue
                # 同标题去重（48小时窗口）：不同来源/栏目转载同一条新闻时，article_id 因
                # source/url 不同而不同，但内容就是同一条——只靠 article_id 去重会让列表
                # 刷屏一样的重复标题。48小时够覆盖"同一事件被多家转载"，又不会误伤隔了
                # 一周以上、恰好标题相似的常设栏目新闻（如"一周流动性观察"这类周更标题）。
                title_dup = session.query(NewsArticle).filter(
                    NewsArticle.title == art["title"],
                    NewsArticle.fetched_at >= dup_window,
                ).first()
                if title_dup:
                    continue
                record = NewsArticle(**art)
                session.add(record)
                new_ids.append(art["article_id"])

            session.commit()
            logger.info(f"新闻入库完成: 新增 {len(new_ids)}/{len(articles)} 条")
        except Exception as e:
            session.rollback()
            logger.error(f"新闻入库失败: {e}")
            raise
        finally:
            session.close()

        return len(new_ids), new_ids

    # ==================== 统一入口：抓取 + 去重存库 ====================

    def fetch_and_store(
        self,
        symbol: Optional[str] = None,
        market: str = "a_share",
    ) -> Generator[str, None, None]:
        """
        统一抓取新闻并存入数据库。
        返回 NDJSON 流式进度事件。
        """
        import json

        def ndjson(obj: dict) -> str:
            return json.dumps(obj, ensure_ascii=False) + "\n"

        yield ndjson({"event": "fetching", "message": "正在抓取新闻..."})

        # 根据 market 选择抓取方式
        if market == "a_share" and symbol:
            articles = self.fetch_a_share_news(symbol)
        elif market == "hk_stock" and symbol:
            articles = self.fetch_hk_stock_news(symbol)
        elif market == "us_stock" and symbol:
            articles = self.fetch_us_stock_news(symbol)
        elif market == "general":
            articles = self.fetch_general_news()
        else:
            yield ndjson({"event": "error", "message": f"不支持的市场类型: {market}"})
            return

        if not articles:
            yield ndjson({"event": "complete", "message": "未获取到新闻", "total": 0, "new_count": 0})
            return

        yield ndjson({"event": "fetching", "message": f"获取到 {len(articles)} 条新闻，正在入库..."})

        try:
            new_count, _ = self.store_articles(articles)
        except Exception as e:
            yield ndjson({"event": "error", "message": f"入库失败: {e}"})
            return

        yield ndjson({
            "event": "fetched",
            "message": f"新闻抓取完成: 新增 {new_count} 条",
            "total": len(articles),
            "new_count": new_count,
        })

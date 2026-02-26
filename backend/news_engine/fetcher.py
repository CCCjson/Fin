"""
新闻抓取器 — AkShare (A股) + Finnhub (全球市场)
"""
import hashlib
import os
from datetime import datetime
from typing import Dict, Generator, List, Optional

import finnhub
from dotenv import load_dotenv
from loguru import logger
from sqlalchemy.dialects.sqlite import insert as sqlite_insert

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

    # ==================== A 股个股新闻 ====================

    def fetch_a_share_news(self, symbol: str) -> List[Dict]:
        """
        抓取 A 股个股新闻（AkShare: stock_news_em）。
        symbol: 纯数字代码，如 '300059'
        """
        import akshare as ak

        raw_symbol = symbol.split(".")[0]
        logger.info(f"正在抓取 A 股新闻: {raw_symbol}")

        try:
            df = ak.stock_news_em(symbol=raw_symbol)
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
                "market": "a_share",
                "title": title,
                "content": content,
                "summary": content[:200] if content else title,
                "source": source,
                "url": url,
                "image_url": None,
                "language": "zh",
                "published_at": published_at or datetime.now(),
            })

        logger.info(f"A 股新闻抓取完成: {len(articles)} 条")
        return articles

    # ==================== 全球市场新闻 (Finnhub) ====================

    def fetch_general_news(self) -> List[Dict]:
        """抓取全球市场新闻（Finnhub general news）"""
        if not self._finnhub_key:
            logger.warning("FINNHUB_API_KEY 未配置，跳过全球新闻抓取")
            return []

        logger.info("正在抓取全球市场新闻 (Finnhub)...")

        try:
            client = finnhub.Client(api_key=self._finnhub_key)
            news_list = client.general_news("general", min_id=0)
        except Exception as e:
            logger.error(f"Finnhub 新闻抓取失败: {e}")
            return []

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
        elif market == "general":
            articles = self.fetch_general_news()
        else:
            yield ndjson({"event": "error", "message": f"不支持的市场类型: {market}"})
            return

        if not articles:
            yield ndjson({"event": "complete", "message": "未获取到新闻", "total": 0, "new_count": 0})
            return

        yield ndjson({"event": "fetching", "message": f"获取到 {len(articles)} 条新闻，正在入库..."})

        # 去重写入数据库
        session = get_session()
        new_count = 0
        try:
            for art in articles:
                existing = session.query(NewsArticle).filter_by(article_id=art["article_id"]).first()
                if existing:
                    continue
                record = NewsArticle(**art)
                session.add(record)
                new_count += 1

            session.commit()
            logger.info(f"新闻入库完成: 新增 {new_count}/{len(articles)} 条")
        except Exception as e:
            session.rollback()
            logger.error(f"新闻入库失败: {e}")
            yield ndjson({"event": "error", "message": f"入库失败: {e}"})
            return
        finally:
            session.close()

        yield ndjson({
            "event": "fetched",
            "message": f"新闻抓取完成: 新增 {new_count} 条",
            "total": len(articles),
            "new_count": new_count,
        })

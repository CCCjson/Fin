"""
NewsScheduler —— 新闻模块的定时抓取+分析常驻任务（Newnew 用的数据底座）。

复刻 data_engine/daily_pipeline_scheduler.py 的 AsyncIOScheduler 单例骨架，注册两个 job：
    Job A（news_fetch_job，默认每 15 分钟）：抓国内外综合新闻 + 自选股/持仓个股新闻
        → 去重入库(NewsArticle) → 本地 BERT 情感打分(NewsSentiment)
        → 有新文章才调 LLM 生成结论(NewsAnalysis，NewsAnalyzer 已自动持久化)
        → 命中高影响关键词/负面情绪堆积时发 business_events + macOS 通知
    Job B（news_cache_clear_job，默认每周一 04:00）：清空内存去重前置缓存 _seen_ids
        （纯性能优化，DB 里 article_id 唯一约束才是去重真正的保障，历史数据永不删除）

两步都是**同步阻塞**（抓取/LLM 调用），统一丢线程池执行，不阻塞事件循环
（仿 daily_pipeline_scheduler 与 automation/price_alert_monitor）。

门控：环境变量 NEWS_AUTO_FETCH_ENABLED（默认 true）。
"""
import asyncio
import json
import os
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional

from common.market import to_bare_code
from loguru import logger

JOB_FETCH_ID = "news_fetch_job"
JOB_CACHE_CLEAR_ID = "news_cache_clear_job"

# 高影响关键词配置：分类 JSON，不塞 .env（词一多 .env 就臃肿）。跟
# knowledge_engine/config.py::get_scrapers_config_dir() 同一套"相对 backend 根目录"路径惯例。
_KEYWORDS_CONFIG_PATH = Path(__file__).resolve().parent.parent / "configs" / "news_high_impact_keywords.json"

_CATEGORY_LABEL = {
    "financial_risk": "财务风险",
    "urgent": "突发",
    "geopolitical": "地缘政治",
}

# DuckDuckGo 联网头条补充的默认查询词，跟 /news/morning-briefing 的默认值保持一致
# （api/routes/news.py），用户没传 queries 时两条路径行为一致。env 关不掉查询词，只能整体
# 关掉这一路（NEWS_ENABLE_WEBSEARCH=false）——DDG 限速/被软封时的应急开关。
_WEBSEARCH_QUERIES = ["global financial markets today", "A股 市场 隔夜 外盘"]

# macOS 通知横幅 body 大概两行，中文按每行~20字估算——截断点系统说了算、不好看，
# 干脆代码里先截好，只留"够用来判断要不要点开细看"的信息量，完整原文进 DB/面板。
NOTIFY_MESSAGE_LIMIT = 40


def _env_bool(key: str, default: bool) -> bool:
    val = os.getenv(key)
    if val is None:
        return default
    return val.strip().lower() in ("1", "true", "yes", "on")


def _truncate(text: str, limit: int) -> str:
    text = (text or "").strip()
    return text if len(text) <= limit else text[:limit].rstrip() + "…"


def _load_high_impact_keywords() -> Dict[str, List[str]]:
    """读分类关键词 JSON。不做进程内缓存——文件很小，15分钟一次的读盘开销可忽略，
    换来 Jason 改词表立即生效、不用重启后端。文件缺失/损坏时降级为空（不中断主流程）。"""
    try:
        with open(_KEYWORDS_CONFIG_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
        return {cat: [str(k).strip() for k in words if str(k).strip()]
                for cat, words in data.items()}
    except Exception as e:  # noqa: BLE001
        logger.warning(f"高影响关键词配置读取失败（跳过关键词判定）: {e}")
        return {}


def _match_keyword(title: str, keywords_by_category: Dict[str, List[str]]) -> Optional[tuple]:
    """标题是否命中任意类别的关键词，命中返回 (category, matched_keyword)，否则 None。"""
    for category, words in keywords_by_category.items():
        for w in words:
            if w and w in title:
                return category, w
    return None


def _infer_market(symbol: str) -> str:
    """symbol 后缀推断市场（委托 common.market 单一真源）。"""
    from common.market import infer_market_from_symbol
    return infer_market_from_symbol(symbol)


class NewsScheduler:
    """新闻抓取+分析定时任务（AsyncIOScheduler 单例）。"""

    def __init__(self):
        self._scheduler = None
        self._running = False
        self._enabled = _env_bool("NEWS_AUTO_FETCH_ENABLED", True)
        self._is_updating = False  # 防重入
        self._last_run: Optional[Dict] = None
        self._seen_ids: set = set()  # 内存去重前置缓存，weekly job 清空

    # ---------- 状态 ----------

    @property
    def is_running(self) -> bool:
        return self._running

    @property
    def enabled(self) -> bool:
        return self._enabled

    def _interval_minutes(self) -> int:
        return int(os.getenv("NEWS_FETCH_INTERVAL_MINUTES", "15"))

    def _cache_clear_cron(self) -> str:
        return os.getenv("NEWS_CACHE_CLEAR_CRON", "0 4 * * 1").strip()

    def get_status(self) -> Dict:
        jobs = self.get_jobs_info()
        next_run = next((j["next_run"] for j in jobs if j["id"] == JOB_FETCH_ID), None)
        return {
            "running": self._running,
            "enabled": self._enabled,
            "is_updating": self._is_updating,
            "interval_minutes": self._interval_minutes(),
            "cache_clear_cron": self._cache_clear_cron(),
            "next_run": next_run,
            "jobs": jobs,
            "last_run": self._last_run,
            "cache_size": len(self._seen_ids),
        }

    def get_jobs_info(self) -> List[Dict]:
        if not self._scheduler:
            return []
        return [{"id": j.id, "name": j.name,
                 "next_run": str(j.next_run_time) if j.next_run_time else None}
                for j in self._scheduler.get_jobs()]

    # ---------- 生命周期 ----------

    def _get_scheduler(self):
        if self._scheduler is None:
            from apscheduler.schedulers.asyncio import AsyncIOScheduler
            import pytz
            self._scheduler = AsyncIOScheduler(timezone=pytz.timezone("Asia/Shanghai"))
        return self._scheduler

    def start(self):
        if self._running:
            return
        scheduler = self._get_scheduler()
        if self._enabled:
            self._add_jobs(scheduler)
        scheduler.start()
        self._running = True
        logger.info(
            f"新闻定时任务已启动（enabled={self._enabled}，"
            f"interval={self._interval_minutes()}min，cache_clear={self._cache_clear_cron()}）"
        )

    def stop(self):
        if not self._running:
            return
        if self._scheduler:
            self._scheduler.shutdown(wait=False)
            self._scheduler = None
        self._running = False
        logger.info("新闻定时任务已停止")

    def _add_jobs(self, scheduler):
        from apscheduler.triggers.interval import IntervalTrigger
        from apscheduler.triggers.cron import CronTrigger

        scheduler.add_job(
            self._fetch_job, IntervalTrigger(minutes=self._interval_minutes()),
            id=JOB_FETCH_ID, name="新闻抓取+分析", replace_existing=True,
            misfire_grace_time=300, coalesce=True,
        )
        scheduler.add_job(
            self._cache_clear_job, CronTrigger.from_crontab(self._cache_clear_cron()),
            id=JOB_CACHE_CLEAR_ID, name="新闻去重缓存清理", replace_existing=True,
            misfire_grace_time=3600, coalesce=True,
        )

    def set_enabled(self, enabled: bool) -> Dict:
        self._enabled = enabled
        if self._running and self._scheduler:
            if enabled:
                self._add_jobs(self._scheduler)
                logger.info("新闻定时任务已开启")
            else:
                for job_id in (JOB_FETCH_ID, JOB_CACHE_CLEAR_ID):
                    try:
                        self._scheduler.remove_job(job_id)
                    except Exception:  # noqa: BLE001 — job 不存在无所谓
                        pass
                logger.info("新闻定时任务已关闭")
        return self.get_status()

    # ---------- Job B：清缓存 ----------

    async def _cache_clear_job(self):
        n = len(self._seen_ids)
        self._seen_ids.clear()
        logger.info(f"新闻去重前置缓存已清空（清理前 {n} 条，DB 历史数据不受影响）")

    # ---------- Job A：抓取 + 分析 ----------

    async def _fetch_job(self):
        if self._is_updating:
            logger.warning("上一轮新闻抓取尚未结束，跳过本次定时触发")
            return
        self._is_updating = True
        loop = asyncio.get_event_loop()
        try:
            result = await loop.run_in_executor(None, self._run_fetch_sync)
            self._last_run = result
            logger.info(f"新闻抓取链完成: {result}")
        except Exception as e:  # noqa: BLE001 — 定时任务绝不抛出
            logger.exception(f"新闻抓取链异常: {e}")
            self._last_run = {"ok": False, "error": str(e)}
        finally:
            self._is_updating = False

    def run_once_sync(self) -> Dict:
        """供 /news/job/run-once 手动触发，同步跑一轮（debug 用）。"""
        result = self._run_fetch_sync()
        self._last_run = result
        return result

    def _run_fetch_sync(self) -> Dict:
        from news_engine.fetcher import NewsFetcher
        from news_engine.sentiment import SentimentAnalyzer

        summary: Dict = {
            "ok": True, "started_at": datetime.now().isoformat(),
            "new_general": 0, "new_symbol": {}, "high_impact": [],
        }
        fetcher = NewsFetcher()
        all_new_ids: List[str] = []
        # symbol -> (market, [new article dicts])，供后面高影响检测复用
        symbol_new_articles: Dict[str, tuple] = {}
        # 综合桶本轮新增的文章（地缘政治新闻的主要入口），同样供高影响检测复用
        general_new_articles: List[Dict] = []

        # ---- 1) 综合国内外新闻 ----
        try:
            general_articles = self._fetch_general_market_news()
            general_articles = [a for a in general_articles if a["article_id"] not in self._seen_ids]
            new_count, new_ids = fetcher.store_articles(general_articles)
            for a in general_articles:
                self._seen_ids.add(a["article_id"])
            summary["new_general"] = new_count
            all_new_ids.extend(new_ids)
            general_new_articles = [a for a in general_articles if a["article_id"] in new_ids]
        except Exception as e:  # noqa: BLE001 — 单步失败不中断整条链
            logger.warning(f"[新闻链] 综合新闻抓取失败: {e}")
            summary["ok"] = False
            summary["general_error"] = str(e)

        # ---- 2) 自选股 + 持仓个股新闻 ----
        try:
            symbols = self._watchlist_and_position_symbols()
        except Exception as e:  # noqa: BLE001
            logger.warning(f"[新闻链] 获取自选股/持仓列表失败: {e}")
            symbols = []

        for symbol in symbols:
            market = _infer_market(symbol)
            try:
                raw_symbol = to_bare_code(symbol)
                if market == "a_share":
                    articles = fetcher.fetch_a_share_news(raw_symbol)
                elif market == "hk_stock":
                    articles = fetcher.fetch_hk_stock_news(symbol)
                else:
                    articles = fetcher.fetch_us_stock_news(raw_symbol)
            except Exception as e:  # noqa: BLE001
                logger.warning(f"[新闻链] {symbol} 新闻抓取失败: {e}")
                continue

            articles = [a for a in articles if a["article_id"] not in self._seen_ids]
            if not articles:
                continue
            try:
                new_count, new_ids = fetcher.store_articles(articles)
            except Exception as e:  # noqa: BLE001
                logger.warning(f"[新闻链] {symbol} 新闻入库失败: {e}")
                continue
            for a in articles:
                self._seen_ids.add(a["article_id"])
            if new_count:
                summary["new_symbol"][symbol] = new_count
                all_new_ids.extend(new_ids)
                new_articles_only = [a for a in articles if a["article_id"] in new_ids]
                symbol_new_articles[symbol] = (market, new_articles_only)

        # ---- 3) 本地 BERT 情感打分（免费，只打新增的）----
        if all_new_ids:
            try:
                SentimentAnalyzer.get_instance().analyze_batch(all_new_ids)
            except Exception as e:  # noqa: BLE001
                logger.warning(f"[新闻链] 情感打分失败: {e}")

        # ---- 4) LLM 结论（只在有新文章时才调用，控制成本）----
        if summary["new_general"] > 0:
            try:
                self._generate_conclusion(symbol=None, market="general")
            except Exception as e:  # noqa: BLE001
                logger.warning(f"[新闻链] 综合结论生成失败: {e}")
        for symbol, count in summary["new_symbol"].items():
            try:
                self._generate_conclusion(symbol=symbol, market=_infer_market(symbol))
            except Exception as e:  # noqa: BLE001
                logger.warning(f"[新闻链] {symbol} 结论生成失败: {e}")

        # ---- 5) 高影响预警（关键词命中 或 同 symbol 新增负面≥2）----
        try:
            high_impact = self._detect_high_impact(symbol_new_articles, general_new_articles)
            summary["high_impact"] = high_impact
            for hit in high_impact:
                self._raise_high_impact(hit)
        except Exception as e:  # noqa: BLE001
            logger.warning(f"[新闻链] 高影响检测失败: {e}")

        summary["completed_at"] = datetime.now().isoformat()
        return summary

    # ---------- 子步骤 ----------

    def _fetch_general_market_news(self) -> List[Dict]:
        """综合国内外新闻：NewsFetcher.collect_market_news 三路聚合（东财A股要闻+全球财经+Finnhub英文
        新闻）+ 第四路 DuckDuckGo 真联网搜索补充头条，映射成 NewsArticle 字段。
        三路聚合与 NewsSubagent 无 symbol 分支同源；第四路查询词跟 /news/morning-briefing
        默认查询词保持一致（api/routes/news.py），独立 try——DDG 失败/限速不拖垮前三路。"""
        import hashlib

        from news_engine.fetcher import NewsFetcher

        fetcher = NewsFetcher()
        try:
            raw_news = fetcher.collect_market_news()
        except Exception as e:  # noqa: BLE001 — 聚合失败按"没抓到"处理
            logger.warning(f"综合新闻聚合抓取失败: {e}")
            raw_news = []

        if _env_bool("NEWS_ENABLE_WEBSEARCH", True):
            try:
                web_news = fetcher.search_global_headlines(_WEBSEARCH_QUERIES)
                raw_news = raw_news + web_news
            except Exception as e:  # noqa: BLE001 — DDG 限速/失败不影响前三路
                logger.warning(f"联网头条补充失败，跳过: {e}")

        if not raw_news:
            return []

        articles: List[Dict] = []
        for n in raw_news:
            title = (n.get("title") or "").strip()
            if not title:
                continue
            source = n.get("source") or "综合"
            url = n.get("url") or ""
            body = n.get("body") or ""
            article_id = hashlib.md5(f"{source}:{url or title}".encode()).hexdigest()
            articles.append({
                "article_id": article_id,
                "symbol": None,
                "market": "general",
                "title": title,
                "content": body,
                "summary": body[:200] if body else title,
                "source": source,
                "url": url,
                "image_url": None,
                "language": n.get("lang") or "zh",
                # 三路来源的 time 字段各自格式不一（showTime/date/空字符串），不逐一解析，
                # 用抓取时刻做 published_at——对"最近有什么新闻"这个用途够用。
                "published_at": datetime.now(),
            })
        return articles

    def _watchlist_and_position_symbols(self) -> List[str]:
        from data_engine.storage.database import get_session
        from data_engine.storage.repository import WatchlistRepository

        session = get_session()
        try:
            symbols = {w.symbol for w in WatchlistRepository(session).list_all()}
        finally:
            session.close()

        try:
            from portfolio.calculator import PortfolioCalculator
            positions = PortfolioCalculator().get_current_positions()
            symbols |= {p["symbol"] for p in positions if p.get("quantity", 0) > 0}
        except Exception as e:  # noqa: BLE001
            logger.debug(f"读取持仓列表失败（不影响自选股部分）: {e}")

        return sorted(symbols)

    def _generate_conclusion(self, symbol: Optional[str], market: str) -> None:
        """drain NewsAnalyzer 的流式结论生成器；结论已由该方法自动持久化到 NewsAnalysis。"""
        from news_engine.analyzer import NewsAnalyzer
        for _ in NewsAnalyzer().generate_report_stream(symbol=symbol, market=market):
            pass

    def _detect_high_impact(
        self,
        symbol_new_articles: Dict[str, tuple],
        general_new_articles: List[Dict],
    ) -> List[Dict]:
        """v1 启发式：标题命中关键词（财务风险/突发/地缘政治三类，配置见
        configs/news_high_impact_keywords.json），或同 symbol 本轮新增负面情感 ≥2 条。

        地缘政治新闻走的是综合桶（market=general，无 symbol）——这类新闻有时候影响力
        比个股财经消息还大，所以关键词检测同样要扫描综合桶，不能只盯着自选股/持仓。
        情感计数规则只对有 symbol 的个股新闻有意义，不套用到综合桶。
        不做 LLM 语义判断——避免解析 markdown 结论的脆弱性，省钱、可预测。"""
        from data_engine.storage.database import get_session
        from data_engine.storage.models import NewsSentiment

        keywords_by_category = _load_high_impact_keywords()
        hits: List[Dict] = []

        # ---- 综合新闻桶：只做关键词检测（地缘政治的主要入口，没有 symbol 概念）----
        for article in general_new_articles:
            matched = _match_keyword(article.get("title") or "", keywords_by_category)
            if matched:
                category, _kw = matched
                hits.append({
                    "symbol": None, "category": category, "reason": "keyword",
                    "title": article["title"], "url": article.get("url") or "",
                })

        # ---- 个股新闻：关键词优先，其次负面情感堆积 ----
        for symbol, (market, articles) in symbol_new_articles.items():
            keyword_hit = None
            matched_category = None
            for a in articles:
                matched = _match_keyword(a.get("title") or "", keywords_by_category)
                if matched:
                    keyword_hit = a
                    matched_category = matched[0]
                    break

            if keyword_hit:
                hits.append({
                    "symbol": symbol, "category": matched_category, "reason": "keyword",
                    "title": keyword_hit["title"],
                    "url": keyword_hit.get("url") or "",
                })
                continue

            ids = [a["article_id"] for a in articles]
            if not ids:
                continue
            session = get_session()
            try:
                neg_count = (
                    session.query(NewsSentiment)
                    .filter(NewsSentiment.article_id.in_(ids), NewsSentiment.sentiment == "negative")
                    .count()
                )
            finally:
                session.close()
            if neg_count >= 2:
                hits.append({
                    "symbol": symbol, "category": None, "reason": "negative_sentiment",
                    "negative_count": neg_count,
                    "title": articles[0]["title"],
                    "url": articles[0].get("url") or "",
                })

        return hits

    def _raise_high_impact(self, hit: Dict) -> None:
        from notify.mac_notify import notify

        symbol = hit.get("symbol")
        title = hit.get("title", "")
        # symbol=None 时是综合桶命中（地缘政治等，没有具体股票），用类别中文标签兜底，
        # 不然 f-string 会显示成难看的 "None"。
        label = symbol or _CATEGORY_LABEL.get(hit.get("category"), "综合新闻")
        if hit["reason"] == "keyword":
            reason_text = "命中预警关键词"
        else:
            reason_text = f"新增{hit.get('negative_count')}条负面新闻"
        # 完整版：进 business_events / DataMonitor 面板，不截断，Jason 想细看随时能看全文。
        full_message = f"{label} {reason_text}：{title}"

        try:
            from business_events import publish_event, NEWS_HIGH_IMPACT
            publish_event(
                NEWS_HIGH_IMPACT, source="news_scheduler", severity="warn",
                symbol=symbol, title=f"新闻高影响预警：{label}", message=full_message,
                reason=hit["reason"],
            )
        except Exception:  # noqa: BLE001 — 事件总线失败不影响通知本身
            pass

        # 通知版：macOS 横幅装不下长标题，代码里主动截断（比让系统硬切体面），
        # label 放 subtitle（固定短字段，不会被切；有 symbol 用 symbol，综合桶用类别标签），
        # message 只放"原因+精简标题"。
        # open_url：优先直接跳新闻原文链接（Jason 要看的是具体新闻内容，不是汇总面板）；
        # 源没带 url（个别数据源确实会缺）才退回 DataMonitor 的 Newnew 面板兜底。
        # 装了 terminal-notifier 才会真的跳转，没装则退化成纯展示。
        notify_message = _truncate(f"{reason_text}：{title}", NOTIFY_MESSAGE_LIMIT)
        fallback_url = os.getenv("NEWS_NOTIFY_OPEN_URL", "http://127.0.0.1:5174/app/data-monitor")
        open_url = hit.get("url") or fallback_url
        notify(title="Newnew 新闻预警", subtitle=label, message=notify_message, open_url=open_url)


# 模块级单例
news_scheduler = NewsScheduler()

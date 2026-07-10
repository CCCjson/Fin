"""
Web搜索器 — 当数据库数据不足时，从网上搜索最新市场信息
使用 ProxyManager 获取代理IP，直接调用 eastmoney API 拉取数据
"""
import time
from datetime import date
from typing import Dict, List, Optional
from loguru import logger

MAX_RETRIES = 3


class MarketWebSearcher:
    """搜索最新市场资讯，补充数据库缺失的信息"""

    def __init__(self, random_ip: bool = False):
        """
        Args:
            random_ip: True = 少试几轮（直连 + 一轮代理）。历史上它的含义是
                「每次请求都换新 IP」，那会让 advisor 每分析一只票就烧掉一串 IP。
        """
        self._proxy_manager = None
        self._random_ip = random_ip
        self._ensure_proxy_manager()

    def _ensure_proxy_manager(self):
        """拿进程内单例。自建 `ProxyManager()` 会多出一份谁也看不见的 IP 缓存。"""
        try:
            from net import get_proxy_manager
            if self._proxy_manager is None:
                self._proxy_manager = get_proxy_manager()
        except Exception as e:
            logger.warning(f"初始化 ProxyManager 失败: {e}")

    def _get_proxies(self) -> Optional[Dict[str, str]]:
        """复用当前 IP（没过期就不扣额度）。"""
        if not self._proxy_manager:
            return None
        try:
            proxy = self._proxy_manager.get_proxy()
            if proxy:
                proxies = proxy.to_requests_proxies()
                logger.info(f"使用代理IP: {proxy.ip}:{proxy.port}")
                return proxies
        except Exception as e:
            logger.warning(f"获取代理IP失败: {e}")
        return None

    def _switch_proxy(self):
        """换一个新 IP（扣额度）。只该在请求真的失败后调。"""
        if self._proxy_manager:
            self._proxy_manager.switch_proxy()

    def search_market_overview(self) -> Dict:
        """
        搜索最新A股市场总览信息

        Returns:
            {
                "indices": [...],
                "sectors": [...],
                "concepts": [...],
                "money_flow": [...],
                "market_breadth": {...},
                "news": [...],
                "source": "web_search",
            }
        """
        result: Dict = {
            "indices": [],
            "index_klines": [],
            "sectors": [],
            "concepts": [],
            "money_flow": [],
            "northbound": {},
            "news": [],
            "source": "web_search",
        }

        # 1. 实时指数（带重试）
        result["indices"] = self._fetch_with_retry(self._fetch_realtime_indices)

        # 2. 指数近10日K线走势（带重试）
        result["index_klines"] = self._fetch_with_retry(self._fetch_index_klines)

        # 3. 行业板块涨跌（带重试）
        result["sectors"] = self._fetch_with_retry(self._fetch_sector_ranking)

        # 4. 概念板块热点（带重试）
        result["concepts"] = self._fetch_with_retry(self._fetch_concept_boards)

        # 5. 主力资金流向（带重试）
        result["money_flow"] = self._fetch_with_retry(self._fetch_money_flow)

        # 6. 全市场涨跌统计 — 已改由 data_collector._collect_market_from_db() 从数据库获取
        # （不再从 EastMoney API 分页拉取，避免分页上限导致数据不全）

        # 7. 北向资金数据（带重试）
        northbound = self._fetch_with_retry(self._fetch_northbound_realtime)
        northbound_hist = self._fetch_with_retry(self._fetch_northbound_history)
        northbound_stocks = self._fetch_with_retry(self._fetch_northbound_top_stocks)
        nb: Dict = {}
        if isinstance(northbound, dict):
            nb = northbound
        elif isinstance(northbound, list) and northbound:
            nb = northbound[0] if isinstance(northbound[0], dict) else {}
        nb["history"] = northbound_hist if isinstance(northbound_hist, list) else []
        nb["top_stocks"] = northbound_stocks if isinstance(northbound_stocks, list) else []
        result["northbound"] = nb

        # 8. 多源新闻聚合（国内+全球+Finnhub）
        result["news"] = self._fetch_with_retry(self._collect_all_news)

        return result

    def _fetch_with_retry(self, fetch_fn) -> list | dict:
        """带代理重试的通用包装

        统一节奏：第 0 轮直连 → 第 1 轮复用当前 IP → 之后每轮换新 IP。
        random_ip 只决定总轮数（2 轮 vs MAX_RETRIES+1 轮）。
        """
        # 第 0 轮一律本地直连（新闻是低频接口，先省额度）；直连不通才上快代理。
        # random_ip 模式只影响「之后还试几轮代理」，不再每次请求都无脑买新 IP
        # —— 那是 advisor 每分析一只票就烧掉一串 IP 的原因。
        rounds = 2 if self._random_ip else MAX_RETRIES + 1

        for attempt in range(rounds):
            if attempt == 0:
                proxies = None                      # 直连
            elif attempt == 1:
                proxies = self._get_proxies()       # 复用当前 IP，没过期不扣额度
            else:
                self._switch_proxy()                # 上一轮的 IP 不行，换一个
                proxies = self._get_proxies()
            try:
                data = fetch_fn(proxies)
                if data:
                    return data
            except Exception as e:
                label = "直连" if attempt == 0 else f"代理第{attempt}轮"
                logger.warning(f"{fetch_fn.__name__} {label}失败: {e}")
        return []

    # ------------------------------------------------------------------
    # 已有数据源
    # ------------------------------------------------------------------

    def _fetch_realtime_indices(self, proxies: Optional[Dict]) -> List[Dict]:
        """直接调用 eastmoney API + 代理获取6大指数实时行情"""
        import requests

        url = "https://push2.eastmoney.com/api/qt/ulist.np/get"
        params = {
            "fltt": "2",
            "invt": "2",
            "fields": "f2,f3,f4,f6,f12,f14",
            # 上证指数、深证成指、创业板指、沪深300、中证500、科创50
            "secids": "1.000001,0.399001,0.399006,1.000300,1.000905,1.000688",
        }

        resp = requests.get(url, params=params, proxies=proxies, timeout=15)
        diff = resp.json().get("data", {}).get("diff", [])
        if not diff:
            return []

        indices = []
        for item in diff:
            indices.append({
                "name": item.get("f14", ""),
                "price": item.get("f2"),
                "change_pct": item.get("f3"),
                "change_amount": item.get("f4"),
                "amount": item.get("f6"),
            })

        logger.info(f"获取实时指数成功: {len(indices)} 个")
        return indices

    def _fetch_index_klines(self, proxies: Optional[Dict]) -> List[Dict]:
        """
        获取6大指数近10日K线数据

        复用 eastmoney_crawler 的 API 端点和 parse_kline_data 解析逻辑，
        直接构建 secid 参数绕过 code→market 的映射问题。
        """
        import sys
        from pathlib import Path

        # 导入 parse_kline_data
        scripts_dir = str(Path(__file__).resolve().parent.parent / "scripts")
        if scripts_dir not in sys.path:
            sys.path.insert(0, scripts_dir)
        from eastmoney_crawler import EastMoneyCrawler, CrawlerConfig, parse_kline_data

        INDEX_SECIDS = {
            "上证指数": "1.000001",
            "深证成指": "0.399001",
            "创业板指": "0.399006",
            "沪深300": "1.000300",
            "中证500": "1.000905",
            "科创50": "1.000688",
        }

        # 创建轻量爬虫实例（只6次请求，配置宽松）
        crawler = EastMoneyCrawler(CrawlerConfig(
            min_delay=0.3,
            max_delay=1.0,
            max_retries=1,
            timeout=15,
        ))

        results = []
        consecutive_failures = 0
        for name, secid in INDEX_SECIDS.items():
            params = {
                "fields1": "f1,f2,f3,f4,f5,f6",
                "fields2": "f51,f52,f53,f54,f55,f56,f57,f58,f59,f60,f61",
                "ut": "7eea3edcaed734bea9cbfc24409ed989",
                "klt": "101",       # 日线
                "fqt": "1",         # 前复权
                "secid": secid,
                "lmt": "10",        # 最近10天
                "end": "20500101",  # 远期截止 → API 自动返回到最新
                "_": str(int(time.time() * 1000)),
            }

            try:
                crawler.rate_limiter.wait()
                headers = crawler._get_request_headers()
                resp = crawler.session.get(
                    crawler.HIST_API,
                    params=params,
                    headers=headers,
                    timeout=15,
                    proxies=proxies,
                )

                data = resp.json().get("data")
                if not data:
                    logger.warning(f"指数K线 {name} 无数据")
                    consecutive_failures += 1
                    if consecutive_failures >= 2:
                        raise ConnectionError(f"连续{consecutive_failures}个指数失败，代理可能已失效")
                    continue

                consecutive_failures = 0  # 成功则重置
                klines = parse_kline_data(data)
                if not klines:
                    continue

                # 计算汇总统计
                closes = [k["close"] for k in klines]
                highs = [k["high"] for k in klines]
                lows = [k["low"] for k in klines]
                amounts = [k["amount"] for k in klines]

                # 5日均量（取最近5条）
                recent_amounts = amounts[-5:] if len(amounts) >= 5 else amounts
                avg_amount_5d = sum(recent_amounts) / len(recent_amounts) if recent_amounts else 0

                results.append({
                    "name": name,
                    "secid": secid,
                    "klines": klines,
                    "stats": {
                        "high_10d": max(highs) if highs else None,
                        "low_10d": min(lows) if lows else None,
                        "avg_close_10d": round(sum(closes) / len(closes), 2) if closes else None,
                        "avg_amount_5d": avg_amount_5d,
                        "latest_amount": amounts[-1] if amounts else None,
                        "volume_vs_avg": round(amounts[-1] / avg_amount_5d * 100, 1) if avg_amount_5d and amounts else None,
                    },
                })

                logger.debug(f"指数K线 {name}: {len(klines)} 日")
            except Exception as e:
                logger.warning(f"获取指数K线 {name} 失败: {e}")
                # 连接类错误（超时/代理失效）直接抛到外层换代理，不要在坏IP上继续等
                if "timeout" in str(e).lower() or "proxy" in str(e).lower() or \
                   "connect" in str(e).lower() or "ssl" in str(e).lower() or \
                   isinstance(e, (ConnectionError, OSError)):
                    raise
                continue

        logger.info(f"获取指数K线成功: {len(results)} 个指数")
        return results

    def _fetch_sector_ranking(self, proxies: Optional[Dict]) -> List[Dict]:
        """直接调用 eastmoney API + 代理获取行业板块涨跌排行"""
        import requests

        url = "https://push2.eastmoney.com/api/qt/clist/get"
        params = {
            "pn": "1", "pz": "100", "po": "1", "np": "1",
            "ut": "bd1d9ddb04089700cf9c27f6f7426281",
            "fltt": "2", "invt": "2",
            "fid": "f3",
            "fs": "m:90+t:2+f:!50",
            "fields": "f3,f14,f128,f136",
        }

        resp = requests.get(url, params=params, proxies=proxies, timeout=15)
        diff = resp.json().get("data", {}).get("diff", [])
        if not diff:
            return []

        sectors = []
        top_n, bottom_n = 5, 5
        # 防止 top 和 bottom 重叠
        bottom_start = max(top_n, len(diff) - bottom_n)

        for item in diff[:top_n]:
            sectors.append({
                "name": item.get("f14", ""),
                "change_pct": item.get("f3", 0),
                "leader": item.get("f128", ""),
                "leader_pct": item.get("f136", 0),
                "rank": "top",
            })

        for item in diff[bottom_start:]:
            sectors.append({
                "name": item.get("f14", ""),
                "change_pct": item.get("f3", 0),
                "leader": item.get("f128", ""),
                "leader_pct": item.get("f136", 0),
                "rank": "bottom",
            })

        logger.info(f"获取板块排行成功: {len(sectors)} 个")
        return sectors

    # ------------------------------------------------------------------
    # 新增数据源
    # ------------------------------------------------------------------

    def _fetch_concept_boards(self, proxies: Optional[Dict]) -> List[Dict]:
        """获取概念板块热点排行（t:3 = 概念板块）"""
        import requests

        url = "https://push2.eastmoney.com/api/qt/clist/get"
        params = {
            "pn": "1", "pz": "100", "po": "1", "np": "1",
            "ut": "bd1d9ddb04089700cf9c27f6f7426281",
            "fltt": "2", "invt": "2",
            "fid": "f3",
            "fs": "m:90+t:3+f:!50",
            "fields": "f3,f14,f104,f105,f128,f136",
        }

        resp = requests.get(url, params=params, proxies=proxies, timeout=15)
        diff = resp.json().get("data", {}).get("diff", [])
        if not diff:
            return []

        concepts = []
        top_n, bottom_n = 10, 5
        # 防止 top 和 bottom 重叠
        bottom_start = max(top_n, len(diff) - bottom_n)

        # 涨幅前 10
        for item in diff[:top_n]:
            concepts.append({
                "name": item.get("f14", ""),
                "change_pct": item.get("f3", 0),
                "up_count": item.get("f104", 0),
                "down_count": item.get("f105", 0),
                "leader": item.get("f128", ""),
                "leader_pct": item.get("f136", 0),
                "rank": "top",
            })

        # 跌幅后 5
        for item in diff[bottom_start:]:
            concepts.append({
                "name": item.get("f14", ""),
                "change_pct": item.get("f3", 0),
                "up_count": item.get("f104", 0),
                "down_count": item.get("f105", 0),
                "leader": item.get("f128", ""),
                "leader_pct": item.get("f136", 0),
                "rank": "bottom",
            })

        logger.info(f"获取概念板块排行成功: {len(concepts)} 个")
        return concepts

    def _fetch_money_flow(self, proxies: Optional[Dict]) -> List[Dict]:
        """获取主力资金流向（按 f62 主力净流入排序）"""
        import requests

        url = "https://push2.eastmoney.com/api/qt/clist/get"
        params = {
            "pn": "1", "pz": "100", "po": "1", "np": "1",
            "ut": "bd1d9ddb04089700cf9c27f6f7426281",
            "fltt": "2", "invt": "2",
            "fid": "f62",
            "fs": "m:90+t:2+f:!50",
            "fields": "f3,f14,f62,f184",
        }

        resp = requests.get(url, params=params, proxies=proxies, timeout=15)
        diff = resp.json().get("data", {}).get("diff", [])
        if not diff:
            return []

        flows = []
        top_n, bottom_n = 10, 5
        # 防止 top 和 bottom 重叠
        bottom_start = max(top_n, len(diff) - bottom_n)

        # 净流入前 10
        for item in diff[:top_n]:
            flows.append({
                "name": item.get("f14", ""),
                "net_inflow": item.get("f62", 0),
                "net_inflow_pct": item.get("f184", 0),
                "change_pct": item.get("f3", 0),
                "rank": "inflow",
            })

        # 净流出后 5
        for item in diff[bottom_start:]:
            flows.append({
                "name": item.get("f14", ""),
                "net_inflow": item.get("f62", 0),
                "net_inflow_pct": item.get("f184", 0),
                "change_pct": item.get("f3", 0),
                "rank": "outflow",
            })

        logger.info(f"获取资金流向成功: {len(flows)} 个")
        return flows

    # ------------------------------------------------------------------
    # 多源新闻聚合
    # ------------------------------------------------------------------

    def _collect_all_news(self, proxies: Optional[Dict]) -> List[Dict]:
        """
        多源新闻聚合：国内财经 + 全球财经 + Finnhub 英文新闻
        返回统一格式，按 category 分组，自动去重。
        """
        all_news: List[Dict] = []

        # 1. 东财 A 股财经要闻 (column=350)
        domestic = self._fetch_eastmoney_news_by_column("350", "domestic", proxies)
        all_news.extend(domestic)

        # 2. 东财全球财经 (column=356: 外汇/大宗/特朗普/美联储等)
        global_finance = self._fetch_eastmoney_news_by_column("356", "global", proxies)
        all_news.extend(global_finance)

        # 3. 东财国际时事 (column=351: 地缘政治/国际大事)
        intl_affairs = self._fetch_eastmoney_news_by_column("351", "global", proxies)
        all_news.extend(intl_affairs)

        # 4. Finnhub 英文财经新闻
        finnhub_news = self._fetch_finnhub_news()
        all_news.extend(finnhub_news)

        # 去重（按标题前20字符）
        seen = set()
        deduped = []
        for n in all_news:
            key = n["title"][:20]
            if key not in seen:
                seen.add(key)
                deduped.append(n)

        domestic_count = sum(1 for n in deduped if n["category"] == "domestic")
        global_count = sum(1 for n in deduped if n["category"] == "global")
        finnhub_count = sum(1 for n in deduped if n["category"] == "finnhub")
        logger.info(f"新闻聚合完成: 国内{domestic_count} + 全球{global_count} + Finnhub{finnhub_count} = {len(deduped)} 条")
        return deduped

    def search_global_headlines(self, queries: List[str], max_results: int = 5) -> List[Dict]:
        """
        用真·联网搜索（DuckDuckGo）补充外部头条，产出 category='websearch' 的条目。
        与 _collect_all_news 同构 [{title, body, source, time, category, lang, url}]，可直接并入同一 list。
        失败返回 []，不影响东财/Finnhub 三路。
        """
        from knowledge_engine.websearch import web_search

        out: List[Dict] = []
        seen = set()
        for q in queries:
            try:
                hits = web_search(q, max_results=max_results)
            except Exception:
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

    def _fetch_eastmoney_news_by_column(
        self, column: str, category: str, proxies: Optional[Dict], limit: int = 10
    ) -> List[Dict]:
        """从东方财富指定频道获取新闻（走统一网络层：快代理→轮换重试，绝不直连兜底，不依赖 Clash）"""
        from net import domestic_json

        url = "https://np-listapi.eastmoney.com/comm/web/getNewsByColumns"
        params = {
            "client": "web",
            "biz": "web_news_col",
            "column": column,
            "page_size": str(limit),
            "req_trace": str(int(time.time() * 1000)),
        }

        try:
            data = domestic_json(url, params=params, timeout=15, prefer_direct=True)
            raw = (data or {}).get("data")
            if not raw:
                return []
            news_list = raw.get("list", [])
        except Exception as e:
            logger.warning(f"东财新闻 column={column} 失败: {e}")
            return []

        results = []
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
                # 之前漏读了这个字段——实测 getNewsByColumns 原始响应本就带 url/uniqueUrl，
                # 不是数据源没有链接，是代码没取（uniqueUrl 兜底，两者实测都指向同一篇文章页）
                "url": item.get("url") or item.get("uniqueUrl") or "",
            })

        logger.info(f"东财新闻 column={column}: {len(results)} 条")
        return results[:limit]

    def _fetch_finnhub_news(self) -> List[Dict]:
        """从 Finnhub 获取英文市场新闻（需要 FINNHUB_API_KEY 环境变量）"""
        import os
        import requests

        api_key = os.environ.get("FINNHUB_API_KEY", "")
        if not api_key:
            logger.info("未配置 FINNHUB_API_KEY，跳过 Finnhub 新闻")
            return []

        url = "https://finnhub.io/api/v1/news"
        params = {
            "category": "general",
            "token": api_key,
        }

        try:
            resp = requests.get(url, params=params, timeout=15)
            if resp.status_code == 401:
                logger.warning("Finnhub API key 无效")
                return []
            if resp.status_code == 429:
                logger.warning("Finnhub 请求频率超限")
                return []
            items = resp.json()
            if not isinstance(items, list):
                return []
        except Exception as e:
            logger.warning(f"Finnhub 新闻获取失败: {e}")
            return []

        results = []
        for item in items[:10]:
            headline = item.get("headline", "")
            if not headline:
                continue
            # 将 UNIX 时间戳转为可读时间
            ts = item.get("datetime")
            time_str = ""
            if ts:
                from datetime import datetime
                try:
                    time_str = datetime.fromtimestamp(ts).strftime("%Y-%m-%d %H:%M")
                except (ValueError, OSError):
                    pass
            results.append({
                "title": headline,
                "body": (item.get("summary") or "")[:300],
                "source": item.get("source") or "",
                "time": time_str,
                "category": "finnhub",
                "lang": "en",
                # 之前漏读了这个字段——Finnhub /api/v1/news 本就带 url
                # （news_engine/fetcher.py::fetch_general_news 对同一数据源已验证过字段名）
                "url": item.get("url") or "",
            })

        logger.info(f"Finnhub 新闻: {len(results)} 条")
        return results

    # ------------------------------------------------------------------
    # 个股新闻搜索
    # ------------------------------------------------------------------

    def search_stock_news(self, keyword: str, limit: int = 10) -> List[Dict]:
        """
        搜索特定股票的相关新闻（东财搜索 API）

        Args:
            keyword: 股票代码或名称，如 "600519" 或 "贵州茅台"
            limit: 返回条数

        Returns:
            新闻列表 [{title, body, source, time, url}]
        """
        return self._fetch_with_retry(
            lambda proxies: self._do_search_stock_news(keyword, limit, proxies)
        )

    def _do_search_stock_news(
        self, keyword: str, limit: int, proxies: Optional[Dict]
    ) -> List[Dict]:
        """调用东财搜索 API 获取个股新闻"""
        import json as _json
        import requests

        url = "https://search-api-web.eastmoney.com/search/jsonp"

        param_obj = {
            "uid": "",
            "keyword": keyword,
            "type": ["cmsArticleWebOld"],
            "client": "web",
            "clientType": "web",
            "clientVersion": "curr",
            "param": {
                "cmsArticleWebOld": {
                    "searchScope": "default",
                    "sort": "default",
                    "pageIndex": 1,
                    "pageSize": limit,
                    "preTag": "",
                    "postTag": "",
                }
            },
        }

        params = {
            "cb": "callback",
            "param": _json.dumps(param_obj, ensure_ascii=False),
        }

        resp = requests.get(url, params=params, proxies=proxies, timeout=15)
        text = resp.text

        # 去除 JSONP 包装: callback({...})
        if text.startswith("callback(") and text.endswith(");"):
            text = text[len("callback("):-len(");")]
        elif text.startswith("callback(") and text.endswith(")"):
            text = text[len("callback("):-len(")")]

        data = _json.loads(text)
        if data.get("code") != 0:
            logger.warning(f"东财搜索 API 返回错误: {data.get('msg')}")
            return []

        articles = (
            data.get("result", {})
            .get("cmsArticleWebOld", [])
        )

        results = []
        for item in articles:
            title = item.get("title", "")
            if not title:
                continue
            results.append({
                "title": title,
                "body": (item.get("content") or "")[:200],
                "source": item.get("mediaName") or "",
                "time": item.get("date") or "",
                "url": item.get("url") or "",
                "category": "stock_news",
                "lang": "zh",
            })

        logger.info(f"东财个股搜索 '{keyword}': {len(results)} 条")
        return results

    # ------------------------------------------------------------------
    # 北向资金
    # ------------------------------------------------------------------

    def _fetch_northbound_realtime(self, proxies: Optional[Dict]) -> List[Dict]:
        """获取今日北向资金实时净流入数据（沪股通+深股通）

        API 返回结构:
            data.hk2sh = {dayNetAmtIn, dayAmtRemain, dayAmtThreshold, date2}  沪股通(北向)
            data.hk2sz = {dayNetAmtIn, dayAmtRemain, dayAmtThreshold, date2}  深股通(北向)
            data.sh2hk = {...}  港股通沪(南向)
            data.sz2hk = {...}  港股通深(南向)
        金额单位：万元
        """
        import requests

        url = "https://push2.eastmoney.com/api/qt/kamt/get"
        params = {
            "fields1": "f1,f2,f3,f4",
            "fields2": "f51,f52,f53,f54,f55,f56",
            "ut": "bd1d9ddb04089700cf9c27f6f7426281",
            "_": str(int(time.time() * 1000)),
        }

        resp = requests.get(url, params=params, proxies=proxies, timeout=15)
        data = resp.json().get("data", {})
        if not data:
            return []

        hk2sh = data.get("hk2sh", {})
        hk2sz = data.get("hk2sz", {})
        sh2hk = data.get("sh2hk", {})
        sz2hk = data.get("sz2hk", {})

        sh_net = hk2sh.get("dayNetAmtIn", 0) or 0  # 沪股通净买入（万元）
        sz_net = hk2sz.get("dayNetAmtIn", 0) or 0  # 深股通净买入（万元）
        nb_total = sh_net + sz_net                    # 北向合计

        sh_south = sh2hk.get("dayNetAmtIn", 0) or 0
        sz_south = sz2hk.get("dayNetAmtIn", 0) or 0
        sb_total = sh_south + sz_south                # 南向合计

        result = {
            "hk_to_sh": sh_net,
            "hk_to_sz": sz_net,
            "northbound_total": nb_total,
            "southbound_total": sb_total,
            "date": hk2sh.get("date2", ""),
            "sh_remain": hk2sh.get("dayAmtRemain", 0),    # 沪股通剩余额度
            "sz_remain": hk2sz.get("dayAmtRemain", 0),    # 深股通剩余额度
            "sh_threshold": hk2sh.get("dayAmtThreshold", 0),
            "sz_threshold": hk2sz.get("dayAmtThreshold", 0),
        }

        logger.info(f"北向资金实时: 沪{sh_net:.0f}万 深{sz_net:.0f}万 合计{nb_total:.0f}万")
        return [result]

    def _fetch_northbound_history(self, proxies: Optional[Dict]) -> List[Dict]:
        """获取近10日北向资金历史净流入数据

        API 返回结构:
            data.hk2sh = ["日期,当日净买入(万元),剩余额度(万元),累计净买入(万元)", ...]
            data.hk2sz = [...]
            data.s2n   = [...]  北向合计
        """
        import requests

        url = "https://push2his.eastmoney.com/api/qt/kamt.kline/get"
        params = {
            "fields1": "f1,f2,f3,f4,f5",
            "fields2": "f51,f52,f53,f54,f55,f56",
            "klt": "101",       # 日线
            "lmt": "10",        # 最近10天
            "end": "20500101",
            "ut": "bd1d9ddb04089700cf9c27f6f7426281",
            "_": str(int(time.time() * 1000)),
        }

        resp = requests.get(url, params=params, proxies=proxies, timeout=15)
        data = resp.json().get("data", {})
        if not data:
            return []

        # 分别取沪股通、深股通、北向合计
        hk2sh_lines = data.get("hk2sh", [])
        hk2sz_lines = data.get("hk2sz", [])
        s2n_lines = data.get("s2n", [])

        if not s2n_lines:
            return []

        def _parse_val(s: str):
            if s == "-" or s == "":
                return None
            try:
                return float(s)
            except (ValueError, TypeError):
                return None

        # 以 s2n（北向合计）为基准，同时提取沪股通/深股通明细
        sh_map = {}
        for line in hk2sh_lines:
            if not isinstance(line, str):
                continue
            parts = line.split(",")
            if len(parts) >= 2:
                sh_map[parts[0]] = _parse_val(parts[1])

        sz_map = {}
        for line in hk2sz_lines:
            if not isinstance(line, str):
                continue
            parts = line.split(",")
            if len(parts) >= 2:
                sz_map[parts[0]] = _parse_val(parts[1])

        history = []
        for line in s2n_lines:
            if not isinstance(line, str):
                continue
            parts = line.split(",")
            if len(parts) < 4:
                continue
            dt = parts[0]
            history.append({
                "date": dt,
                "hk_to_sh": sh_map.get(dt),            # 沪股通当日净买入（万元）
                "hk_to_sz": sz_map.get(dt),            # 深股通当日净买入（万元）
                "northbound_total": _parse_val(parts[1]),  # 北向合计当日净买入（万元）
                "cumulative_total": _parse_val(parts[3]),  # 累计净买入（万元）
            })

        logger.info(f"北向资金历史: {len(history)} 日")
        return history

    def _fetch_northbound_top_stocks(self, proxies: Optional[Dict]) -> List[Dict]:
        """获取北向资金季度持仓 Top 20（按持股市值排序）

        2024-08-19 起港交所将北向持仓数据从每日改为每季度披露，
        使用新报表 RPT_MUTUAL_HOLDSTOCKNORTH_STA（最新数据为上季末）。
        """
        import requests

        url = "https://datacenter-web.eastmoney.com/api/data/v1/get"
        params = {
            "sortColumns": "HOLD_MARKET_CAP",
            "sortTypes": "-1",
            "pageSize": "20",
            "pageNumber": "1",
            "reportName": "RPT_MUTUAL_HOLDSTOCKNORTH_STA",
            "columns": "SECURITY_CODE,SECURITY_NAME,SECUCODE,CLOSE_PRICE,CHANGE_RATE,"
                       "HOLD_SHARES,HOLD_MARKET_CAP,A_SHARES_RATIO,"
                       "FREE_SHARES_RATIO,TOTAL_SHARES_RATIO,TRADE_DATE",
            "source": "WEB",
            "client": "WEB",
            "_": str(int(time.time() * 1000)),
        }

        resp = requests.get(url, params=params, proxies=proxies, timeout=15)
        data = resp.json()

        result_data = data.get("result")
        if not result_data:
            return []
        items = result_data.get("data", [])
        if not items:
            return []

        trade_date = items[0].get("TRADE_DATE", "")[:10] if items else ""

        results = []
        for item in items:
            results.append({
                "name": item.get("SECURITY_NAME", ""),
                "code": item.get("SECURITY_CODE", ""),
                "secucode": item.get("SECUCODE", ""),
                "close": item.get("CLOSE_PRICE"),
                "change_pct": item.get("CHANGE_RATE"),
                "hold_shares": item.get("HOLD_SHARES"),              # 持股数(股)
                "hold_market_cap": item.get("HOLD_MARKET_CAP"),      # 持股市值(元)
                "a_shares_ratio": item.get("A_SHARES_RATIO"),        # 占A股比 %
                "free_shares_ratio": item.get("FREE_SHARES_RATIO"),  # 占流通股比 %
                "total_shares_ratio": item.get("TOTAL_SHARES_RATIO"),  # 占总股本比 %
                "trade_date": trade_date,
            })

        logger.info(f"北向资金季度持仓 Top{len(results)} ({trade_date})")
        return results

    # ------------------------------------------------------------------
    # 个股基本面
    # ------------------------------------------------------------------

    def fetch_stock_fundamentals(self, symbols: List[str]) -> Dict[str, Dict]:
        """
        批量获取个股基本面数据（PE/PB/市值/ROE）

        Args:
            symbols: 股票代码列表，如 ["600519.SH", "000001.SZ"]

        Returns:
            {symbol: {pe, pe_ttm, pb, total_market_cap, float_market_cap, roe}}
        """
        if not symbols:
            return {}

        # 将 symbol 转为 eastmoney secid 格式
        secids = []
        symbol_map = {}  # secid -> symbol 的反向映射
        for sym in symbols:
            code = sym.split(".")[0]
            suffix = sym.split(".")[-1].upper() if "." in sym else ""
            if suffix == "SH":
                secid = f"1.{code}"
            elif suffix == "SZ":
                secid = f"0.{code}"
            else:
                continue
            secids.append(secid)
            symbol_map[code] = sym

        if not secids:
            return {}

        # 带代理重试
        for attempt in range(1, MAX_RETRIES + 1):
            proxies = self._get_proxies()
            try:
                result = self._do_fetch_fundamentals(secids, symbol_map, proxies)
                if result:
                    return result
            except Exception as e:
                logger.warning(f"fetch_stock_fundamentals 第{attempt}次失败: {e}")
                self._switch_proxy()

        return {}

    def _do_fetch_fundamentals(
        self, secids: List[str], symbol_map: Dict[str, str], proxies: Optional[Dict]
    ) -> Dict[str, Dict]:
        """实际执行基本面数据获取"""
        import requests

        url = "https://push2.eastmoney.com/api/qt/ulist.np/get"
        params = {
            "fltt": "2",
            "invt": "2",
            "fields": "f2,f9,f12,f14,f20,f21,f23,f37,f115",
            "secids": ",".join(secids),
            "ut": "bd1d9ddb04089700cf9c27f6f7426281",
            "_": str(int(time.time() * 1000)),
        }

        resp = requests.get(url, params=params, proxies=proxies, timeout=15)
        diff = resp.json().get("data", {}).get("diff", [])
        if not diff:
            return {}

        result: Dict[str, Dict] = {}
        for item in diff:
            code = str(item.get("f12", ""))
            sym = symbol_map.get(code)
            if not sym:
                continue

            def _safe_float(val):
                if val is None or val == "-" or val == "":
                    return None
                try:
                    return float(val)
                except (ValueError, TypeError):
                    return None

            result[sym] = {
                "price": _safe_float(item.get("f2")),
                "pe": _safe_float(item.get("f9")),           # 动态市盈率
                "pe_ttm": _safe_float(item.get("f115")),     # 市盈率TTM
                "pb": _safe_float(item.get("f23")),           # 市净率
                "total_market_cap": _safe_float(item.get("f20")),  # 总市值（元）
                "float_market_cap": _safe_float(item.get("f21")),  # 流通市值（元）
                "roe": _safe_float(item.get("f37")),          # ROE %
            }

        logger.info(f"获取个股基本面成功: {len(result)}/{len(secids)} 只")
        return result

"""
大盘市场纵览聚合 —— 指数/指数K线/板块/概念/资金流/北向/综合新闻，一次拿齐。

13.4-2 S7f：从 report_engine/web_searcher.py::MarketWebSearcher.search_market_overview
迁出（web_searcher 淘汰）。这是**引擎层编排**——各路取数已下沉 acquisition/markets
（走铁律）与 news_engine（新闻），本函数只负责组装 + top/bottom 切片，不直接出网。

唯一消费方：report_engine/data_collector._collect_market_overview。
"""
from collections.abc import Callable

from loguru import logger

# 上证/深证成指/创业板/沪深300/中证500/科创50
_INDEX_SECIDS = ["1.000001", "0.399001", "0.399006", "1.000300", "1.000905", "1.000688"]


def _safe(fn: Callable, default):
    """单路取数失败优雅降级（acquisition fetcher 内部已换 IP 重试，这里只兜底异常）。"""
    try:
        return fn()
    except Exception as e:  # noqa: BLE001
        logger.warning(f"市场纵览分路取数失败 {getattr(fn, '__name__', fn)}: {e}")
        return default


def _top_bottom(rows: list[dict], top_n: int, bottom_n: int,
                top_tag: str, bottom_tag: str, fields: tuple) -> list[dict]:
    """取 top/bottom 并只保留指定字段 + rank 标签（保持 web_searcher 原输出形状）。"""
    bottom_start = max(top_n, len(rows) - bottom_n)
    picked = [(r, top_tag) for r in rows[:top_n]] + [(r, bottom_tag) for r in rows[bottom_start:]]
    return [{**{k: r.get(k) for k in fields}, "rank": tag} for r, tag in picked]


def collect_market_overview() -> dict:
    """组装大盘纵览。返回 {indices, index_klines, sectors, concepts, money_flow,
    northbound, news, source}（形状与原 search_market_overview 一致）。"""
    from acquisition.markets import northbound as nb
    from acquisition.markets import sectors as sec
    from acquisition.markets.index_klines import fetch_index_klines
    from acquisition.markets.quote_router import fetch_index_snapshot
    from news_engine.fetcher import NewsFetcher

    result: dict = {
        "indices": [], "index_klines": [], "sectors": [], "concepts": [],
        "money_flow": [], "northbound": {}, "news": [], "source": "web_search",
    }

    result["indices"] = _safe(lambda: fetch_index_snapshot(_INDEX_SECIDS), [])
    result["index_klines"] = _safe(fetch_index_klines, [])
    result["sectors"] = _top_bottom(_safe(lambda: sec.fetch_boards("f3"), []),
                                    5, 5, "top", "bottom",
                                    ("name", "change_pct", "leader", "leader_pct"))
    result["concepts"] = _top_bottom(_safe(sec.fetch_concept_boards, []),
                                     10, 5, "top", "bottom",
                                     ("name", "change_pct", "up_count", "down_count",
                                      "leader", "leader_pct"))
    result["money_flow"] = _top_bottom(_safe(lambda: sec.fetch_boards("f62"), []),
                                       10, 5, "inflow", "outflow",
                                       ("name", "net_inflow", "net_inflow_pct", "change_pct"))

    realtime = _safe(nb.fetch_northbound_realtime, [])
    nb_dict = realtime[0] if realtime and isinstance(realtime[0], dict) else {}
    nb_dict["history"] = _safe(nb.fetch_northbound_history, [])
    nb_dict["top_stocks"] = _safe(nb.fetch_northbound_top_stocks, [])
    result["northbound"] = nb_dict

    result["news"] = _safe(NewsFetcher().collect_market_news, [])
    return result

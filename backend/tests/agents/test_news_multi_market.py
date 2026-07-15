"""
news.py 港股/美股个股新闻 + 无 symbol 大盘新闻回归测试（Fix 4，不打真实网络）。

在这次修复之前，market="hk_stock"/"us_stock" 是从头到尾的空头支票——schema
声称支持，NewsSubagent.run() 却只有 market=="a_share" 一条真正会抓数据的分支，
其余永远走"未抓到新闻"。现在 get_realtime_sentiment 按 market 路由到真实数据源
（港股复用 ak.stock_news_em，美股用 Finnhub company_news），这里验证路由和
字段映射正确，不验证底层数据源本身（那些已经在设计阶段用真实 API 现场验证过）。
"""
import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

from agents.subagents.news import NewsSubagent  # noqa: E402


def _drain_and_get_done_result(gen):
    result = None
    for line in gen:
        ev = json.loads(line)
        if ev.get("event") == "subagent_done":
            result = ev.get("result")
    return result


def test_hk_stock_routes_market_through_to_get_realtime_sentiment(monkeypatch):
    import news_engine.realtime as realtime_mod

    calls = []

    def fake_sentiment(symbol, market="a_share"):
        calls.append((symbol, market))
        return {"available": True, "articles": [
            {"title": "腾讯控股回购", "source": "东方财富", "published_at": "2026-07-06", "sentiment": "positive"},
        ]}

    monkeypatch.setattr(realtime_mod, "get_realtime_sentiment", fake_sentiment)
    # NewsAnalyzer 在 run() 函数体内本地 import，必须打到源模块
    monkeypatch.setattr("news_engine.analyzer.NewsAnalyzer",
                         lambda: type("F", (), {"report_stream_from_articles":
                                      lambda self, articles, symbol, model: iter([])})())

    list(NewsSubagent().run({"symbol": "00700.HK", "market": "hk_stock"}))
    assert calls == [("00700.HK", "hk_stock")]


def test_us_stock_routes_market_through_to_get_realtime_sentiment(monkeypatch):
    import news_engine.realtime as realtime_mod

    calls = []

    def fake_sentiment(symbol, market="a_share"):
        calls.append((symbol, market))
        return {"available": True, "articles": [
            {"title": "Apple earnings beat", "source": "Finnhub", "published_at": "2026-07-06", "sentiment": "positive"},
        ]}

    monkeypatch.setattr(realtime_mod, "get_realtime_sentiment", fake_sentiment)
    monkeypatch.setattr("news_engine.analyzer.NewsAnalyzer",
                         lambda: type("F", (), {"report_stream_from_articles":
                                      lambda self, articles, symbol, model: iter([])})())

    list(NewsSubagent().run({"symbol": "AAPL", "market": "us_stock"}))
    assert calls == [("AAPL", "us_stock")]


def test_no_symbol_uses_market_web_searcher_aggregation(monkeypatch):
    """不填 symbol 时应该走 NewsFetcher.collect_market_news，而不是永远
    返回"未抓到新闻"（这是修复前 not-symbol 分支的唯一行为）。"""
    fake_news = [
        {"title": "A股早盘冲高回落", "source": "东方财富", "time": "2026-07-06 09:30", "category": "domestic"},
        {"title": "Fed signals rate pause", "source": "Finnhub", "time": "2026-07-06 08:00", "category": "finnhub"},
    ]

    class _FakeSearcher:
        def collect_market_news(self, limit=10):
            return fake_news

    captured_articles = {}

    class _FakeAnalyzer:
        def report_stream_from_articles(self, articles, symbol, model):
            captured_articles["value"] = articles
            return iter([])

    monkeypatch.setattr("news_engine.fetcher.NewsFetcher", _FakeSearcher)
    monkeypatch.setattr("news_engine.analyzer.NewsAnalyzer", lambda: _FakeAnalyzer())

    list(NewsSubagent().run({}))  # 不填 symbol

    assert captured_articles["value"] is not None
    titles = [a["title"] for a in captured_articles["value"]]
    assert "A股早盘冲高回落" in titles
    assert "Fed signals rate pause" in titles
    # 聚合新闻没有逐条情绪打分，不应该被编造成假数据
    assert all("sentiment" not in a for a in captured_articles["value"])


def test_no_symbol_aggregation_failure_is_negative_not_crash(monkeypatch):
    class _BrokenSearcher:
        def collect_market_news(self, limit=10):
            raise RuntimeError("网络挂了")

    monkeypatch.setattr("news_engine.fetcher.NewsFetcher", _BrokenSearcher)

    result = _drain_and_get_done_result(NewsSubagent().run({}))
    assert result["ok"] is True
    assert result["business_result"] == "negative"

"""新闻/舆情解读 subagent —— 无状态实时抓取 + NewsAnalyzer 综合解读。"""
from typing import Generator

from agents.subagents.base import SubagentRunner, emit_subagent_done, relay_markdown
from agents.tool_envelope import ToolEnvelope


class NewsSubagent(SubagentRunner):
    name = "run_news_analysis"

    def run(self, args: dict) -> Generator[str, None, None]:
        symbol = args.get("symbol") or None
        market = args.get("market") or "a_share"

        from news_engine.analyzer import NewsAnalyzer
        from news_engine.realtime import get_realtime_sentiment
        from llm_config import get_cheap_model

        model = get_cheap_model()

        # 实时抓取 + 情绪打分（无状态，不入库），把带情绪标签的新闻喂给报告生成。
        # a_share/hk_stock/us_stock 三个市场现在都真的有数据源（get_realtime_sentiment
        # 内部按 market 路由到对应的 NewsFetcher 抓取方法），不再是只有 a_share 能
        # 真正工作、其余市场值形同虚设的空头支票。
        articles = []
        if symbol and market in ("a_share", "hk_stock", "us_stock"):
            r = get_realtime_sentiment(symbol, market=market)
            if r["available"]:
                articles = [
                    {"title": a["title"], "source": a["source"],
                     "published_at": a["published_at"], "sentiment": a["sentiment"],
                     "content": a["title"]}
                    for a in r["articles"]
                ]
        elif not symbol:
            # 不填 symbol = 大盘/全球整体新闻——复用 get_morning_brief 已经在跑、
            # 验证过的聚合路径（东财 A股要闻 + 全球财经 + Finnhub 英文新闻），
            # 而不是重新造一个抓取轮子。聚合新闻没有逐条情绪打分，sentiment
            # 留空即可，report_stream_from_articles 的 prompt 构建对缺失字段
            # 本就是 .get() 兜底，不需要编造假数据。
            from report_engine.web_searcher import MarketWebSearcher
            try:
                raw_news = MarketWebSearcher(random_ip=False)._collect_all_news(None)
            except Exception as e:  # noqa: BLE001 — 聚合失败按"没抓到"处理，不让异常炸主流程
                raw_news = []
            articles = [
                {"title": n.get("title"), "source": n.get("source"),
                 "published_at": n.get("time"), "content": n.get("body") or n.get("title")}
                for n in raw_news if n.get("title")
            ]

        if not articles:
            # 这是 Phase 3 修的旗舰 bug：以前这里漏了 ok 字段，被 orchestrator 默认判定成功，
            # 但"没抓到新闻"本该是明确的 business_result=negative（诚实的"无"），而不是
            # 隐式、意外地和真正跑出报告的情况一样被当成 affirmative。
            yield emit_subagent_done(ToolEnvelope(
                business_result="negative",
                message=f"[{self.name}] 近期未抓到 {symbol or market} 的相关新闻，暂无可解读舆情。",
            ))
            return

        inner = NewsAnalyzer().report_stream_from_articles(
            articles=articles, symbol=symbol, model=model,
        )
        yield from relay_markdown(inner, agent=self.name, model=model, max_summary=1200)

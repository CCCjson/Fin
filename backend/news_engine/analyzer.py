"""
OpenAI 新闻深度分析 — 流式输出
"""
import json
import os
import time
import uuid
from typing import Dict, Generator, List, Optional

from dotenv import load_dotenv
from loguru import logger
from openai import OpenAI
from httpx import Timeout as HttpxTimeout

from data_engine.storage.database import get_session
from data_engine.storage.models import NewsAnalysis, NewsArticle, NewsSentiment
from news_engine.prompts import build_single_analysis_prompt, build_report_prompt

load_dotenv(override=True)


def _ndjson(obj: dict) -> str:
    return json.dumps(obj, ensure_ascii=False) + "\n"


class NewsAnalyzer:
    """OpenAI 新闻深度分析服务"""

    SYSTEM_PROMPT = (
        "你是一位资深金融分析师，擅长从新闻中提取投资线索。\n"
        "分析要客观、专业、有深度，结合市场实际给出实用建议。\n"
        "输出使用 Markdown 格式，条理清晰。\n"
        "禁止说「不构成投资建议」等废话，直接给出判断。"
    )

    def _get_client(self) -> OpenAI:
        """创建 OpenAI 客户端"""
        api_key = os.getenv("OPENAI_API_KEY", "")
        base_url = os.getenv("OPENAI_BASE_URL", "https://api.openai.com/v1")

        if not api_key:
            raise ValueError("OPENAI_API_KEY 未配置")

        return OpenAI(
            api_key=api_key,
            base_url=base_url,
            timeout=HttpxTimeout(connect=15.0, read=300.0, write=30.0, pool=30.0),
            max_retries=2,
        )

    def analyze_single_stream(
        self,
        article_id: str,
        model: str = "gpt-4o",
    ) -> Generator[str, None, None]:
        """
        单篇新闻深度分析（流式 NDJSON）。

        Events: start → chunk → done | error
        """
        session = get_session()
        try:
            article = session.query(NewsArticle).filter_by(article_id=article_id).first()
            if not article:
                yield _ndjson({"event": "error", "message": "文章不存在"})
                return

            # 查询 BERT 情感结果
            sentiment_record = (
                session.query(NewsSentiment).filter_by(article_id=article_id).first()
            )
            sentiment_data = None
            if sentiment_record:
                sentiment_data = {
                    "sentiment": sentiment_record.sentiment,
                    "confidence": sentiment_record.confidence,
                    "prob_positive": sentiment_record.prob_positive,
                    "prob_negative": sentiment_record.prob_negative,
                    "prob_neutral": sentiment_record.prob_neutral,
                }

            article_data = {
                "title": article.title,
                "content": article.content or article.summary or article.title,
                "source": article.source,
                "published_at": str(article.published_at) if article.published_at else "未知",
                "symbol": article.symbol,
            }
        finally:
            session.close()

        # 构建 Prompt
        user_prompt = build_single_analysis_prompt(article_data, sentiment_data)

        # 创建分析记录
        analysis_id = f"na_{uuid.uuid4().hex[:12]}"
        yield _ndjson({"event": "start", "analysis_id": analysis_id})

        # 调用 OpenAI
        yield from self._stream_openai(
            analysis_id=analysis_id,
            analysis_type="single",
            article_id=article_id,
            symbol=article_data.get("symbol"),
            user_prompt=user_prompt,
            model=model,
        )

    def generate_report_stream(
        self,
        symbol: Optional[str] = None,
        market: str = "a_share",
        model: str = "gpt-4o",
    ) -> Generator[str, None, None]:
        """
        多篇新闻综合分析报告（流式 NDJSON）。

        Events: start → chunk → done | error
        """
        session = get_session()
        try:
            query = session.query(NewsArticle, NewsSentiment).outerjoin(
                NewsSentiment, NewsArticle.article_id == NewsSentiment.article_id
            )

            if symbol:
                query = query.filter(NewsArticle.symbol == symbol.split(".")[0])
            if market:
                query = query.filter(NewsArticle.market == market)

            query = query.order_by(NewsArticle.published_at.desc()).limit(30)
            rows = query.all()

            if not rows:
                yield _ndjson({"event": "error", "message": "没有可分析的新闻，请先抓取新闻"})
                return

            articles_for_prompt: List[Dict] = []
            for article, sentiment in rows:
                item = {
                    "title": article.title,
                    "content": article.content or article.summary or article.title,
                    "source": article.source,
                    "published_at": str(article.published_at) if article.published_at else "未知",
                }
                if sentiment:
                    item["sentiment"] = sentiment.sentiment
                    item["confidence"] = sentiment.confidence
                articles_for_prompt.append(item)

        finally:
            session.close()

        # 构建 Prompt
        user_prompt = build_report_prompt(articles_for_prompt, symbol)

        analysis_id = f"nr_{uuid.uuid4().hex[:12]}"
        yield _ndjson({"event": "start", "analysis_id": analysis_id})

        yield from self._stream_openai(
            analysis_id=analysis_id,
            analysis_type="report",
            article_id=None,
            symbol=symbol,
            user_prompt=user_prompt,
            model=model,
        )

    def _stream_openai(
        self,
        analysis_id: str,
        analysis_type: str,
        article_id: Optional[str],
        symbol: Optional[str],
        user_prompt: str,
        model: str,
    ) -> Generator[str, None, None]:
        """OpenAI 流式调用公共方法"""
        start_time = time.time()

        try:
            client = self._get_client()

            messages = [
                {"role": "system", "content": self.SYSTEM_PROMPT},
                {"role": "user", "content": user_prompt},
            ]

            stream_kwargs = dict(
                model=model,
                messages=messages,
                temperature=0.5,
                stream=True,
            )

            try:
                stream = client.chat.completions.create(
                    **stream_kwargs,
                    stream_options={"include_usage": True},
                )
            except Exception:
                stream = client.chat.completions.create(**stream_kwargs)

            full_content = ""
            token_count = 0

            for chunk in stream:
                if chunk.usage:
                    token_count = chunk.usage.total_tokens

                if chunk.choices:
                    delta = chunk.choices[0].delta
                    if delta and delta.content:
                        full_content += delta.content
                        yield _ndjson({"event": "chunk", "content": delta.content})

            elapsed = round(time.time() - start_time, 2)

            # 保存分析结果到数据库
            session = get_session()
            try:
                record = NewsAnalysis(
                    analysis_id=analysis_id,
                    analysis_type=analysis_type,
                    article_id=article_id,
                    symbol=symbol,
                    content=full_content,
                    model_used=model,
                    token_count=token_count,
                    status="completed",
                )
                session.add(record)
                session.commit()
            except Exception as e:
                session.rollback()
                logger.error(f"保存分析结果失败: {e}")
            finally:
                session.close()

            yield _ndjson({
                "event": "done",
                "analysis_id": analysis_id,
                "token_count": token_count,
                "generation_time": elapsed,
            })

        except Exception as e:
            logger.error(f"OpenAI 新闻分析失败: {e}")
            yield _ndjson({"event": "error", "message": f"AI 分析失败: {e}"})

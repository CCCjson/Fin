"""
BERT 双语情感分析器 — 懒加载单例模式
中文: yiyanghkust/finbert-tone-chinese
英文: ProsusAI/finbert
"""
import threading
from datetime import datetime
from typing import Dict, List, Optional, Tuple

from loguru import logger

from data_engine.storage.database import get_session
from data_engine.storage.models import NewsArticle, NewsSentiment


class SentimentAnalyzer:
    """BERT 情感分析器（线程安全单例）"""

    _instance: Optional["SentimentAnalyzer"] = None
    _init_lock = threading.Lock()

    def __init__(self) -> None:
        self._zh_pipeline = None  # 中文 FinBERT
        self._en_pipeline = None  # 英文 FinBERT
        self._load_lock = threading.Lock()

    @classmethod
    def get_instance(cls) -> "SentimentAnalyzer":
        """获取单例实例"""
        if cls._instance is None:
            with cls._init_lock:
                if cls._instance is None:
                    cls._instance = cls()
        return cls._instance

    def _load_zh_model(self):
        """懒加载中文 FinBERT"""
        if self._zh_pipeline is not None:
            return
        with self._load_lock:
            if self._zh_pipeline is not None:
                return
            logger.info("正在加载中文 FinBERT 模型 (yiyanghkust/finbert-tone-chinese)...")
            from transformers import pipeline
            self._zh_pipeline = pipeline(
                "sentiment-analysis",
                model="yiyanghkust/finbert-tone-chinese",
                top_k=None,
                truncation=True,
                max_length=512,
            )
            logger.info("中文 FinBERT 加载完成")

    def _load_en_model(self):
        """懒加载英文 FinBERT"""
        if self._en_pipeline is not None:
            return
        with self._load_lock:
            if self._en_pipeline is not None:
                return
            logger.info("正在加载英文 FinBERT 模型 (ProsusAI/finbert)...")
            from transformers import pipeline
            self._en_pipeline = pipeline(
                "sentiment-analysis",
                model="ProsusAI/finbert",
                top_k=None,
                truncation=True,
                max_length=512,
            )
            logger.info("英文 FinBERT 加载完成")

    def analyze(self, text: str, language: str = "zh") -> Dict:
        """
        分析单条文本的情感。

        Returns:
            {
                "sentiment": "positive" | "negative" | "neutral",
                "confidence": float,
                "prob_positive": float,
                "prob_negative": float,
                "prob_neutral": float,
                "model_used": str,
            }
        """
        # 截断文本（BERT 512 token 限制，中文约 500 字）
        truncated = text[:500]

        if language == "zh":
            self._load_zh_model()
            pipe = self._zh_pipeline
            model_name = "yiyanghkust/finbert-tone-chinese"
        else:
            self._load_en_model()
            pipe = self._en_pipeline
            model_name = "ProsusAI/finbert"

        try:
            results = pipe(truncated)
        except Exception as e:
            logger.error(f"BERT 情感分析失败: {e}")
            return {
                "sentiment": "neutral",
                "confidence": 0.0,
                "prob_positive": 0.33,
                "prob_negative": 0.33,
                "prob_neutral": 0.34,
                "model_used": model_name,
            }

        # top_k=None 时 results 格式: [[{"label": "positive", "score": 0.9}, ...]]
        scores = results[0] if results and isinstance(results[0], list) else results
        prob_map: Dict[str, float] = {}
        for item in scores:
            label = item["label"].lower()
            # 统一标签名（中文模型可能返回不同标签）
            if label in ("positive", "pos", "利好"):
                prob_map["positive"] = item["score"]
            elif label in ("negative", "neg", "利空"):
                prob_map["negative"] = item["score"]
            elif label in ("neutral", "neu", "中性"):
                prob_map["neutral"] = item["score"]
            else:
                prob_map[label] = item["score"]

        prob_pos = prob_map.get("positive", 0.0)
        prob_neg = prob_map.get("negative", 0.0)
        prob_neu = prob_map.get("neutral", 0.0)

        # 取最高概率的标签
        best_label = max(prob_map, key=prob_map.get, default="neutral")
        confidence = prob_map.get(best_label, 0.0)

        # 标准化标签
        if best_label not in ("positive", "negative", "neutral"):
            best_label = "neutral"

        return {
            "sentiment": best_label,
            "confidence": round(confidence, 4),
            "prob_positive": round(prob_pos, 4),
            "prob_negative": round(prob_neg, 4),
            "prob_neutral": round(prob_neu, 4),
            "model_used": model_name,
        }

    def analyze_batch(self, article_ids: List[str]) -> List[Dict]:
        """
        批量分析新闻情感并写入数据库。
        返回分析结果列表。
        """
        session = get_session()
        results: List[Dict] = []

        try:
            articles = (
                session.query(NewsArticle)
                .filter(NewsArticle.article_id.in_(article_ids))
                .all()
            )

            for article in articles:
                # 跳过已分析的
                existing = (
                    session.query(NewsSentiment)
                    .filter_by(article_id=article.article_id)
                    .first()
                )
                if existing:
                    results.append({
                        "article_id": article.article_id,
                        "sentiment": existing.sentiment,
                        "confidence": existing.confidence,
                        "skipped": True,
                    })
                    continue

                # 用标题 + 内容前 300 字
                text = article.title or ""
                if article.content:
                    text += " " + article.content[:300]

                result = self.analyze(text.strip(), language=article.language or "zh")

                # 写入数据库
                sentiment_record = NewsSentiment(
                    article_id=article.article_id,
                    sentiment=result["sentiment"],
                    confidence=result["confidence"],
                    prob_positive=result["prob_positive"],
                    prob_negative=result["prob_negative"],
                    prob_neutral=result["prob_neutral"],
                    model_used=result["model_used"],
                    analyzed_at=datetime.now(),
                )
                session.add(sentiment_record)

                results.append({
                    "article_id": article.article_id,
                    "sentiment": result["sentiment"],
                    "confidence": result["confidence"],
                    "skipped": False,
                })

            session.commit()
            logger.info(f"情感分析完成: {len(results)} 条")

        except Exception as e:
            session.rollback()
            logger.error(f"批量情感分析失败: {e}")
            raise
        finally:
            session.close()

        return results

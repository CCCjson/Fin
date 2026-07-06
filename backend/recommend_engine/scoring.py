"""深度打分（aggregate）+ 并发 —— 从 agents/tools/recommend_tools.py 原样下沉。"""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from typing import Optional

from loguru import logger

# 盘中深度打分并发数（aggregate 偏 IO：新闻抓取 + ML 推理）
_AGG_WORKERS = 4


def _aggregate_safe(symbol: str) -> Optional[dict]:
    from cockpit_engine.aggregator import CockpitAggregator
    try:
        # light 档：跳过新闻情感 + ML 两个慢维度（批量选股用技术+基本面+持仓即可）
        return CockpitAggregator().aggregate(symbol, light=True)
    except Exception as e:
        logger.warning(f"深度打分 {symbol} 失败: {e}")
        return None


def _aggregate_many(symbols: list[str]) -> dict[str, dict]:
    if not symbols:
        return {}
    results: dict[str, dict] = {}
    workers = min(_AGG_WORKERS, len(symbols))
    with ThreadPoolExecutor(max_workers=workers) as pool:
        for sym, res in zip(symbols, pool.map(_aggregate_safe, symbols)):
            if res is not None:
                results[sym] = res
    return results

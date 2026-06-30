"""
决策驾驶舱引擎 — 整合技术/基本面/情感/ML/持仓五维，输出综合买卖建议。

- aggregator: 调用各现有引擎，归一化五维分（0-100）
- scorer: 确定性加权综合分 + 建议（不交给 LLM，保证可复现）
- prompt_builder: 构建 LLM 文字总结的 prompt（LLM 仅叙述，不打分）
"""
from cockpit_engine.aggregator import CockpitAggregator
from cockpit_engine.scorer import score_cockpit

__all__ = ["CockpitAggregator", "score_cockpit"]

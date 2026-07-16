"""
高影响关键词配置 —— 新闻重要度判定的单一真源。

供 news_scheduler（定时抓取后打标）与 news_engine.articles（/articles 排序）共用，
消除「route/scheduler 各引一份私有函数」的跨模块坏味道（域9 news 下沉）。
"""
import json
from pathlib import Path

from loguru import logger

_KEYWORDS_CONFIG_PATH = Path(__file__).resolve().parent.parent / "configs" / "news_high_impact_keywords.json"


def load_high_impact_keywords() -> dict[str, list[str]]:
    """读分类关键词 JSON。不做进程内缓存——文件很小，15分钟一次的读盘开销可忽略，
    换来 Jason 改词表立即生效、不用重启后端。文件缺失/损坏时降级为空（不中断主流程）。"""
    try:
        with open(_KEYWORDS_CONFIG_PATH, encoding="utf-8") as f:
            data = json.load(f)
        return {cat: [str(k).strip() for k in words if str(k).strip()]
                for cat, words in data.items()}
    except Exception as e:  # noqa: BLE001
        logger.warning(f"高影响关键词配置读取失败（跳过关键词判定）: {e}")
        return {}


def match_keyword(title: str, keywords_by_category: dict[str, list[str]]) -> tuple | None:
    """标题是否命中任意类别的关键词，命中返回 (category, matched_keyword)，否则 None。"""
    for category, words in keywords_by_category.items():
        for w in words:
            if w and w in title:
                return category, w
    return None

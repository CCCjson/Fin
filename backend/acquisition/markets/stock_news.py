"""
个股新闻站内搜索 —— 东财 search/jsonp（按代码/名称全文检索）。

走 `net.domestic_get`（铁律：快代理→轮换重试，绝不静默直连）取回 JSONP 文本，
剥掉 callback() 包装再解析。用作 advisor DDG 联网搜索的国内兜底。

13.4-2 S7d：从 report_engine/web_searcher.py 迁入（原为裸 requests）。
"""
import json as _json

from loguru import logger


def search_stock_news(keyword: str, limit: int = 10) -> list[dict]:
    """搜索个股相关新闻，返回 [{title, body, source, time, url, category, lang}]。"""
    from net import domestic_get

    param_obj = {
        "uid": "", "keyword": keyword, "type": ["cmsArticleWebOld"],
        "client": "web", "clientType": "web", "clientVersion": "curr",
        "param": {"cmsArticleWebOld": {
            "searchScope": "default", "sort": "default",
            "pageIndex": 1, "pageSize": limit, "preTag": "", "postTag": "",
        }},
    }
    resp = domestic_get(
        "https://search-api-web.eastmoney.com/search/jsonp",
        params={"cb": "callback", "param": _json.dumps(param_obj, ensure_ascii=False)},
        timeout=15,
    )
    if resp is None:
        return []

    text = resp.text
    if text.startswith("callback(") and text.endswith(");"):
        text = text[len("callback("):-len(");")]
    elif text.startswith("callback(") and text.endswith(")"):
        text = text[len("callback("):-len(")")]

    try:
        data = _json.loads(text)
    except (ValueError, TypeError):
        logger.warning(f"东财个股搜索 '{keyword}' 响应非 JSON")
        return []
    if data.get("code") != 0:
        logger.warning(f"东财搜索 API 返回错误: {data.get('msg')}")
        return []

    articles = (data.get("result", {}) or {}).get("cmsArticleWebOld", [])
    results = [{
        "title": it.get("title", ""),
        "body": (it.get("content") or "")[:200],
        "source": it.get("mediaName") or "",
        "time": it.get("date") or "",
        "url": it.get("url") or "",
        "category": "stock_news", "lang": "zh",
    } for it in articles if it.get("title")]
    logger.info(f"东财个股搜索 '{keyword}': {len(results)} 条")
    return results

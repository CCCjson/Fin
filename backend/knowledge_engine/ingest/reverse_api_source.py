"""
通用逆向 API 摄入源 — 把 discover_api 侦查过的任意站点 JSON 接口的数据，
经模板转自然语言入知识库。

复用逆向基建（浏览器过盾 discover 一次 → curl_cffi+cookie 高速复用 fetch_api）。
任何 discover 过的 (site, endpoint) 都能喂：给一个 text_fn 把 JSON 拼成知识文本即可。
这是「拿别人不易获取到的增量数据」的通用入口——针对性侦查新页/新端点后即插即用。

⚠️ fetch_api 需 KNOWLEDGE_PLAYWRIGHT_ENABLED（首次侦查用浏览器）；复用阶段不开浏览器。
"""
from typing import List, Callable, Optional, Dict, Any

from loguru import logger

from knowledge_engine.ingest import IngestPipeline


def ingest_reverse_api(
    site: str,
    endpoint: str,
    param_list: List[Dict[str, Any]],
    *,
    text_fn: Callable[[dict, dict], Optional[str]],
    title_fn: Callable[[dict, dict], str],
    id_fn: Callable[[dict, dict], str],
    source_type: str = "news",
    language: str = "zh",
    min_chars: int = 20,
    pre_chunked: bool = True,
) -> Dict[str, Any]:
    """
    通用摄入：对每组 params 调 fetch_api(site, endpoint, params) 拿 JSON →
    text_fn(data, params) 拼文本 → ingest。

    text_fn/title_fn/id_fn 接收 (fetched_data, params)；text_fn 返回 None/空则跳过。
    pre_chunked=True：短文本整段一片；False：交给 chunk_text 多片切（长文用）。
    """
    from knowledge_engine.reverse_api import fetch_api

    pipeline = IngestPipeline()
    ingested = skipped = failed = 0

    for params in param_list:
        try:
            res = fetch_api(site, endpoint=endpoint, params=params)
        except Exception as e:  # noqa: BLE001
            logger.warning(f"fetch_api 失败 {site}/{endpoint} {params}: {e}")
            failed += 1
            continue
        data = res.get("data")
        if not isinstance(data, dict):
            failed += 1
            continue

        try:
            text = text_fn(data, params)
        except Exception as e:  # noqa: BLE001
            logger.debug(f"text_fn 异常 {params}: {e}")
            text = None
        if not text or len(text) < min_chars:
            skipped += 1
            continue

        r = pipeline.ingest_text(
            source_type=source_type,
            title=title_fn(data, params),
            text=text,
            native_id=id_fn(data, params),
            language=language,
            metadata={"site": site, "endpoint": endpoint, **params},
            pre_chunked=([{"seq": 0, "text": text, "token_count": None}] if pre_chunked else None),
        )
        if r["is_new"]:
            ingested += 1
        else:
            skipped += 1

    logger.info(f"逆向摄入 {site}/{endpoint} 完成：新增 {ingested}，跳过 {skipped}，失败 {failed}")
    return {"ingested": ingested, "skipped": skipped, "failed": failed}


# ============ 首个应用：雪球个股财报发布日历 ============

def _xq_event_text(data: dict, params: dict) -> Optional[str]:
    items = (data.get("data") or {}).get("items") or []
    sym = params.get("symbol", "")
    lines = []
    for it in items:
        cal = it.get("cal_title", "")
        date = it.get("title", "")          # 雪球把日期放 title
        content = it.get("content", "")
        if date or content:
            lines.append(f"{date} {content}（{cal}）".strip())
    if not lines:
        return None
    return f"{sym} 财报/事件日历：" + "；".join(lines) + "。"


def ingest_xueqiu_events(symbols: List[str]) -> Dict[str, Any]:
    """雪球个股「财报发布日历/事件」→ 知识（研判时知道各票下次出报告的时点）。"""
    # 雪球代码格式 SH600519 / SZ000001
    def _xq(sym: str) -> str:
        code = sym.split(".")[0]
        if sym.upper().endswith(".SH") or code.startswith("6"):
            return "SH" + code
        return "SZ" + code

    param_list = [{"symbol": _xq(s)} for s in symbols]
    return ingest_reverse_api(
        "xueqiu.com", "entry", param_list,
        text_fn=_xq_event_text,
        title_fn=lambda d, p: f"{p['symbol']} 财报发布日历",
        id_fn=lambda d, p: f"xq_event:{p['symbol']}",
        source_type="news",
    )

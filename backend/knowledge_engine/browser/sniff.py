"""
嗅探页面 XHR/fetch 请求 + 差分定位 gate 头。

蓝本：worldcup2026/scrapers/sofascore_playwright.py 的 `_on_request` / `diff_gate_headers`，
站点无关化：不写死 host，捕获**所有** XHR/fetch，配对 response 标出哪些是真实 JSON 数据端点。

流程（对应 §3.20 第 1/3/4 步）：
  1. BrowserSession 先过盾（cookie 落盘）
  2. 挂 on("request")/on("response") 后 reload，捕获干净的 API 流量
  3. 每条请求差分剔除常规头，剩下的可疑头即 gate 头候选
"""
from typing import Optional

from loguru import logger

from knowledge_engine.browser.launch import BrowserSession

# 差分基线：裸 curl 默认也会发的常规头（这些不是 gate 头）
BASELINE_HEADERS = {
    "host", "user-agent", "accept", "accept-encoding", "accept-language",
    "connection", "content-length", "content-type", "cookie",
}

# gate 头候选：浏览器特有、可能用于反爬门禁的头
GATE_CANDIDATES = {
    "x-requested-with", "cache-control", "pragma", "sec-fetch-dest",
    "sec-fetch-mode", "sec-fetch-site", "sec-ch-ua", "sec-ch-ua-mobile",
    "sec-ch-ua-platform", "referer", "origin", "baggage", "sentry-trace",
    "authorization", "x-csrf-token", "x-xsrf-token", "x-token",
}


def diff_gate_headers(captured_headers: dict) -> dict:
    """从捕获的请求头里剔除常规基线头，定位 gate 头（浏览器特有、可能门禁）。"""
    gate = {}
    for key, value in captured_headers.items():
        k = key.lower()
        if k in BASELINE_HEADERS:
            continue
        if k in GATE_CANDIDATES or k.startswith("sec-") or k.startswith("x-"):
            gate[key] = value
    return gate


def sniff_requests(
    url: str,
    proxy: Optional[dict] = None,
    settle_ms: int = 6000,
    max_requests: int = 300,
) -> list[dict]:
    """
    打开 url，捕获所有 XHR/fetch 请求及其响应元数据。

    返回 [{url, method, resource_type, req_headers, gate_headers, status, content_type, is_json}]，
    按首次出现顺序。**必须在 run_off_loop() 内调用**（sync Playwright 约束）。
    """
    captured: dict[str, dict] = {}
    order: list[str] = []

    def on_request(req):
        try:
            if req.resource_type not in ("xhr", "fetch"):
                return
            if req.url not in captured:
                order.append(req.url)
                captured[req.url] = {
                    "url": req.url,
                    "method": req.method,
                    "resource_type": req.resource_type,
                    "req_headers": dict(req.headers),
                    "status": None,
                    "content_type": "",
                }
        except Exception:
            pass

    def on_response(resp):
        try:
            rec = captured.get(resp.url)
            if rec is not None:
                rec["status"] = resp.status
                rec["content_type"] = (resp.headers or {}).get("content-type", "")
        except Exception:
            pass

    with BrowserSession(proxy=proxy) as sess:
        sess.goto(url)                        # 先过盾（挑战/盾在此期间通过，cookie 落盘）
        page = sess.page
        page.on("request", on_request)
        page.on("response", on_response)
        try:
            page.reload(wait_until="domcontentloaded", timeout=sess.timeout_ms)
        except Exception as e:
            logger.debug(f"reload 异常（忽略，仍尝试收集）：{str(e)[:80]}")
        page.wait_for_timeout(settle_ms)

    out = []
    for u in order[:max_requests]:
        rec = captured[u]
        ct = (rec.get("content_type") or "").lower()
        rec["is_json"] = "json" in ct
        rec["gate_headers"] = diff_gate_headers(rec["req_headers"])
        out.append(rec)
    logger.info(f"嗅探 {url}：捕获 {len(out)} 条 XHR/fetch，其中 JSON 端点 "
                f"{sum(1 for r in out if r['is_json'])} 条")
    return out

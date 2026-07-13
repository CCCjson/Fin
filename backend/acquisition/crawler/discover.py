"""
探测类抓取器 `ApiDiscoverer` —— 侦查**新站点**的接口结构，产出站点配置。

一次浏览器会话内：过盾 → 嗅探 XHR/fetch → 差分 gate 头 → 采样字段 → 建端点。
产出端点记录（存进站点配置的 endpoints）：
  {name, url, url_base, default_params, method, gate_headers, is_json, status, sample_fields}
其中 url_base + default_params 拆开，便于 fetch_api 换参复用（如换股票代码）。

13.4-2 S6b 把 browser/discover.py + browser/sniff.py 合并于此（侦查编排 + 嗅探/差分）。
与 `BaseCrawler` **平级**（不继承）：探测靠浏览器嗅探，压根不发 HTTP GET。
含 sync Playwright，`discover` / `sniff_requests` **必须在 run_off_loop() 内调用**。
"""
from urllib.parse import urlsplit, parse_qsl
from typing import Callable, Optional

from loguru import logger

from acquisition.browser.launch import BrowserSession
from acquisition.browser import sessions
from acquisition.crawler.reverse import evaluate_fetch  # 同层：逆向类的浏览器内验证


def _split_url(url: str) -> tuple[str, dict]:
    """拆 url → (无 query 的 base, query 参数 dict)。"""
    parts = urlsplit(url)
    base = f"{parts.scheme}://{parts.netloc}{parts.path}"
    params = dict(parse_qsl(parts.query))
    return base, params


def _endpoint_name(url_base: str, taken: set) -> str:
    """从 url path 末段派生端点名，去重。"""
    seg = url_base.rstrip("/").rsplit("/", 1)[-1] or "endpoint"
    seg = seg.split(".")[0] or "endpoint"        # 去 .json 之类后缀
    name, i = seg, 2
    while name in taken:
        name = f"{seg}_{i}"
        i += 1
    taken.add(name)
    return name


def _top_keys(obj, limit: int = 25) -> list:
    """取 JSON 顶层字段名（dict）或首元素字段名（list），供人读/校验。"""
    if isinstance(obj, dict):
        keys = list(obj.keys())
        if len(keys) == 1 and isinstance(obj[keys[0]], dict):   # 常见 {data:{...}} 再下探一层
            keys += [f"{keys[0]}.{k}" for k in list(obj[keys[0]].keys())[:limit]]
        return keys[:limit]
    if isinstance(obj, list) and obj and isinstance(obj[0], dict):
        return list(obj[0].keys())[:limit]
    return []


def _capture_cookies(page, url: str) -> str:
    """从浏览器上下文取本域 cookie，拼成 'k=v; k=v' 串（fast_fetch 直打用）。"""
    try:
        host = urlsplit(url).netloc.lower()
        root = ".".join(host.split(".")[-2:])         # xueqiu.com
        pairs = []
        for c in page.context.cookies():
            d = (c.get("domain") or "").lstrip(".").lower()
            if d.endswith(root):
                pairs.append(f"{c['name']}={c['value']}")
        return "; ".join(pairs)
    except Exception as e:  # noqa: BLE001
        logger.debug(f"cookie 抓取失败：{str(e)[:80]}")
        return ""


def discover(url: str, proxy: Optional[dict] = None, settle_ms: int = 6000,
             max_endpoints: int = 25, session_id: Optional[str] = None,
             on_progress: Optional[Callable[[str, dict], None]] = None) -> dict:
    """侦查 url 的真实 JSON 接口，返回 {endpoints:[...], cookies:'k=v;...'}。

    session_id/on_progress 均可选，不传时零行为变化（不上报进度、随机分配独立 profile）。
    """
    captured: dict[str, dict] = {}
    order: list[str] = []
    sessions.report(session_id, on_progress, "browser_launch", url=url)

    def on_request(req):
        try:
            if req.resource_type not in ("xhr", "fetch"):
                return
            if req.url not in captured:
                order.append(req.url)
                captured[req.url] = {
                    "url": req.url, "method": req.method,
                    "req_headers": dict(req.headers),
                    "status": None, "content_type": "",
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

    with BrowserSession(proxy=proxy, session_id=session_id) as sess:
        sessions.report(session_id, on_progress, "goto", url=url)
        sess.goto(url)                            # 过盾（cookie 落盘）
        page = sess.page
        page.on("request", on_request)
        page.on("response", on_response)
        try:
            page.reload(wait_until="domcontentloaded", timeout=sess.timeout_ms)
        except Exception as e:
            logger.debug(f"reload 异常（忽略）：{str(e)[:80]}")
        sessions.report(session_id, on_progress, "sniffing", settle_ms=settle_ms)
        page.wait_for_timeout(settle_ms)

        endpoints, taken = [], set()
        for u in order:
            rec = captured[u]
            if "json" not in (rec.get("content_type") or "").lower():
                continue
            gate = diff_gate_headers(rec["req_headers"])
            url_base, default_params = _split_url(u)
            # 带 gate 头在浏览器内复打一次，取样字段 + 确认解锁
            sampled = evaluate_fetch(page, u, gate)
            sj = sampled.get("json")
            fields = _top_keys(sj)
            # 存一小段数据样本（截断），让 discover 结果常常直接含答案
            sample = ""
            if sampled.get("too_large"):             # 响应超 50MB：跳过取样，只记端点，防撑爆内存
                sample = "(响应过大，已跳过取样)"
            elif sj is not None:
                try:
                    import json as _json
                    sample = _json.dumps(sj, ensure_ascii=False)[:600]
                except Exception:
                    sample = ""
            endpoints.append({
                "name": _endpoint_name(url_base, taken),
                "url": u,
                "url_base": url_base,
                "default_params": default_params,
                "method": rec["method"],
                "gate_headers": gate,
                "is_json": True,
                "status": sampled.get("status") or rec.get("status"),
                "sample_fields": fields,
                "sample": sample,
            })
            if len(endpoints) >= max_endpoints:
                break

        # 抓浏览器会话 cookie（国内金融站真门禁：雪球 xq_a_token / 东财等），
        # 存进配置供 fast_fetch 带上直打；403 时 re-discover 会刷新。
        cookies = _capture_cookies(page, url)

    logger.info(f"侦查 {url}：产出 {len(endpoints)} 个 JSON 端点，cookie {len(cookies)} 字节")
    sessions.report(session_id, on_progress, "endpoints_found",
                     count=len(endpoints), cookie_bytes=len(cookies))
    return {"endpoints": endpoints, "cookies": cookies}


# ════════════════════════════════════════════════════════════════════════
# 嗅探页面 XHR/fetch + 差分定位 gate 头
# 13.4-2 S6b 从 browser/sniff.py 合并进来：与侦查编排同属探测类，合于一处。
# ════════════════════════════════════════════════════════════════════════

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


# ════════════════════════════════════════════════════════════════════════
# 探测类抓取器门面：与 BaseCrawler 平级（不继承），把上面的模块函数收成实例方法。
# 模块级 discover / sniff_requests 仍是主 API（reverse_api 直接 import 复用，签名不变）。
# ════════════════════════════════════════════════════════════════════════


class ApiDiscoverer:
    """探测类：侦查新站点的真实 JSON 接口结构，产出可复用的站点配置。

    与 `BaseCrawler` **平级**——不继承（探测靠浏览器嗅探 XHR/fetch，压根不发 HTTP GET）。
    `discover` / `sniff` 含 sync Playwright，**必须在 run_off_loop() 内调用**。

    Example:
        >>> from acquisition.browser.launch import run_off_loop
        >>> res = run_off_loop(ApiDiscoverer().discover, "https://xueqiu.com/S/SH600519", proxy)
    """

    def __init__(self, *, settle_ms: int = 6000, max_endpoints: int = 25) -> None:
        """
        Args:
            settle_ms: reload 后等待页面异步请求落定的时长（毫秒）。
            max_endpoints: 单次侦查最多记录的 JSON 端点数（防端点爆炸）。
        """
        self.settle_ms = settle_ms
        self.max_endpoints = max_endpoints

    def discover(self, url: str, proxy: Optional[dict] = None,
                 session_id: Optional[str] = None,
                 on_progress: Optional[Callable[[str, dict], None]] = None) -> dict:
        """侦查 url 的真实 JSON 接口，返回 {endpoints, cookies}。委托模块函数 discover。"""
        return discover(url, proxy=proxy, settle_ms=self.settle_ms,
                        max_endpoints=self.max_endpoints,
                        session_id=session_id, on_progress=on_progress)

    def sniff(self, url: str, proxy: Optional[dict] = None,
              max_requests: int = 300) -> list[dict]:
        """捕获 url 的所有 XHR/fetch 请求及响应元数据。委托模块函数 sniff_requests。"""
        return sniff_requests(url, proxy=proxy, settle_ms=self.settle_ms,
                              max_requests=max_requests)

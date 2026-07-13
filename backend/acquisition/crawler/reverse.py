"""
逆向类抓取器 `ReverseApiCrawler` —— 按 `sites/` 站点配置调用**已侦查好**的接口。

三层能力合于一处（13.4-2 S6b 把 reverse_api.py + browser/reverse.py 合并）：
  1. 编排层：discover_api / fetch_api / scrape —— 侦查+取数+cookie 自愈串成一步。
  2. 浏览器内验证：evaluate_fetch / verify_gate —— 在页面上下文 fetch，确认某端点
     带上 gate 头能否 200 解锁（侦查阶段用，必须在 run_off_loop() 内）。
  3. curl_cffi 高速直打：fast_fetch —— 侦查完成后带 gate 头 + cookie 直打（快 10x），
     走国内快代理池，遇 403 触发 on_403（re-discover 刷新）后重试。

与 BaseCrawler **平级**（不继承）：逆向靠浏览器过盾 + curl_cffi 打指纹，套不进
BaseCrawler 的「一次有上限 HTTP GET」模型，但同样复用 net 原语（bounded_get）。

对外由 agents/tools/knowledge_tools.py 包成 scrape 这个 @tool 给 MoneyBill；
discover_api/fetch_api 也被 knowledge_engine/ingest/reverse_api_source.py 摄入源
直接 import 复用（模块级函数签名保持不变）。
"""
import json
import time
from typing import Callable, Optional

from loguru import logger

from acquisition.browser import sessions
from net.bounded import bounded_get, MAX_RESPONSE_BYTES


def discover_api(url: str, session_id: Optional[str] = None,
                  on_progress: Optional[Callable[[str, dict], None]] = None) -> dict:
    """用浏览器过盾嗅探 JSON 接口 + 差分 gate 头 + 抓 cookie，存成站点复用配置（每站只侦查一次）。"""
    from acquisition.config import get_playwright_enabled
    if not get_playwright_enabled():
        return {"summary": {"count": 0,
                            "message": "浏览器爬虫未启用：请在 .env 设 KNOWLEDGE_PLAYWRIGHT_ENABLED=true"}}
    from acquisition.browser.launch import run_off_loop
    from acquisition.crawler.discover import discover
    from acquisition.browser.proxy_route import playwright_proxy_for, domain_of
    from acquisition.crawler.sites import save_site_config

    from acquisition.browser.diagnose import diagnose
    proxy = playwright_proxy_for(url)
    try:
        res = run_off_loop(discover, url, proxy, session_id=session_id, on_progress=on_progress)
    except Exception as e:
        logger.warning(f"discover_api 失败：{e}")
        d = diagnose(error=str(e), url=url)
        return {"summary": {"count": 0, "message": "侦查失败",
                            "why": d["reason"], "how": d["suggestion"]}}
    eps = res.get("endpoints", [])
    cookies = res.get("cookies", "")
    if not eps:
        d = diagnose(body="", url=url)   # 有响应但无 JSON 接口
        return {"summary": {"count": 0, "message": "未发现 JSON 接口",
                            "why": "页面可能纯静态渲染、数据画在 canvas，或被站点盾拦在过盾前",
                            "how": "改用 read_url 抓 HTML 正文；若是盾，开有头浏览器人工过一次再侦查。"}}

    cfg = {"domain": domain_of(url), "page_url": url, "endpoints": eps, "cookies": cookies}
    save_site_config(cfg)
    sessions.report(session_id, on_progress, "config_saved", domain=cfg["domain"], count=len(eps))
    results = [{
        "name": e["name"], "url_base": e["url_base"],
        "gate_headers": list(e.get("gate_headers", {}).keys()),
        "sample_fields": e.get("sample_fields", [])[:12],
    } for e in eps]
    return {"summary": {
        "count": len(eps), "domain": cfg["domain"],
        "message": f"发现 {len(eps)} 个接口，已存配置。之后用 fetch_api(域名, 端点名) 高速复用。",
        "endpoints": results,
    }}


def fetch_api(site: str, endpoint: str = None, params: dict = None,
              session_id: Optional[str] = None,
              on_progress: Optional[Callable[[str, dict], None]] = None) -> dict:
    """逆向 API 复用取数（curl_cffi + cookie + 403 自愈）。"""
    from acquisition.crawler.sites import (
        load_site_config, find_endpoint, save_site_config, domain_of,
    )
    from acquisition.browser.proxy_route import curl_proxy_for

    domain = domain_of(site) or site
    cfg = load_site_config(domain)
    if cfg is None:
        if "://" in site:                          # 传了完整 URL 且未侦查过 → 自动侦查一次
            discover_api(site, session_id=session_id, on_progress=on_progress)
            cfg = load_site_config(domain)
        if cfg is None:
            return {"summary": {"count": 0,
                                "message": f"{domain} 尚未侦查，请先用完整 URL 调 discover_api"}}

    ep = find_endpoint(cfg, endpoint)
    if ep is None:
        names = [e.get("name") for e in cfg.get("endpoints", [])]
        return {"summary": {"count": 0, "message": f"未找到端点「{endpoint}」，可选：{names}"}}

    target = ep.get("url_base") or ep["url"]
    eff_params = params or ep.get("default_params") or None
    curl_proxy = curl_proxy_for(target)

    def on_403():
        """re-discover 刷新该端点的 gate 头 + cookie。返回 {gate_headers, cookies}。"""
        from acquisition.browser.launch import run_off_loop
        from acquisition.crawler.discover import discover
        from acquisition.browser.proxy_route import playwright_proxy_for
        try:
            fresh = run_off_loop(discover, cfg["page_url"], playwright_proxy_for(cfg["page_url"]))
        except Exception:
            return None
        fresh_eps = fresh.get("endpoints", [])
        if not fresh_eps:
            return None
        cfg["endpoints"] = fresh_eps
        cfg["cookies"] = fresh.get("cookies", cfg.get("cookies", ""))
        save_site_config(cfg)
        fe = find_endpoint(cfg, ep.get("name")) or find_endpoint(cfg, None)
        return {"gate_headers": fe.get("gate_headers") if fe else None,
                "cookies": cfg.get("cookies", "")}

    diag: dict = {}
    data = fast_fetch(
        target, ep.get("gate_headers"), proxy=curl_proxy,
        method=ep.get("method", "GET"), params=eff_params,
        cookies=cfg.get("cookies", ""), on_403=on_403, diag=diag,
        session_id=session_id, on_progress=on_progress,
    )
    if data is None:
        from acquisition.browser.diagnose import diagnose
        d = diagnose(status=diag.get("status"), error=diag.get("error"),
                     body=diag.get("body"), url=target)
        return {"summary": {"count": 0, "message": "拉取失败",
                            "why": d["reason"], "how": d["suggestion"]}}
    return {"summary": {"count": 1, "endpoint": ep.get("name"), "domain": domain}, "data": data}


def _looks_like_domain(s: str) -> bool:
    """粗判是不是域名（含点、无空格、无中文）。"""
    s = s.strip()
    return ("." in s) and (" " not in s) and s.isascii()


def _match_endpoint_name(cfg: dict, want: str = None) -> str:
    """按 want 关键词模糊匹配最像的端点名；无 want/无匹配返回 None。"""
    if not want:
        return None
    endpoints = cfg.get("endpoints", [])
    want_l = want.lower()
    best, best_score = None, 0
    for ep in endpoints:
        hay = " ".join([
            ep.get("name", ""), ep.get("url_base", ""),
            " ".join(str(f) for f in ep.get("sample_fields", [])),
            ep.get("sample", ""),
        ]).lower()
        # 简单打分：want 里每个词命中 hay 计 1 分
        score = sum(1 for w in want_l.replace("的", " ").split() if w and w in hay)
        # 整体子串再加权
        if want_l in hay:
            score += 2
        if score > best_score:
            best, best_score = ep.get("name"), score
    return best if best_score > 0 else None


def scrape(url: str, want: str = None, endpoint: str = None, params: dict = None,
           session_id: Optional[str] = None,
           on_progress: Optional[Callable[[str, dict], None]] = None) -> dict:
    """一句话拿网站数据：侦查+取数+cookie 自愈串成一步。见 agents/tools/knowledge_tools.py::scrape 的工具描述。"""
    from acquisition.crawler.sites import load_site_config, domain_of

    # session_id 不传（如 LLM 工具调用）时自动生成，让每次 scrape（含聊天里触发的）
    # 都自动进注册表、自动获得独立浏览器 profile，不局限于走新流式端点手动发起的那次。
    session_id = session_id or sessions.new_session_id()
    sessions.register(session_id, url=url)

    try:
        # 1) 解析目标 URL：完整 URL 直接用；域名补 https://；否则搜索兜底
        page_url = url.strip()
        if "://" not in page_url:
            if _looks_like_domain(page_url):
                page_url = "https://" + page_url
            else:
                # TODO(13.4-2 websearch 迁入后收口)：Scope A 下 websearch 暂留 knowledge_engine，
                # 这是本栈唯一一条容许的临时上行边（函数体内延迟 import，仅 URL 兜底才触发）。
                from knowledge_engine.websearch import web_search as _ws
                hits = _ws(f"{url} {want or ''}".strip(), max_results=3)
                if not hits:
                    sessions.finish(session_id, "error", error="未找到对应网页")
                    return {"summary": {"count": 0, "message": f"没找到「{url}」对应的网页，请直接给我网页 URL"}}
                page_url = hits[0]["url"]

        domain = domain_of(page_url)
        sessions.report(session_id, on_progress, "resolved", domain=domain, page_url=page_url)

        # 2) 站点配置：没有就现场侦查（委托内部 discover_api——正确处理 cookie + 诊断 why/how）
        cfg = load_site_config(domain)
        if cfg is None:
            disc = discover_api(page_url, session_id=session_id, on_progress=on_progress)
            cfg = load_site_config(domain)
            if cfg is None:
                sessions.finish(session_id, "error", error=disc.get("summary", {}).get("message"))
                return disc                          # 侦查失败：把诊断原样返回给模型

        # 3) 接口菜单（带样本，供 MoneyBill 判断）
        menu = [{
            "name": e.get("name"), "url_base": e.get("url_base"),
            "sample_fields": e.get("sample_fields", [])[:12],
            "sample": (e.get("sample") or "")[:300],
        } for e in cfg.get("endpoints", [])]

        # 4) 选端点：显式 endpoint 优先，否则按 want 智能猜
        picked = endpoint or _match_endpoint_name(cfg, want)
        result = {"summary": {
            "domain": domain, "page_url": page_url, "want": want,
            "count": len(menu), "picked": picked,
            "message": f"侦查到 {len(menu)} 个接口" + (f"，picked=「{picked}」已附完整数据" if picked else "，见 endpoints 样本自行判断"),
        }, "endpoints": menu}

        # 5) 取数委托内部 fetch_api（自带 cookie + 403 自愈 + params 覆盖）
        if picked:
            fetched = fetch_api(domain, endpoint=picked, params=params,
                                 session_id=session_id, on_progress=on_progress)
            if "data" in fetched:
                result["data"] = fetched["data"]
            else:                                    # 取数失败：把诊断 why/how 挂上，别静默
                fs = fetched.get("summary", {})
                result["summary"]["fetch_failed"] = fs.get("message")
                if fs.get("why"):
                    result["summary"]["why"] = fs["why"]
                    result["summary"]["how"] = fs["how"]

        sessions.finish(session_id, "done")
        return result
    except Exception as e:  # noqa: BLE001 — 记录会话失败态，再把异常原样抛给调用方
        sessions.finish(session_id, "error", error=str(e)[:300])
        raise


# ════════════════════════════════════════════════════════════════════════
# 浏览器内验证（evaluate_fetch / verify_gate）+ curl_cffi 高速直打（fast_fetch）
# 13.4-2 S6b 从 browser/reverse.py 合并进来：与上面的编排层同属逆向类，合于一处。
# ════════════════════════════════════════════════════════════════════════

# cookie 过期时，部分站返回 200 + "登录失效" body（不是 403），需照样 re-discover 刷 cookie。
# 保守判定：中文明确标记，或 (含 error 字段 + 英文登录失效关键词)，避免误伤正常空数据。
def _looks_auth_failed(data) -> bool:
    try:
        s = json.dumps(data, ensure_ascii=False).lower()[:3000]
    except Exception:
        return False
    if any(m in s for m in ("登录失效", "未登录", "请登录", "登录已过期", "认证失败",
                            "身份验证失败", "token已过期", "会话过期")):
        return True
    if ("error" in s or "code" in s) and any(
        m in s for m in ("unauthorized", "not logged in", "please login", "login required",
                         "token invalid", "invalid token", "session expired", "not authenticated")):
        return True
    return False

# 页面内 evaluate-fetch：整体 try/catch + AbortController 超时（防跨源接口挂死
# 拖到 page.evaluate 的 30s 默认超时），网络错/超时返哨兵 {status:0}（§3.20 第 8 步）
_EVAL_FETCH = """async ([url, headers, timeoutMs, maxBytes]) => {
    const h = Object.assign({'accept': '*/*'}, headers || {});
    const ctrl = new AbortController();
    const timer = setTimeout(() => ctrl.abort(), timeoutMs || 8000);
    try {
        const r = await fetch(url, {headers: h, credentials: 'include', signal: ctrl.signal});
        // 体积封顶：先看 content-length 预判，再按已读文本长度兜底，超限不解析（防大响应撑爆内存）
        const cl = parseInt(r.headers.get('content-length') || '0', 10);
        if (maxBytes && cl && cl > maxBytes) {
            return {status: r.status, json: null, too_large: true, size: cl};
        }
        let txt = '';
        try { txt = await r.text(); } catch (e) { return {status: r.status, json: null}; }
        if (maxBytes && txt.length > maxBytes) {
            return {status: r.status, json: null, too_large: true, size: txt.length};
        }
        let j = null;
        try { j = JSON.parse(txt); } catch (e) {}
        return {status: r.status, json: j};
    } catch (e) {
        return {status: 0, json: null};
    } finally {
        clearTimeout(timer);
    }
}"""


def evaluate_fetch(page, api_url: str, gate_headers: Optional[dict] = None,
                   timeout_ms: int = 8000) -> dict:
    """在浏览器上下文内 fetch api_url，返回 {status, json}（超 50MB 返 too_large）。内置超时。"""
    try:
        return page.evaluate(_EVAL_FETCH, [api_url, gate_headers or {}, timeout_ms, MAX_RESPONSE_BYTES])
    except Exception as e:
        logger.debug(f"evaluate_fetch 异常：{str(e)[:80]}")
        return {"status": 0, "json": None}


def verify_gate(page, api_url: str, gate_headers: Optional[dict] = None) -> dict:
    """
    验证带 gate 头能否解锁端点。返回 {ok, status, gated, json}：
      - ok    : 200 且拿到 JSON
      - gated : 不带头 403/401、带头 200（说明该 gate 头确实是门禁）
    """
    without = evaluate_fetch(page, api_url, None)
    with_ = evaluate_fetch(page, api_url, gate_headers) if gate_headers else without
    ok = with_.get("status") == 200 and with_.get("json") is not None
    gated = (without.get("status") in (401, 403)) and with_.get("status") == 200
    return {
        "ok": ok,
        "gated": gated,
        "status": with_.get("status"),
        "status_without": without.get("status"),
        "json": with_.get("json"),
    }


def fast_fetch(
    api_url: str,
    gate_headers: Optional[dict] = None,
    proxy: Optional[str] = None,
    impersonate: str = "chrome120",
    method: str = "GET",
    params: Optional[dict] = None,
    cookies: Optional[str] = None,
    retries: int = 3,
    on_403: Optional[Callable[[], Optional[dict]]] = None,
    diag: Optional[dict] = None,
    session_id: Optional[str] = None,
    on_progress: Optional[Callable[[str, dict], None]] = None,
) -> Optional[dict]:
    """
    curl_cffi + gate 头 + cookie 直打 API（不开浏览器，快）。

    cookies: 浏览器会话 cookie 串（国内金融站真门禁，如雪球 xq_a_token）。
    on_403: 遇 403 调用（re-discover 刷新），返回 {gate_headers, cookies} 则用之重试一次。
    proxy : 单串代理 url（curl_cffi proxies），None 直连；国内站由 proxy_route 传入快代理。
    diag  : 出参 dict，失败时填 {status, error, body} 供上层诊断反馈（为什么/怎么办）。
    session_id/on_progress: 可选，抓取进度上报（见 acquisition/browser/sessions.py）。
    返回解析后的 JSON dict，失败 None。
    """
    from curl_cffi import requests as cffi

    if diag is None:
        diag = {}

    sessions.report(session_id, on_progress, "fetching", url=api_url)

    headers = dict(gate_headers or {})
    headers.setdefault("accept", "application/json, text/javascript, */*; q=0.01")
    if cookies:
        headers["cookie"] = cookies
    # 显式设 proxies：有则走它；无则空串强制直连，**覆盖环境变量 HTTP(S)_PROXY**
    # （.env 可能残留已弃用的 Clash 127.0.0.1:7897，不覆盖会被 libcurl 读走导致连接失败）
    proxies = {"http": proxy, "https": proxy} if proxy else {"http": "", "https": ""}
    refreshed_once = False

    session = cffi.Session(impersonate=impersonate)

    def _refresh() -> bool:
        """cookie/gate 头失效 → on_403 re-discover 刷新，成功则更新 headers 返回 True。"""
        nonlocal headers, refreshed_once
        if not on_403 or refreshed_once:
            return False
        refreshed_once = True
        fresh = on_403()
        if not fresh:
            return False
        # 兼容旧签名（纯 gate 头 dict）与新签名（{gate_headers, cookies}）
        new_gate = fresh.get("gate_headers", fresh) if isinstance(fresh, dict) else fresh
        new_ck = fresh.get("cookies") if isinstance(fresh, dict) else None
        headers = dict(new_gate or {})
        headers.setdefault("accept", "application/json, text/javascript, */*; q=0.01")
        if new_ck:
            headers["cookie"] = new_ck
        elif cookies:
            headers["cookie"] = cookies
        return True

    try:
        for attempt in range(retries):
            try:
                # bounded_get：流式 + 50MB 上限，杜绝大响应把整份 body 读进内存
                cap = bounded_get(
                    session, api_url, mode="bytes", method=method,
                    headers=headers, params=params, proxies=proxies, timeout=20,
                )
                sc = cap.status
                _body = (cap.data or b"").decode("utf-8", "ignore")[:2000]  # 仅诊断用，已截断
                if sc == 200:
                    if not cap.usable_json:                 # 超上限/被截断 → JSON 不可靠，放弃
                        logger.warning(f"fast_fetch 200 但响应过大(>上限)，已保护放弃：{api_url}")
                        diag.update(status=200, error="响应过大，超过内存上限（已按上限保护）", body="")
                        return None
                    try:
                        j = json.loads(cap.data)
                    except Exception:
                        logger.warning(f"fast_fetch 200 但非 JSON：{api_url}")
                        diag.update(status=200, error="200 但非 JSON", body=_body)
                        return None
                    # 200 但登录态失效（cookie 过期常见）→ re-discover 刷 cookie 重试
                    if not refreshed_once and _looks_auth_failed(j):
                        logger.info(f"fast_fetch 200 但登录态失效，触发 re-discover 刷 cookie…")
                        if _refresh():
                            continue
                        diag.update(status=200, error="登录态失效且刷新失败（可能需人工重新登录）",
                                    body=json.dumps(j, ensure_ascii=False)[:2000])
                        return None
                    sessions.report(session_id, on_progress, "done", url=api_url)
                    return j
                if sc in (401, 403):
                    logger.info(f"fast_fetch {sc}，触发 re-discover 刷新 gate 头 + cookie…")
                    sessions.report(session_id, on_progress, "retry_403", url=api_url)
                    if _refresh():
                        continue
                    diag.update(status=sc, error=f"HTTP {sc}", body=_body)
                    return None
                logger.warning(f"fast_fetch {sc}：{api_url}（尝试 {attempt + 1}）")
                diag.update(status=sc, error=f"HTTP {sc}", body=_body)
            except Exception as e:
                logger.warning(f"fast_fetch 异常：{str(e)[:80]}（尝试 {attempt + 1}）")
                diag.update(status=None, error=str(e), body="")
            time.sleep(min(2 ** attempt, 8))
        return None
    finally:
        try:
            session.close()
        except Exception:
            pass


# ════════════════════════════════════════════════════════════════════════
# 逆向类抓取器门面：与 BaseCrawler 平级（不继承），把上面的模块函数收成实例方法。
# 模块级 discover_api/fetch_api/scrape/fast_fetch 仍是主 API（外部直接 import 复用，
# 签名不变）；类只是给「三类抓取器并列」提供一致的 OO 入口，并承载可调旋钮。
# ════════════════════════════════════════════════════════════════════════


class ReverseApiCrawler:
    """逆向类：按 `sites/` 站点配置调用**已侦查好**的接口（curl_cffi 直打 + cookie 自愈）。

    与 `BaseCrawler` **平级**——不继承（逆向靠浏览器过盾 + curl_cffi 打指纹，套不进
    「一次有上限 HTTP GET」模型），但同样复用 net 原语（fast_fetch 走 `net.bounded_get`）。

    Example:
        >>> rc = ReverseApiCrawler()
        >>> rc.scrape("https://xueqiu.com/S/SH600519", want="行情")
    """

    def __init__(self, *, retries: int = 3, impersonate: str = "chrome120") -> None:
        """
        Args:
            retries: fast_fetch 单次取数的重试次数（403 自愈 + 网络失败）。
            impersonate: curl_cffi 伪装的 TLS 指纹（强反爬源）。
        """
        self.retries = retries
        self.impersonate = impersonate

    def discover_api(self, url: str, session_id: Optional[str] = None,
                     on_progress: Optional[Callable[[str, dict], None]] = None) -> dict:
        """浏览器过盾侦查站点 JSON 接口，存成复用配置。委托模块函数 discover_api。"""
        return discover_api(url, session_id=session_id, on_progress=on_progress)

    def fetch_api(self, site: str, endpoint: Optional[str] = None,
                  params: Optional[dict] = None, session_id: Optional[str] = None,
                  on_progress: Optional[Callable[[str, dict], None]] = None) -> dict:
        """按站点配置 curl_cffi 复用取数（cookie + 403 自愈）。委托模块函数 fetch_api。"""
        return fetch_api(site, endpoint=endpoint, params=params,
                         session_id=session_id, on_progress=on_progress)

    def scrape(self, url: str, want: Optional[str] = None, endpoint: Optional[str] = None,
               params: Optional[dict] = None, session_id: Optional[str] = None,
               on_progress: Optional[Callable[[str, dict], None]] = None) -> dict:
        """一句话拿网站数据：侦查+取数+cookie 自愈串成一步。委托模块函数 scrape。"""
        return scrape(url, want=want, endpoint=endpoint, params=params,
                      session_id=session_id, on_progress=on_progress)

    def fast_fetch(self, api_url: str, gate_headers: Optional[dict] = None,
                   proxy: Optional[str] = None, method: str = "GET",
                   params: Optional[dict] = None, cookies: Optional[str] = None,
                   on_403: Optional[Callable[[], Optional[dict]]] = None,
                   diag: Optional[dict] = None, session_id: Optional[str] = None,
                   on_progress: Optional[Callable[[str, dict], None]] = None) -> Optional[dict]:
        """curl_cffi + gate 头 + cookie 直打 API（用本实例的 retries/impersonate）。"""
        return fast_fetch(api_url, gate_headers, proxy=proxy, impersonate=self.impersonate,
                          method=method, params=params, cookies=cookies, retries=self.retries,
                          on_403=on_403, diag=diag, session_id=session_id, on_progress=on_progress)

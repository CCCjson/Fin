"""
逆向 API 爬取引擎 —— 侦查网站背后的真实数据接口 + 复用取数 + cookie 自愈。

从 knowledge_engine/tools.py 拆出（那个文件原本混着 @tool 注册，被 agents/tools/
knowledge_tools.py 收编成薄适配器后，这里只保留纯引擎逻辑，不再依赖 agents.registry，
也就不存在"引擎层反向依赖工具层"这回事）。

对外由 agents/tools/knowledge_tools.py 包成 scrape 这个 @tool 给 MoneyBill；
discover_api/fetch_api 也被 knowledge_engine/ingest/reverse_api_source.py 摄入源
直接 import 复用。
"""
from typing import Callable, Optional

from loguru import logger

from acquisition.browser import sessions


def discover_api(url: str, session_id: Optional[str] = None,
                  on_progress: Optional[Callable[[str, dict], None]] = None) -> dict:
    """用浏览器过盾嗅探 JSON 接口 + 差分 gate 头 + 抓 cookie，存成站点复用配置（每站只侦查一次）。"""
    from acquisition.config import get_playwright_enabled
    if not get_playwright_enabled():
        return {"summary": {"count": 0,
                            "message": "浏览器爬虫未启用：请在 .env 设 KNOWLEDGE_PLAYWRIGHT_ENABLED=true"}}
    from acquisition.browser.launch import run_off_loop
    from acquisition.browser.discover import discover
    from acquisition.browser.proxy_route import playwright_proxy_for, domain_of
    from acquisition.browser.registry import save_site_config

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
    from acquisition.browser.registry import (
        load_site_config, find_endpoint, save_site_config, domain_of,
    )
    from acquisition.browser.proxy_route import curl_proxy_for
    from acquisition.browser.reverse import fast_fetch

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
        from acquisition.browser.discover import discover
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
    from acquisition.browser.registry import load_site_config, domain_of

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

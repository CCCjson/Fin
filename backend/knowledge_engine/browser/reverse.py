"""
验证 gate 头解锁 + curl_cffi 高速直打（403 自愈）。

蓝本：
  - worldcup2026/scrapers/sofascore_hybrid.py 的 `fetch_api`（page.evaluate 内 fetch + 哨兵）
  - worldcup2026/scrapers/sofascore_api.py 的 `SofaScoreAPI.fetch`（curl_cffi + gate 头 + 403 重嗅）

两层能力：
  1. evaluate_fetch / verify_gate — 在浏览器上下文内 fetch，继承 cookie + 注入 gate 头，
     用于「侦查」阶段确认某端点带上 gate 头能否 200 解锁。
  2. fast_fetch — 侦查完成后，用 curl_cffi + gate 头直打（快 10x），走国内快代理池；
     遇 403 触发 on_403 回调（re-discover 刷新 gate 头）后重试。
"""
import json
import time
from typing import Callable, Optional

from loguru import logger

from knowledge_engine.browser import sessions


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
_EVAL_FETCH = """async ([url, headers, timeoutMs]) => {
    const h = Object.assign({'accept': '*/*'}, headers || {});
    const ctrl = new AbortController();
    const timer = setTimeout(() => ctrl.abort(), timeoutMs || 8000);
    try {
        const r = await fetch(url, {headers: h, credentials: 'include', signal: ctrl.signal});
        let j = null;
        try { j = await r.json(); } catch (e) {}
        return {status: r.status, json: j};
    } catch (e) {
        return {status: 0, json: null};
    } finally {
        clearTimeout(timer);
    }
}"""


def evaluate_fetch(page, api_url: str, gate_headers: Optional[dict] = None,
                   timeout_ms: int = 8000) -> dict:
    """在浏览器上下文内 fetch api_url，返回 {status, json}。内置 AbortController 超时。"""
    try:
        return page.evaluate(_EVAL_FETCH, [api_url, gate_headers or {}, timeout_ms])
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
    session_id/on_progress: 可选，抓取进度上报（见 knowledge_engine/browser/sessions.py）。
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
                resp = session.request(
                    method.upper(), api_url, headers=headers,
                    params=params, proxies=proxies, timeout=20,
                )
                if resp.status_code == 200:
                    try:
                        j = resp.json()
                    except Exception:
                        logger.warning(f"fast_fetch 200 但非 JSON：{api_url}")
                        diag.update(status=200, error="200 但非 JSON",
                                    body=(resp.text or "")[:2000])
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
                if resp.status_code in (401, 403):
                    logger.info(f"fast_fetch {resp.status_code}，触发 re-discover 刷新 gate 头 + cookie…")
                    sessions.report(session_id, on_progress, "retry_403", url=api_url)
                    if _refresh():
                        continue
                    diag.update(status=resp.status_code, error=f"HTTP {resp.status_code}",
                                body=(resp.text or "")[:2000])
                    return None
                logger.warning(f"fast_fetch {resp.status_code}：{api_url}（尝试 {attempt + 1}）")
                diag.update(status=resp.status_code, error=f"HTTP {resp.status_code}",
                            body=(resp.text or "")[:2000])
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

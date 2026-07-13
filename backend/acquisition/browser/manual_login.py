"""
人工登录助手 —— 登录态彻底过期时，弹有头 Chrome 让 Jason 手动登录，抓 cookie 存配置。

场景：cookie 自愈（403/200-登录失效 → re-discover）也刷不出有效 cookie，说明持久化
profile 里的登录本身过期了。此时开有头浏览器，Jason 登一次，登录后 cookie 落盘 + 存进
站点配置，之后 fast_fetch/fetch_api 用新 cookie 恢复高速复用。

★ 必须经 run_off_loop 调（sync Playwright 约束）；需 KNOWLEDGE_PLAYWRIGHT_ENABLED=true。
完成检测：Jason 关窗 / 检测到登录态 / 超时（每 3s 快照 cookie，用登录后的最新快照）。
"""
import time
from datetime import datetime
from typing import Dict, Any

from loguru import logger

from acquisition.browser.launch import BrowserSession
from acquisition.crawler.discover import _capture_cookies
from acquisition.browser.proxy_route import playwright_proxy_for, domain_of
from acquisition.crawler.sites import load_site_config, save_site_config


def manual_login(url: str, timeout_s: int = 300) -> Dict[str, Any]:
    """
    有头浏览器打开 url 让用户手动登录，登录后（关窗/超时）抓 cookie 存进站点配置。
    返回 {domain, cookie_count, cookie_names, saved}。
    """
    dom = domain_of(url)
    proxy = playwright_proxy_for(url)
    last = ""

    logger.info(f"打开浏览器等你登录 {dom} …（登录完成后关闭该窗口，我会自动抓 cookie）")
    with BrowserSession(proxy=proxy, headless=False) as sess:
        try:
            sess.page.goto(url, wait_until="domcontentloaded", timeout=sess.timeout_ms)
        except Exception as e:  # noqa: BLE001
            logger.debug(f"manual_login goto 异常（忽略，继续等登录）：{str(e)[:80]}")

        deadline = time.time() + timeout_s
        while time.time() < deadline:
            try:
                if sess.page.is_closed():           # 用户关了标签页
                    break
                ck = _capture_cookies(sess.page, url)
                if ck:
                    last = ck                        # 留最新快照（登录后覆盖登录前）
            except Exception:                        # 窗口/context 被关 → context 没了
                break
            time.sleep(3)

    if not last:
        logger.warning(f"manual_login 未抓到 {dom} 的 cookie（可能没登录就关了）")
        return {"domain": dom, "cookie_count": 0, "cookie_names": [], "saved": False}

    # 存进站点配置（无则建最小配置，后续 discover_api 再补 endpoints）
    cfg = load_site_config(dom) or {"domain": dom, "page_url": url, "endpoints": []}
    cfg["cookies"] = last
    cfg["cookies_updated_at"] = datetime.now().isoformat(timespec="seconds")
    save_site_config(cfg)

    names = [p.split("=", 1)[0] for p in last.split("; ") if "=" in p]
    logger.info(f"已抓取 {dom} 登录 cookie（{len(names)} 个）并存入配置")
    return {"domain": dom, "cookie_count": len(names), "cookie_names": names[:20], "saved": True}

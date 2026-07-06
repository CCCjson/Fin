"""
逆向 API 浏览器爬虫 — Playwright 无头浏览器过盾 + 嗅探 gate 头 + curl_cffi 直打复用。

蓝本：worldcup2026 三层实现（§3.20）抽成站点无关内核：
  - launch.py   浏览器地基（反检测 + 持久化会话 + 智能升级有头）
  - sniff.py    嗅探页面 XHR/fetch + 差分定位 gate 头
  - reverse.py  验证 gate 头解锁 + curl_cffi 高速直打（403 自愈）
  - registry.py 站点配置持久化（configs/scrapers/<domain>.json + .md）
  - proxy_route 国内快代理池 / 海外 Shadowrocket 按域名路由

discover_api / fetch_api 内核逻辑在 knowledge_engine/reverse_api.py，对外由
agents/tools/knowledge_tools.py 包成 scrape 这个 @tool 给 MoneyBill。
"""
from knowledge_engine.browser.launch import BrowserSession, run_off_loop

__all__ = ["BrowserSession", "run_off_loop"]

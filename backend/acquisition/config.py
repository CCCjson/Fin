"""
acquisition 层配置 —— 爬虫/浏览器/代理路由的出网配置真源。

13.4-2 S6：这 9 个 getter 原住 `knowledge_engine/config.py`，随逆向/探测/浏览器栈
一并收编进 acquisition。分层上它们本就属于「出网取数」而非「RAG/摄入」，留在引擎层
会让 acquisition 反向 import knowledge_engine（违反 engines → acquisition → net）。

沿用项目惯例：用函数每次读 env，避免 dotenv 加载顺序导致模块级常量取不到值。
所有配置项前缀仍为 KNOWLEDGE_（对外行为零改动，只挪了定义位置）。
"""
import os
from pathlib import Path

from dotenv import load_dotenv

load_dotenv(override=True)

# backend/ 目录（本文件在 backend/acquisition/config.py，parent.parent = backend/）
_BACKEND_DIR = Path(__file__).resolve().parent.parent


# ---------- 浏览器爬虫（逆向 API：Playwright 无头浏览器） ----------

def get_playwright_enabled() -> bool:
    """是否启用 Playwright 浏览器爬虫（逆向 API discover/fetch）。默认关，灰度开。"""
    return os.getenv("KNOWLEDGE_PLAYWRIGHT_ENABLED", "false").lower() in ("1", "true", "yes")


def get_playwright_channel() -> str:
    """浏览器渠道：chrome（系统真实 Chrome，反检测更强）/ chromium（Playwright 内置）。"""
    return os.getenv("KNOWLEDGE_PLAYWRIGHT_CHANNEL", "chrome")


def get_playwright_timeout() -> int:
    """浏览器操作超时（毫秒）。"""
    return int(os.getenv("KNOWLEDGE_PLAYWRIGHT_TIMEOUT", "30000"))


def get_playwright_user_data_dir() -> str:
    """持久化浏览器数据目录（cookie/会话落盘，过一次挑战后复用）。"""
    default = str(_BACKEND_DIR / "data" / "browser_profile")
    return os.getenv("KNOWLEDGE_PLAYWRIGHT_USER_DATA_DIR", default)


def get_playwright_headed_on_challenge() -> bool:
    """headless 遇挑战时是否升级有头浏览器让人工过一次（本地 Mac 适用）。默认开。"""
    return os.getenv("KNOWLEDGE_PLAYWRIGHT_HEADED_ON_CHALLENGE", "true").lower() in ("1", "true", "yes")


def get_domestic_domains() -> list[str]:
    """国内域名清单（这些走快代理池 net/proxy_pool，其余走海外出口）。逗号分隔可扩。"""
    default = (
        "xueqiu.com,eastmoney.com,10jqka.com.cn,sina.com.cn,sinajs.cn,"
        "iwencai.com,sse.com.cn,szse.cn,cninfo.com.cn,tushare.pro,"
        "baidu.com,qq.com,tencent.com,163.com,hexun.com,jrj.com.cn"
    )
    raw = os.getenv("KNOWLEDGE_DOMESTIC_DOMAINS", default)
    return [d.strip().lower() for d in raw.split(",") if d.strip()]


def get_scrapers_config_dir() -> str:
    """逆向 API 侦查产出的站点配置目录（<domain>.json + <domain>.md）。

    13.4-2 S6：默认路径随站点配置一并迁入 `acquisition/crawler/sites/`。
    """
    default = str(_BACKEND_DIR / "acquisition" / "crawler" / "sites")
    return os.getenv("KNOWLEDGE_SCRAPERS_CONFIG_DIR", default)


def get_scraper_proxy_enabled() -> bool:
    """国内站爬取是否走快代理池（net/proxy_manager，IP 轮换抗封）。

    **默认开**（2026-07-10 Jason 拍板「严格统一」）：国内抓取绝不降级直连的铁律，
    同样约束逆向爬虫栈。取不到 IP 时 `proxy_route` 抛 `ProxyExhaustedError`，
    不再静默直连。

    显式设为 false = **明示选择直连**（等价于「没配代理」这个唯一合法直连场景），
    不是静默降级。快代理额度耗尽又想继续爬时用它，但你会知道自己在直连。
    """
    return os.getenv("KNOWLEDGE_SCRAPER_PROXY_ENABLED", "true").lower() in ("1", "true", "yes")


def get_proxy_fetch_timeout() -> float:
    """取快代理 IP 的硬超时（秒）。超时即抛 ProxyExhaustedError，绝不无限等。"""
    return float(os.getenv("KNOWLEDGE_PROXY_FETCH_TIMEOUT", "8"))


# ---------- 通用网页检索（websearch：SEC EDGAR 合规 UA） ----------

def get_edgar_ua() -> str:
    """SEC EDGAR 要求带邮箱标识的合规 User-Agent（13.4-2 S7 随 websearch 迁入）。"""
    return os.getenv("KNOWLEDGE_EDGAR_UA", "Fin Research Tool cccjson0828@gmail.com")

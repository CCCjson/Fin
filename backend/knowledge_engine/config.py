"""
外置金融大脑（knowledge_engine）配置 — 统一从环境变量读取。

沿用项目惯例（见 llm_config.py）：用函数每次读 env，避免 dotenv 加载顺序
导致模块级常量取不到值。所有配置项前缀 KNOWLEDGE_。
"""
import os
from pathlib import Path

from dotenv import load_dotenv

load_dotenv(override=True)

# backend/ 目录
_BACKEND_DIR = Path(__file__).resolve().parent.parent
_DEFAULT_DB = _BACKEND_DIR / "data" / "knowledge.db"


def get_db_path() -> str:
    """知识库独立 SQLite 文件路径（与主库 market.db 隔离，避免膨胀拖累主库）。"""
    return os.getenv("KNOWLEDGE_DB_PATH", str(_DEFAULT_DB))


def get_db_url() -> str:
    """知识库 SQLAlchemy URL。"""
    return f"sqlite:///{get_db_path()}"


# ---------- embedding ----------

def get_embed_model() -> str:
    """本地 embedding 模型名（HuggingFace id）。bge-m3 中英双语、维度 1024。"""
    return os.getenv("KNOWLEDGE_EMBED_MODEL", "BAAI/bge-m3")


def get_embed_dim() -> int:
    """embedding 维度。必须与 vec0 建表 float[N] 一致，换模型需同步改 + 重嵌。"""
    return int(os.getenv("KNOWLEDGE_EMBED_DIM", "1024"))


def get_embed_device() -> str:
    """embedding 推理设备：mps(Apple GPU) / cpu / cuda。"""
    return os.getenv("KNOWLEDGE_EMBED_DEVICE", "mps")


def get_embed_batch_size() -> int:
    return int(os.getenv("KNOWLEDGE_EMBED_BATCH_SIZE", "32"))


# ---------- 切片 ----------

def get_chunk_tokens() -> int:
    return int(os.getenv("KNOWLEDGE_CHUNK_TOKENS", "512"))


def get_chunk_overlap() -> int:
    return int(os.getenv("KNOWLEDGE_CHUNK_OVERLAP", "64"))


# ---------- 检索 ----------

def get_rerank_enabled() -> bool:
    return os.getenv("KNOWLEDGE_RERANK", "false").lower() in ("1", "true", "yes")


# ---------- 摄入 / websearch（P1） ----------

def get_search_queries() -> list[str]:
    """摄入用的 query 列表，逗号分隔。"""
    raw = os.getenv("KNOWLEDGE_SEARCH_QUERIES", "")
    return [q.strip() for q in raw.split(",") if q.strip()]


def get_edgar_ua() -> str:
    """SEC EDGAR 要求带邮箱标识的合规 User-Agent。"""
    return os.getenv("KNOWLEDGE_EDGAR_UA", "Fin Research Tool cccjson0828@gmail.com")


def get_scraper_proxy() -> str:
    """可选反爬代理（强反爬源用），形如 http://user:pass@host:port。
    websearch 整体的自适配走全局 `net`（env `HTTP_PROXY_MODE`，默认 auto）。"""
    return os.getenv("KNOWLEDGE_SCRAPER_PROXY", "")


def get_overseas_proxy() -> str:
    """海外出口代理（Shadowrocket，Clash 已弃用）。形如 http://127.0.0.1:1082。
    空 = 直连。websearch/浏览器爬虫访问海外站点时优先走它，
    国内站点不受此影响（走快代理池 net/proxy_pool）。"""
    return os.getenv("KNOWLEDGE_OVERSEAS_PROXY", "")


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
    """逆向 API 侦查产出的站点配置目录（<domain>.json + <domain>.md）。"""
    default = str(_BACKEND_DIR / "configs" / "scrapers")
    return os.getenv("KNOWLEDGE_SCRAPERS_CONFIG_DIR", default)


def get_scraper_proxy_enabled() -> bool:
    """国内站爬取是否走快代理池（net/proxy_pool，IP 轮换抗封）。
    默认关——直连开箱即用、可靠；快代理慢/额度耗尽时不会拖死爬虫。
    需要抗封量抓时再开（且确保 kuaidaili 健康）。"""
    return os.getenv("KNOWLEDGE_SCRAPER_PROXY_ENABLED", "false").lower() in ("1", "true", "yes")


def get_proxy_fetch_timeout() -> float:
    """取快代理 IP 的硬超时（秒）。超时即降级直连，绝不无限等。"""
    return float(os.getenv("KNOWLEDGE_PROXY_FETCH_TIMEOUT", "8"))


# ---------- 定时摄入（KnowledgeScheduler，只摄入不回测） ----------

def get_sched_ingest_enabled() -> bool:
    """是否开启定期自动摄入论文（默认 false——不显式开 + 不配 query 就不自动抓）。"""
    return os.getenv("KNOWLEDGE_SCHED_INGEST_ENABLED", "false").lower() in ("1", "true", "yes")


def get_ingest_cron() -> str:
    """摄入 cron 表达式（标准 5 段），默认每天 07:00。"""
    return os.getenv("KNOWLEDGE_SCHED_INGEST_CRON", "0 7 * * *")


def get_sched_max_docs() -> int:
    """每次定时摄入最多入库篇数。"""
    return int(os.getenv("KNOWLEDGE_SCHED_MAX_DOCS", "10"))

# 注：Alpha Lab 回测开销大，**只在用户明确请求时**触发（API /knowledge/ideas/{id}/backtest），
# 没有任何自动/定时回测开关——故意不提供 auto_backtest 配置。

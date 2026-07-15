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


# 13.4-2 S7：`get_edgar_ua` 随 websearch（sec_edgar）一并迁入 `acquisition/config.py`
# ——它是出网取数的配置，跟着 acquisition/websearch 走。


# 13.4-2：`get_scraper_proxy` 已下沉 `net/overseas.py`（网络层配置不该住在引擎层，
# 否则 acquisition 取代理策略要反向 import knowledge_engine）。`get_overseas_proxy`
# 在这里是与 `net/overseas.py` 逐字重复的**死代码**（零调用方），随之删除。


# 13.4-2 S6：浏览器爬虫/代理路由配置（get_playwright_* / get_domestic_domains /
# get_scrapers_config_dir / get_scraper_proxy_enabled / get_proxy_fetch_timeout）已随
# 逆向/探测/浏览器栈一并迁入 `acquisition/config.py`——它们属于「出网取数」而非
# 「RAG/摄入」，留在引擎层会让 acquisition 反向 import knowledge_engine。调用方改
# `from acquisition.config import ...`（无 shim，与 S2b fetchers 迁移同惯例）。


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

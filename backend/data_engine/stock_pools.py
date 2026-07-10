"""
股票池数据服务 — 指数成分股 / 行业板块成分股（akshare 取数 + 12h 内存缓存）

从 api/routes/stock_pools.py 下沉而来：route 和 MoneyBill 工具（pool_tools/screener）
共用这一份，route 只做 HTTP 封装。
"""
import time
from typing import Any, Dict, List, Tuple

from loguru import logger

from common.market import add_exchange_suffix

# ── 简单内存缓存 ──
_cache: Dict[str, Tuple[Any, float]] = {}
CACHE_TTL = 3600 * 12  # 12 小时


def get_cached(key: str):
    if key in _cache:
        data, ts = _cache[key]
        if time.time() - ts < CACHE_TTL:
            return data
    return None


def set_cached(key: str, data):
    _cache[key] = (data, time.time())


# ── 预设指数池 ──

INDEX_POOLS = {
    "sse50": {"name": "上证50", "index_code": "000016", "description": "上交所 50 只核心蓝筹"},
    "csi300": {"name": "沪深300", "index_code": "000300", "description": "沪深两市 300 只大盘股"},
    "csi500": {"name": "中证500", "index_code": "000905", "description": "中盘成长股 500 只"},
}


def fetch_index_constituents(index_code: str) -> List[Dict[str, str]]:
    """获取指数成分股（同步，在线程池中运行）"""
    import akshare as ak
    from net import domestic_akshare

    try:
        df = domestic_akshare(ak.index_stock_cons, symbol=index_code)
        if df is None or df.empty:
            return []

        # 自适应列名
        code_col = None
        name_col = None
        for col in df.columns:
            if "代码" in col or "code" in col.lower():
                code_col = col
            if "名称" in col or "name" in col.lower():
                name_col = col
        if not code_col:
            code_col = df.columns[0]
        if not name_col and len(df.columns) > 1:
            name_col = df.columns[1]

        result = []
        for _, row in df.iterrows():
            code = str(row[code_col]).strip()
            name = str(row[name_col]).strip() if name_col else ""
            try:
                symbol = add_exchange_suffix(code)
            except ValueError as e:
                # 成分股里不该有 ETF/非法码。丢这一行，别让异常炸掉整批，
                # 也别像旧版那样静默返回无后缀裸码往下游漂。
                logger.warning(f"指数 {index_code} 成分股代码异常，跳过: {e}")
                continue
            result.append({"symbol": symbol, "name": name})

        return result
    except Exception as e:
        logger.error(f"获取指数 {index_code} 成分股失败: {e}")
        return []


def fetch_industry_list() -> List[Dict[str, str]]:
    """获取行业板块列表"""
    import akshare as ak
    from net import domestic_akshare

    try:
        df = domestic_akshare(ak.stock_board_industry_name_em)
        if df is None or df.empty:
            return []

        name_col = None
        for col in df.columns:
            if "板块名称" in col or "名称" in col:
                name_col = col
                break
        if not name_col:
            name_col = df.columns[1] if len(df.columns) > 1 else df.columns[0]

        return [{"name": str(row[name_col]).strip()} for _, row in df.iterrows()]
    except Exception as e:
        logger.error(f"获取行业板块列表失败: {e}")
        return []


def fetch_industry_stocks(name: str) -> List[Dict[str, str]]:
    """获取行业板块成分股"""
    import akshare as ak
    from net import domestic_akshare

    try:
        df = domestic_akshare(ak.stock_board_industry_cons_em, symbol=name)
        if df is None or df.empty:
            return []

        code_col = None
        name_col = None
        for col in df.columns:
            if "代码" in col:
                code_col = col
            if "名称" in col:
                name_col = col
        if not code_col:
            code_col = df.columns[0]
        if not name_col and len(df.columns) > 1:
            name_col = df.columns[1]

        result = []
        for _, row in df.iterrows():
            code = str(row[code_col]).strip()
            stock_name = str(row[name_col]).strip() if name_col else ""
            try:
                symbol = add_exchange_suffix(code)
            except ValueError as e:
                logger.warning(f"行业 {name} 成分股代码异常，跳过: {e}")
                continue
            result.append({"symbol": symbol, "name": stock_name})
        return result
    except Exception as e:
        logger.error(f"获取行业 {name} 成分股失败: {e}")
        return []

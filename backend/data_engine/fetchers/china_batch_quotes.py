"""
腾讯 / 新浪批量行情接口 — 东财实时行情的备源

- 腾讯 qt.gtimg.cn：字段最全（价格/涨跌/量额/换手/PE/PB/市值），~500 只/请求，主备源
- 新浪 hq.sinajs.cn：需要 Referer 头，~800 只/请求，无 PE/PB/市值，次备源
- 两者都容忍批量轮询、直连即可（也支持传入代理），且不依赖 akshare 全局锁

返回的行情 dict 与 realtime._parse_items 的字段形状保持一致，
可与东财结果按 symbol 直接合并。
"""
import time
from typing import Any, Dict, List, Optional

from loguru import logger

TENCENT_URL = "https://qt.gtimg.cn/q="
SINA_URL = "https://hq.sinajs.cn/list="

TENCENT_BATCH_SIZE = 500
SINA_BATCH_SIZE = 800


def _make_session(proxies: Optional[Dict[str, str]] = None):
    from net import make_domestic_session
    return make_domestic_session(proxies)


def _to_prefixed(symbol: str) -> Optional[str]:
    """"600519.SH" -> "sh600519"；非沪深返回 None"""
    if "." not in symbol:
        return None
    code, suffix = symbol.split(".", 1)
    suffix = suffix.upper()
    if suffix == "SH":
        return f"sh{code}"
    if suffix == "SZ":
        return f"sz{code}"
    return None


def _num(v, cast=float, default=None):
    if v is None or v == "" or v == "-":
        return default
    try:
        return cast(v)
    except (TypeError, ValueError):
        return default


def _empty_row(symbol: str) -> Dict[str, Any]:
    """与 realtime._parse_items 相同 key 的空行模板"""
    code, suffix = symbol.split(".", 1)
    return {
        "price": None, "change_pct": None, "change_amount": None,
        "volume": None, "amount": None, "amplitude": None,
        "turnover": None, "pe_ratio": None, "code": code,
        "market_id": 1 if suffix.upper() == "SH" else 0,
        "name": None, "high": None, "low": None, "open": None,
        "prev_close": None, "total_mv": None, "circ_mv": None,
        "pb_ratio": None, "pe_ttm": None, "symbol": symbol,
    }


def _parse_tencent_line(line: str) -> Optional[Dict[str, Any]]:
    """解析单行腾讯行情: v_sh600519="1~贵州茅台~600519~价格~昨收~今开~..." """
    if '="' not in line:
        return None
    head, body = line.split('="', 1)
    prefixed = head.rsplit("v_", 1)[-1].strip()
    fields = body.rstrip('";').split("~")
    if len(fields) < 47 or len(prefixed) < 8:
        return None

    code = prefixed[2:]
    suffix = "SH" if prefixed.startswith("sh") else "SZ"
    row = _empty_row(f"{code}.{suffix}")

    price = _num(fields[3])
    row.update({
        "name": fields[1] or None,
        "price": price if price and price > 0 else None,
        "prev_close": _num(fields[4]),
        "open": _num(fields[5]),
        "change_amount": _num(fields[31]),
        "change_pct": _num(fields[32]),
        "high": _num(fields[33]),
        "low": _num(fields[34]),
        "volume": _num(fields[36]),                        # 手，与东财 f5 一致
        "amount": _num(fields[37], lambda v: float(v) * 1e4),   # 万元 → 元
        "turnover": _num(fields[38]),
        "pe_ttm": _num(fields[39]),
        "amplitude": _num(fields[43]),
        "circ_mv": _num(fields[44], lambda v: float(v) * 1e8),  # 亿元 → 元
        "total_mv": _num(fields[45], lambda v: float(v) * 1e8),
        "pb_ratio": _num(fields[46]),
    })
    return row


def _parse_sina_line(line: str) -> Optional[Dict[str, Any]]:
    """解析单行新浪行情: var hq_str_sh600519="贵州茅台,开,昨收,现价,高,低,..." """
    if '="' not in line:
        return None
    head, body = line.split('="', 1)
    prefixed = head.rsplit("hq_str_", 1)[-1].strip()
    fields = body.rstrip('";').split(",")
    if len(fields) < 10 or len(prefixed) < 8:
        return None

    code = prefixed[2:]
    suffix = "SH" if prefixed.startswith("sh") else "SZ"
    row = _empty_row(f"{code}.{suffix}")

    price = _num(fields[3])
    prev_close = _num(fields[2])
    change_amount = None
    change_pct = None
    if price and price > 0 and prev_close and prev_close > 0:
        change_amount = round(price - prev_close, 4)
        change_pct = round((price - prev_close) / prev_close * 100, 2)

    row.update({
        "name": fields[0] or None,
        "open": _num(fields[1]),
        "prev_close": prev_close,
        "price": price if price and price > 0 else None,
        "high": _num(fields[4]),
        "low": _num(fields[5]),
        "volume": _num(fields[8], lambda v: float(v) / 100),  # 股 → 手
        "amount": _num(fields[9]),                             # 元
        "change_amount": change_amount,
        "change_pct": change_pct,
    })
    return row


def fetch_quotes_tencent(
    symbols: List[str],
    batch_size: int = TENCENT_BATCH_SIZE,
    proxies: Optional[Dict[str, str]] = None,
) -> List[Dict[str, Any]]:
    """腾讯批量行情，~500 只/请求"""
    prefixed = [p for p in (_to_prefixed(s) for s in symbols) if p]
    if not prefixed:
        return []

    result: List[Dict[str, Any]] = []
    session = _make_session(proxies)
    try:
        for i in range(0, len(prefixed), batch_size):
            batch = prefixed[i:i + batch_size]
            try:
                resp = session.get(TENCENT_URL + ",".join(batch), timeout=8)
                resp.encoding = "gbk"
                for line in resp.text.splitlines():
                    row = _parse_tencent_line(line)
                    if row:
                        result.append(row)
            except Exception as e:
                logger.warning(f"腾讯行情批次 {i // batch_size + 1} 失败: {e}")
            time.sleep(0.1)
    finally:
        session.close()

    logger.info(f"腾讯批量行情: {len(result)}/{len(prefixed)} 只")
    return result


def fetch_quotes_sina(
    symbols: List[str],
    batch_size: int = SINA_BATCH_SIZE,
    proxies: Optional[Dict[str, str]] = None,
) -> List[Dict[str, Any]]:
    """新浪批量行情，~800 只/请求，需要 Referer 头"""
    prefixed = [p for p in (_to_prefixed(s) for s in symbols) if p]
    if not prefixed:
        return []

    headers = {"Referer": "https://finance.sina.com.cn"}
    result: List[Dict[str, Any]] = []
    session = _make_session(proxies)
    try:
        for i in range(0, len(prefixed), batch_size):
            batch = prefixed[i:i + batch_size]
            try:
                resp = session.get(SINA_URL + ",".join(batch), headers=headers, timeout=8)
                resp.encoding = "gbk"
                for line in resp.text.splitlines():
                    row = _parse_sina_line(line)
                    if row:
                        result.append(row)
            except Exception as e:
                logger.warning(f"新浪行情批次 {i // batch_size + 1} 失败: {e}")
            time.sleep(0.1)
    finally:
        session.close()

    logger.info(f"新浪批量行情: {len(result)}/{len(prefixed)} 只")
    return result


def _load_active_symbols() -> List[str]:
    """从 StockInfo 加载活跃 A 股 symbol 列表（备源全市场兜底用）"""
    try:
        from data_engine.storage.database import get_session
        from data_engine.storage.models import StockInfo
        session = get_session()
        try:
            rows = session.query(StockInfo.symbol).filter(
                StockInfo.market == "a_share",
                StockInfo.is_active == 1,
            ).all()
            return [r[0] for r in rows]
        finally:
            session.close()
    except Exception as e:
        logger.warning(f"加载股票列表失败: {e}")
        return []


def fetch_a_share_realtime_via_fallback(
    exclude: Optional[set] = None,
) -> List[Dict[str, Any]]:
    """全市场行情兜底：腾讯为主，腾讯缺的再走新浪。

    Args:
        exclude: 已从东财拿到的 symbol 集合（跳过不重复拉）
    """
    symbols = _load_active_symbols()
    if exclude:
        symbols = [s for s in symbols if s not in exclude]
    if not symbols:
        return []

    logger.info(f"启用腾讯/新浪备源，待补 {len(symbols)} 只")
    rows = fetch_quotes_tencent(symbols)
    got = {r["symbol"] for r in rows}

    missing = [s for s in symbols if s not in got]
    if missing:
        rows.extend(fetch_quotes_sina(missing))

    return rows

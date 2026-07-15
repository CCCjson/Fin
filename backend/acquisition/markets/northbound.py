"""
北向资金（沪深股通）—— 实时净流入 / 近 10 日历史 / 季度持仓 Top20。

东财 kamt / kamt.kline / datacenter-web 三端点，走 `net.domestic_json`（铁律：
快代理→轮换重试，绝不静默直连；prefer_direct 第 0 轮直连省额度，低频报告接口用）。

13.4-2 S7d：从 report_engine/web_searcher.py 迁入（原为裸 requests，此处收口铁律）。
金额单位统一为万元（季度持仓的市值为元）。
"""
import time

from loguru import logger

_EM_UT = "bd1d9ddb04089700cf9c27f6f7426281"


def fetch_northbound_realtime() -> list[dict]:
    """今日北向资金实时净流入（沪股通+深股通），返回单元素列表（失败空列表）。"""
    from net import domestic_json

    data = domestic_json(
        "https://push2.eastmoney.com/api/qt/kamt/get",
        params={"fields1": "f1,f2,f3,f4", "fields2": "f51,f52,f53,f54,f55,f56",
                "ut": _EM_UT, "_": str(int(time.time() * 1000))},
        timeout=15, prefer_direct=True,
    )
    d = (data or {}).get("data") or {}
    if not d:
        return []

    hk2sh, hk2sz = d.get("hk2sh", {}), d.get("hk2sz", {})
    sh2hk, sz2hk = d.get("sh2hk", {}), d.get("sz2hk", {})
    sh_net = hk2sh.get("dayNetAmtIn", 0) or 0
    sz_net = hk2sz.get("dayNetAmtIn", 0) or 0
    nb_total = sh_net + sz_net
    sb_total = (sh2hk.get("dayNetAmtIn", 0) or 0) + (sz2hk.get("dayNetAmtIn", 0) or 0)

    logger.info(f"北向资金实时: 沪{sh_net:.0f}万 深{sz_net:.0f}万 合计{nb_total:.0f}万")
    return [{
        "hk_to_sh": sh_net, "hk_to_sz": sz_net,
        "northbound_total": nb_total, "southbound_total": sb_total,
        "date": hk2sh.get("date2", ""),
        "sh_remain": hk2sh.get("dayAmtRemain", 0), "sz_remain": hk2sz.get("dayAmtRemain", 0),
        "sh_threshold": hk2sh.get("dayAmtThreshold", 0), "sz_threshold": hk2sz.get("dayAmtThreshold", 0),
    }]


def fetch_northbound_history() -> list[dict]:
    """近 10 日北向资金历史净流入（以 s2n 北向合计为基准，附沪/深股通明细）。"""
    from net import domestic_json

    data = domestic_json(
        "https://push2his.eastmoney.com/api/qt/kamt.kline/get",
        params={"fields1": "f1,f2,f3,f4,f5", "fields2": "f51,f52,f53,f54,f55,f56",
                "klt": "101", "lmt": "10", "end": "20500101",
                "ut": _EM_UT, "_": str(int(time.time() * 1000))},
        timeout=15, prefer_direct=True,
    )
    d = (data or {}).get("data") or {}
    s2n_lines = d.get("s2n", [])
    if not s2n_lines:
        return []

    def _parse_val(s: object) -> float | None:
        if s in ("-", "", None):
            return None
        try:
            return float(s)  # type: ignore[arg-type]
        except (ValueError, TypeError):
            return None

    def _to_map(lines: list) -> dict:
        m: dict = {}
        for line in lines:
            if isinstance(line, str):
                parts = line.split(",")
                if len(parts) >= 2:
                    m[parts[0]] = _parse_val(parts[1])
        return m

    sh_map, sz_map = _to_map(d.get("hk2sh", [])), _to_map(d.get("hk2sz", []))

    history = []
    for line in s2n_lines:
        if not isinstance(line, str):
            continue
        parts = line.split(",")
        if len(parts) < 4:
            continue
        dt = parts[0]
        history.append({
            "date": dt, "hk_to_sh": sh_map.get(dt), "hk_to_sz": sz_map.get(dt),
            "northbound_total": _parse_val(parts[1]),
            "cumulative_total": _parse_val(parts[3]),
        })
    logger.info(f"北向资金历史: {len(history)} 日")
    return history


def fetch_northbound_top_stocks() -> list[dict]:
    """北向资金季度持仓 Top20（按持股市值排序）。

    2024-08-19 起港交所将北向持仓改为每季度披露，用 RPT_MUTUAL_HOLDSTOCKNORTH_STA。
    """
    from net import domestic_json

    data = domestic_json(
        "https://datacenter-web.eastmoney.com/api/data/v1/get",
        params={
            "sortColumns": "HOLD_MARKET_CAP", "sortTypes": "-1",
            "pageSize": "20", "pageNumber": "1",
            "reportName": "RPT_MUTUAL_HOLDSTOCKNORTH_STA",
            "columns": "SECURITY_CODE,SECURITY_NAME,SECUCODE,CLOSE_PRICE,CHANGE_RATE,"
                       "HOLD_SHARES,HOLD_MARKET_CAP,A_SHARES_RATIO,"
                       "FREE_SHARES_RATIO,TOTAL_SHARES_RATIO,TRADE_DATE",
            "source": "WEB", "client": "WEB", "_": str(int(time.time() * 1000)),
        },
        timeout=15, prefer_direct=True,
    )
    result_data = (data or {}).get("result")
    items = (result_data or {}).get("data", []) if result_data else []
    if not items:
        return []

    trade_date = items[0].get("TRADE_DATE", "")[:10] if items else ""
    results = [{
        "name": it.get("SECURITY_NAME", ""), "code": it.get("SECURITY_CODE", ""),
        "secucode": it.get("SECUCODE", ""), "close": it.get("CLOSE_PRICE"),
        "change_pct": it.get("CHANGE_RATE"),
        "hold_shares": it.get("HOLD_SHARES"), "hold_market_cap": it.get("HOLD_MARKET_CAP"),
        "a_shares_ratio": it.get("A_SHARES_RATIO"), "free_shares_ratio": it.get("FREE_SHARES_RATIO"),
        "total_shares_ratio": it.get("TOTAL_SHARES_RATIO"), "trade_date": trade_date,
    } for it in items]
    logger.info(f"北向资金季度持仓 Top{len(results)} ({trade_date})")
    return results

"""
六大指数近 10 日 K 线 + 汇总统计（上证/深成/创业板/沪深300/中证500/科创50）。

东财 push2his kline 端点（复用 eastmoney_crawler 的 HIST_API + parse_kline_data），
走 `net.domestic_json`（铁律：快代理→轮换重试，绝不静默直连；prefer_direct 第 0 轮
直连省额度）。手写的「连续失败换代理」逻辑由 domestic_json 内部 _rotate 接管，删除。

13.4-2 S7d：从 report_engine/web_searcher.py 迁入。
"""
import time

from loguru import logger

_INDEX_SECIDS = {
    "上证指数": "1.000001", "深证成指": "0.399001", "创业板指": "0.399006",
    "沪深300": "1.000300", "中证500": "1.000905", "科创50": "1.000688",
}


def fetch_index_klines() -> list[dict]:
    """六大指数近 10 日 K 线 + 汇总统计。返回 [{name, secid, klines, stats}]。"""
    from acquisition.markets.eastmoney_crawler import EastMoneyCrawler, parse_kline_data
    from net import domestic_json

    results: list[dict] = []
    for name, secid in _INDEX_SECIDS.items():
        params = {
            "fields1": "f1,f2,f3,f4,f5,f6",
            "fields2": "f51,f52,f53,f54,f55,f56,f57,f58,f59,f60,f61",
            "ut": "7eea3edcaed734bea9cbfc24409ed989",
            "klt": "101", "fqt": "1", "secid": secid, "lmt": "10", "end": "20500101",
            "_": str(int(time.time() * 1000)),
        }
        try:
            raw = domestic_json(EastMoneyCrawler.HIST_API, params=params,
                                timeout=15, prefer_direct=True)
            data = (raw or {}).get("data")
            if not data:
                logger.warning(f"指数K线 {name} 无数据")
                continue
            klines = parse_kline_data(data)
            if not klines:
                continue
        except Exception as e:  # noqa: BLE001 — 单指数失败跳过，不拖垮其它
            logger.warning(f"获取指数K线 {name} 失败: {e}")
            continue

        closes = [k["close"] for k in klines]
        highs = [k["high"] for k in klines]
        lows = [k["low"] for k in klines]
        amounts = [k["amount"] for k in klines]
        recent = amounts[-5:] if len(amounts) >= 5 else amounts
        avg_amount_5d = sum(recent) / len(recent) if recent else 0

        results.append({
            "name": name, "secid": secid, "klines": klines,
            "stats": {
                "high_10d": max(highs) if highs else None,
                "low_10d": min(lows) if lows else None,
                "avg_close_10d": round(sum(closes) / len(closes), 2) if closes else None,
                "avg_amount_5d": avg_amount_5d,
                "latest_amount": amounts[-1] if amounts else None,
                "volume_vs_avg": round(amounts[-1] / avg_amount_5d * 100, 1) if avg_amount_5d and amounts else None,
            },
        })
        logger.debug(f"指数K线 {name}: {len(klines)} 日")

    logger.info(f"获取指数K线成功: {len(results)} 个指数")
    return results

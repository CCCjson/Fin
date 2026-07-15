"""
yfinance 批量实时行情助手 — 美股/港股共用

用一次 yf.download(tickers=[...]) 多线程批量调用替代逐只 ticker.info
（.info 每只要打 3-4 个重接口、走 Clash 各 2-4s；批量 download 一次搞定）。
缺失/异常的 symbol 用 fast_info 逐只兜底（远轻于 .info）。

返回 dict 形状与原 fetch_realtime 完全一致：
{symbol, price, change, change_percent, volume, timestamp}
"""
from datetime import datetime
from typing import Dict, List

from loguru import logger


def _row_from_closes(symbol: str, closes, volumes) -> Dict:
    """由最近两根日线收盘价计算行情行"""
    price = float(closes.iloc[-1])
    if len(closes) >= 2:
        prev = float(closes.iloc[-2])
        change = round(price - prev, 4)
        change_percent = round((price - prev) / prev * 100, 4) if prev else 0
    else:
        change = 0
        change_percent = 0
    volume = 0
    if volumes is not None and len(volumes):
        try:
            volume = int(volumes.iloc[-1])
        except (TypeError, ValueError):
            volume = 0
    return {
        "symbol": symbol,
        "price": round(price, 4),
        "change": change,
        "change_percent": change_percent,
        "volume": volume,
        "timestamp": datetime.now().isoformat(),
    }


def _fast_info_fallback(symbol: str) -> Dict:
    """单只兜底：fast_info（轻量，1 个请求，非 .info 的 3-4 个）"""
    import yfinance as yf

    fi = yf.Ticker(symbol).fast_info
    price = float(fi["last_price"])
    prev = float(fi["previous_close"] or 0)
    change = round(price - prev, 4) if prev else 0
    change_percent = round((price - prev) / prev * 100, 4) if prev else 0
    try:
        volume = int(fi["last_volume"] or 0)
    except (KeyError, TypeError, ValueError):
        volume = 0
    return {
        "symbol": symbol,
        "price": round(price, 4),
        "change": change,
        "change_percent": change_percent,
        "volume": volume,
        "timestamp": datetime.now().isoformat(),
    }


def fetch_yf_realtime_batch(symbols: List[str]) -> List[Dict]:
    """批量获取 yfinance 实时行情（一次 download + fast_info 兜底）"""
    import yfinance as yf

    if not symbols:
        return []

    result: List[Dict] = []
    done: set = set()

    try:
        df = yf.download(
            tickers=symbols,
            period="5d",
            interval="1d",
            group_by="ticker",
            threads=True,
            auto_adjust=False,
            progress=False,
        )
        if df is not None and not df.empty:
            for symbol in symbols:
                try:
                    # 多 ticker 时列是 (symbol, field) 两级；单 ticker 是单级
                    sub = df[symbol] if len(symbols) > 1 else df
                    closes = sub["Close"].dropna()
                    if closes.empty:
                        continue
                    volumes = sub["Volume"].dropna() if "Volume" in sub else None
                    result.append(_row_from_closes(symbol, closes, volumes))
                    done.add(symbol)
                except (KeyError, IndexError, TypeError):
                    continue
    except Exception as e:
        logger.warning(f"yfinance 批量下载失败，全部走 fast_info 兜底: {e}")

    # 兜底缺失的 symbol
    for symbol in symbols:
        if symbol in done:
            continue
        try:
            result.append(_fast_info_fallback(symbol))
        except Exception as e:
            logger.warning(f"获取 {symbol} 实时行情失败: {e}")

    logger.info(f"yfinance 批量实时行情: {len(result)}/{len(symbols)} 只")
    return result


# ── 海外日线历史（深度回补用）─────────────────────────────────────────────
# 13.4-2 S8a：从 data_engine/deep_history/overseas_job.py 下沉——引擎层不该直接
# import yfinance 出网。出网仍走 yfinance（海外代理策略待后续单独处理），但调用点
# 收进 acquisition，引擎层改调这两个门面。返回原始 DataFrame，编排/落库留调用方。

def download_daily_history(yf_symbols: List[str], start: str):
    """批量下载海外日线历史（yf.download），返回原始 DataFrame。"""
    import yfinance as yf
    return yf.download(
        tickers=yf_symbols, start=start, interval="1d",
        group_by="ticker", threads=True, auto_adjust=False, progress=False,
    )


def fetch_daily_history(yf_sym: str, start: str):
    """单只海外日线历史兜底（yf.Ticker().history()），返回去空后的 DataFrame（或 None）。"""
    import yfinance as yf
    df = yf.Ticker(yf_sym).history(start=start, interval="1d", auto_adjust=False)
    return df.dropna(how="all") if df is not None else None

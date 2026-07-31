"""
yfinance 批量实时行情助手 — 美股/港股共用

用一次 yf.download(tickers=[...]) 多线程批量调用替代逐只 ticker.info
（.info 每只要打 3-4 个重接口、走 Clash 各 2-4s；批量 download 一次搞定）。
缺失/异常的 symbol 用 fast_info 逐只兜底（远轻于 .info）。

返回 dict 形状与原 fetch_realtime 完全一致：
{symbol, price, change, change_percent, volume, timestamp}
"""
import threading
from contextlib import contextmanager
from datetime import datetime
from typing import Dict, List

from loguru import logger

_yf_proxy_configured = False

# ── Yahoo 跨 job 互斥锁 ────────────────────────────────────────────────────
# 打 Yahoo 的**长任务**有两个：深历史回补（deep_history/overseas_job.py）和每日增量
# （data_engine/overseas_daily_updater.py）。两者各有各的锁，但**互不认识** ——
# 同时跑就是两倍请求量砸向 Yahoo，两边都可能被限速（overseas_job 自己的注释早就
# 点破过这个风险：「hk/us 共用一个单例，避免两个方向同时叠加 Yahoo 限速风险」，
# 只是那个单例管不到别的 job）。
#
# 锁放在 yf_batch 是因为它是**唯一的 Yahoo 出网门面**，两个 job 本来就都 import 它，
# 不引入新的依赖方向（engines → acquisition，合规）。
#
# 粒度是**整个 job 跑一趟**，不是单次请求 —— 单请求粒度只会让两个 job 交替轰炸，
# 该防的没防住。
_JOB_LOCK = threading.Lock()


@contextmanager
def yahoo_job_lock(label: str, blocking: bool = False):
    """抢 Yahoo 长任务的互斥锁。`with yahoo_job_lock("每日增量") as ok:` 判 ok。

    默认 **非阻塞**：抢不到就让调用方自己决定跳过（定时任务宁可跳过本次等下一轮，
    也不要堆在这儿等半小时——等到了数据也过时了，还占着线程）。
    """
    acquired = _JOB_LOCK.acquire(blocking=blocking)
    if not acquired:
        logger.warning(f"[Yahoo 互斥] {label} 抢锁失败：另一个 Yahoo 长任务正在跑，本次跳过")
    try:
        yield acquired
    finally:
        if acquired:
            _JOB_LOCK.release()


def configure_yf_proxy() -> None:
    """按 net.overseas 策略给 yfinance 注入海外代理（进程级一次，幂等）。

    13.4-2 债1：yfinance 1.x 出网底层是 curl_cffi 单例（YfData），代理只在首次建
    session 时从 YfConfig.network.proxy 读一次——必须赶在任何 yf 调用前设好，故每个
    yf 出网入口先调本函数。本仓库 yfinance 只服务海外（美股/港股/纳指），全局代理=
    海外代理无冲突。resolve_overseas_proxy() 返 None（auto 探到可直连 / 显式 direct）
    时保持 yfinance 默认直连。配置失败不阻断取数（退回默认直连）。
    """
    global _yf_proxy_configured
    if _yf_proxy_configured:
        return
    try:
        from yfinance.config import YfConfig

        from acquisition.channels import resolve_overseas_proxy
        proxy = resolve_overseas_proxy()
        if proxy:
            # YfConfig.network.proxy 直接赋给 curl_cffi session.proxies，用 dict 形态
            YfConfig.network.proxy = {"http": proxy, "https": proxy}
        _yf_proxy_configured = True
    except Exception as e:  # noqa: BLE001
        logger.debug(f"configure_yf_proxy 跳过（退回默认直连）: {e}")


def _row_from_closes(symbol: str, closes, volumes) -> Dict:
    """由最近两根日线收盘价计算行情行。

    🔴 **缺数据留 None，不用 0 顶替**（S6 复审）。此前 `change`/`change_percent`/
    `volume` 在算不出来时一律写 0，而 S6 之后这些行会直接喂给 `price_alert_monitor`
    和 `position_guardian` —— 一个看起来像真数据的 `change_percent=0` 会让
    `pct_change` 预警**恒不触发且看不出来**，比返回 None（下游还能判）更坏。
    同 `quote_router._canonical` 的口径，那里的 docstring 讲的是同一件事。
    """
    price = float(closes.iloc[-1])
    if len(closes) >= 2:
        prev = float(closes.iloc[-2])
        change = round(price - prev, 4)
        change_percent = round((price - prev) / prev * 100, 4) if prev else None
        prev_close: float | None = round(prev, 4)
    else:
        change = None
        change_percent = None
        prev_close = None
    volume = None
    if volumes is not None and len(volumes):
        try:
            volume = int(volumes.iloc[-1])
        except (TypeError, ValueError):
            volume = None
    return {
        "symbol": symbol,
        "price": round(price, 4),
        "change": change,
        "change_percent": change_percent,
        "prev_close": prev_close,
        "volume": volume,
        "timestamp": datetime.now().isoformat(),
    }


def _fast_info_fallback(symbol: str) -> Dict:
    """单只兜底：fast_info（轻量，1 个请求，非 .info 的 3-4 个）"""
    import yfinance as yf

    fi = yf.Ticker(symbol).fast_info
    price = float(fi["last_price"])
    prev = float(fi["previous_close"] or 0)
    # 同 `_row_from_closes`：拿不到昨收就是拿不到，⛔ 不许写 0 装作「今天平盘」。
    change = round(price - prev, 4) if prev else None
    change_percent = round((price - prev) / prev * 100, 4) if prev else None
    try:
        volume = int(fi["last_volume"] or 0)
    except (KeyError, TypeError, ValueError):
        volume = None
    return {
        "symbol": symbol,
        "price": round(price, 4),
        "change": change,
        "change_percent": change_percent,
        "prev_close": round(prev, 4) if prev else None,
        "volume": volume,
        "timestamp": datetime.now().isoformat(),
    }


def fetch_yf_realtime_batch(symbols: List[str]) -> List[Dict]:
    """批量获取 yfinance 实时行情（一次 download + fast_info 兜底）"""
    import yfinance as yf

    if not symbols:
        return []

    configure_yf_proxy()

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
# import yfinance 出网。调用点收进 acquisition，引擎层改调这两个门面。海外代理已按
# net.overseas 策略经 configure_yf_proxy() 注入（13.4-2 债1）。返回原始 DataFrame，
# 编排/落库留调用方。

def download_daily_history(yf_symbols: List[str], start: str):
    """批量下载海外日线历史（yf.download），返回原始 DataFrame。"""
    import yfinance as yf
    configure_yf_proxy()
    return yf.download(
        tickers=yf_symbols, start=start, interval="1d",
        group_by="ticker", threads=True, auto_adjust=False, progress=False,
    )


def download_daily_range(yf_symbols: List[str], start: str, end: str):
    """批量下载海外日线的**指定区间**（补洞用），返回原始 DataFrame。

    与 `download_daily_history` 的区别只有一个 `end`，但这个区别很值钱：增量场景
    永远拉到今天，所以那个函数不接右边界；**补洞不接右边界就会白拉一大段** ——
    补一个 3 周前的洞，每批都会顺带把 3 周到今天全下一遍 × 上万只票。

    ⚠️ yfinance 的 `end` 是**左闭右开**的，要拿到 end 当天那根必须传 end+1 天。
    这个 +1 由调用方负责（`data_engine/gap_fill.py` 里做了），本函数原样透传。
    """
    import yfinance as yf
    configure_yf_proxy()
    return yf.download(
        tickers=yf_symbols, start=start, end=end, interval="1d",
        group_by="ticker", threads=True, auto_adjust=False, progress=False,
    )


def fetch_daily_history(yf_sym: str, start: str):
    """单只海外日线历史兜底（yf.Ticker().history()），返回去空后的 DataFrame（或 None）。"""
    import yfinance as yf
    configure_yf_proxy()
    df = yf.Ticker(yf_sym).history(start=start, interval="1d", auto_adjust=False)
    return df.dropna(how="all") if df is not None else None

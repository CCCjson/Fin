"""
pytdx 分钟线数据获取器 — 用于盘中扫描

通过 pytdx 连接通达信行情服务器，拉取 A 股 1/5/15/30/60 分钟 K 线。
"""
import os
import sys
from contextlib import contextmanager
from typing import Dict, List, Tuple

import pandas as pd
from loguru import logger

# pytdx category 映射
_PERIOD_TO_CATEGORY = {
    1: 8,   # 1 分钟
    5: 0,   # 5 分钟
    15: 1,  # 15 分钟
    30: 2,  # 30 分钟
    60: 3,  # 60 分钟
}

# 日线 category
_DAILY_CATEGORY = 9
# get_security_bars / get_index_bars 单次最多 800 根
_MAX_BARS_PER_CALL = 800

# 模块级缓存：最优服务器 IP（避免每次实例化都测速）
_cached_best_ip: Dict = {}
_warmup_done: bool = False


@contextmanager
def _suppress_stdout():
    """临时屏蔽 stdout，用于静音 pytdx select_best_ip() 的 print 输出"""
    devnull = open(os.devnull, "w")
    old_stdout = sys.stdout
    sys.stdout = devnull
    try:
        yield
    finally:
        sys.stdout = old_stdout
        devnull.close()


def _select_best_ip_quiet() -> Dict:
    """静默调用 pytdx select_best_ip()，屏蔽 BAD/GOOD RESPONSE 输出"""
    from pytdx.util.best_ip import select_best_ip
    with _suppress_stdout():
        return select_best_ip()


def warmup_pytdx() -> bool:
    """
    启动时预热：测速选出最优服务器并缓存，后续连接直接命中缓存。

    应在应用启动时（如 FastAPI startup）调用一次。
    """
    global _cached_best_ip, _warmup_done

    if _warmup_done:
        logger.debug("pytdx 已预热过，跳过")
        return True

    logger.info("pytdx 预热开始：测速选最优服务器...")

    try:
        best = _select_best_ip_quiet()
        if best and best.get("ip"):
            ip = best["ip"]
            port = int(best.get("port", 7709))
            _cached_best_ip = {"ip": ip, "port": port}
            _warmup_done = True
            logger.success(f"pytdx 预热完成: 最优服务器 {ip}:{port}")
            return True
    except Exception as e:
        logger.warning(f"pytdx 预热测速失败: {e}，尝试遍历列表...")

    # 回退：遍历内置列表找一个能连的
    try:
        from pytdx.hq import TdxHq_API
        from pytdx.config.hosts import hq_hosts

        api = TdxHq_API()
        for name, host, port in hq_hosts:
            try:
                api.connect(host, int(port))
                api.disconnect()
                _cached_best_ip = {"ip": host, "port": int(port)}
                _warmup_done = True
                logger.success(f"pytdx 预热完成(遍历): {name} ({host}:{port})")
                return True
            except Exception:
                continue
    except ImportError:
        pass

    logger.error("pytdx 预热失败：无可用服务器")
    return False


class PytdxFetcher:
    """pytdx 分钟 K 线获取器"""

    def __init__(self):
        from pytdx.hq import TdxHq_API
        self._api = TdxHq_API()
        self._connected = False

    # ---------- 内部方法 ----------

    def _connect(self) -> bool:
        """连接通达信服务器：优先用缓存 IP，否则测速选最优，最后遍历列表"""
        global _cached_best_ip
        if self._connected:
            return True

        # 方式一：使用缓存的最优 IP（预热后走这条路）
        if _cached_best_ip:
            try:
                self._api.connect(_cached_best_ip["ip"], _cached_best_ip["port"])
                self._connected = True
                logger.info(f"pytdx 已连接(缓存): {_cached_best_ip['ip']}:{_cached_best_ip['port']}")
                return True
            except Exception:
                _cached_best_ip = {}  # 缓存失效，清空

        # 方式二：静默测速选最优
        try:
            best = _select_best_ip_quiet()
            if best and best.get("ip"):
                ip = best["ip"]
                port = int(best.get("port", 7709))
                self._api.connect(ip, port)
                self._connected = True
                _cached_best_ip = {"ip": ip, "port": port}
                logger.info(f"pytdx 已连接最优服务器: {ip}:{port}")
                return True
        except Exception as e:
            logger.debug(f"pytdx 最优 IP 选择失败: {e}")

        # 方式三：遍历内置服务器列表
        try:
            from pytdx.config.hosts import hq_hosts
            for name, host, port in hq_hosts:
                try:
                    self._api.connect(host, int(port))
                    self._connected = True
                    _cached_best_ip = {"ip": host, "port": int(port)}
                    logger.info(f"pytdx 已连接: {name} ({host}:{port})")
                    return True
                except Exception:
                    continue
        except ImportError:
            pass

        logger.error("pytdx 所有服务器连接失败")
        return False

    def _disconnect(self):
        """断开连接"""
        if self._connected:
            try:
                self._api.disconnect()
            except Exception:
                pass
            self._connected = False

    @staticmethod
    def _symbol_to_pytdx(symbol: str) -> Tuple[int, str]:
        """
        股票代码转换为 pytdx 格式

        "600519.SH" -> (1, "600519")   # 沪市 market=1
        "000858.SZ" -> (0, "000858")   # 深市 market=0
        """
        parts = symbol.upper().split(".")
        code = parts[0]

        if len(parts) == 2:
            suffix = parts[1]
            if suffix == "SH":
                return (1, code)
            elif suffix == "SZ":
                return (0, code)

        # 无后缀时按首位数字判断
        if code.startswith(("6", "9")):
            return (1, code)
        else:
            return (0, code)

    def _raw_to_dataframe(self, raw_data: list, keep_amount: bool = False) -> pd.DataFrame:
        """将 pytdx 原始数据转为标准 OHLCV DataFrame

        Args:
            raw_data: pytdx 原始 bar 列表
            keep_amount: 是否保留成交额列（日线写库需要）
        """
        if not raw_data:
            return pd.DataFrame()

        df = pd.DataFrame(raw_data)

        # pytdx 返回列名: datetime, open, close, high, low, vol, amount
        rename_map = {
            "datetime": "date",
            "vol": "volume",
        }
        df = df.rename(columns=rename_map)

        # 保留标准列
        cols = ["date", "open", "high", "low", "close", "volume"]
        if keep_amount:
            cols.append("amount")
        df = df[[c for c in cols if c in df.columns]]

        # date 转 datetime
        df["date"] = pd.to_datetime(df["date"])

        # 数值列转 float
        num_cols = ["open", "high", "low", "close", "volume"]
        if keep_amount:
            num_cols.append("amount")
        for col in num_cols:
            if col in df.columns:
                df[col] = pd.to_numeric(df[col], errors="coerce")

        df = df.sort_values("date").reset_index(drop=True)
        return df

    # ---------- 公开方法 ----------

    def fetch_minute_bars(
        self,
        symbol: str,
        period: int = 1,
        count: int = 240,
        keep_amount: bool = False,
    ) -> pd.DataFrame:
        """
        获取单只股票的分钟 K 线

        Args:
            symbol: 股票代码，如 "600519.SH"
            period: K 线周期（分钟），支持 1/5/15/30/60
            count: 拉取根数，默认 240（≈ 1 个交易日）
            keep_amount: 是否保留成交额列（算分时 VWAP 需要）

        Returns:
            标准 OHLCV DataFrame
        """
        category = _PERIOD_TO_CATEGORY.get(period)
        if category is None:
            raise ValueError(f"不支持的周期: {period}，可选 {list(_PERIOD_TO_CATEGORY.keys())}")

        if not self._connect():
            return pd.DataFrame()

        market, code = self._symbol_to_pytdx(symbol)

        try:
            raw = self._api.get_security_bars(category, market, code, 0, count)
            df = self._raw_to_dataframe(raw, keep_amount=keep_amount)
            if df.empty:
                logger.warning(f"pytdx 获取 {symbol} {period}分钟线为空")
            else:
                logger.debug(f"pytdx 获取 {symbol} {period}分钟线: {len(df)} 根")
            return df
        except Exception as e:
            logger.error(f"pytdx 获取 {symbol} 分钟线失败: {e}")
            return pd.DataFrame()

    def fetch_minute_bars_batch(
        self,
        symbols: List[str],
        period: int = 1,
        count: int = 240,
    ) -> Dict[str, pd.DataFrame]:
        """
        批量获取分钟 K 线（单次连接内循环拉取）

        Args:
            symbols: 股票代码列表
            period: K 线周期
            count: 拉取根数

        Returns:
            {symbol: DataFrame} 字典，失败的 symbol 不包含在结果中
        """
        category = _PERIOD_TO_CATEGORY.get(period)
        if category is None:
            raise ValueError(f"不支持的周期: {period}")

        if not self._connect():
            return {}

        result: Dict[str, pd.DataFrame] = {}

        for symbol in symbols:
            market, code = self._symbol_to_pytdx(symbol)
            try:
                raw = self._api.get_security_bars(category, market, code, 0, count)
                df = self._raw_to_dataframe(raw)
                if not df.empty:
                    result[symbol] = df
                    logger.debug(f"pytdx 获取 {symbol} {period}分钟线: {len(df)} 根")
                else:
                    logger.warning(f"pytdx {symbol} 返回空数据")
            except Exception as e:
                logger.warning(f"pytdx 获取 {symbol} 分钟线失败: {e}")

        logger.info(f"pytdx 批量获取完成: {len(result)}/{len(symbols)} 只成功")
        return result

    def fetch_daily_bars(
        self,
        symbol: str,
        count: int = 800,
        is_index: bool = False,
    ) -> pd.DataFrame:
        """
        获取单只标的的日 K 线（原始不复权价）

        注意：pytdx 返回的是不复权价格。全库日线是东财前复权（fqt=1），
        跨除权日的股票数据不能直接混用；指数无复权问题可放心使用。

        Args:
            symbol: 代码，如 "000001.SH"
            count: 拉取根数（最多 800）
            is_index: 是否指数（指数走 get_index_bars 接口）

        Returns:
            DataFrame: date/open/high/low/close/volume/amount
        """
        if not self._connect():
            return pd.DataFrame()

        market, code = self._symbol_to_pytdx(symbol)
        count = min(count, _MAX_BARS_PER_CALL)

        try:
            if is_index:
                raw = self._api.get_index_bars(_DAILY_CATEGORY, market, code, 0, count)
            else:
                raw = self._api.get_security_bars(_DAILY_CATEGORY, market, code, 0, count)
            df = self._raw_to_dataframe(raw, keep_amount=True)
            if df.empty:
                logger.warning(f"pytdx 获取 {symbol} 日线为空")
            else:
                logger.debug(f"pytdx 获取 {symbol} 日线: {len(df)} 根")
            return df
        except Exception as e:
            logger.error(f"pytdx 获取 {symbol} 日线失败: {e}")
            return pd.DataFrame()

    def fetch_daily_bars_batch(
        self,
        symbols: List[str],
        count: int = 100,
        is_index: bool = False,
    ) -> Dict[str, pd.DataFrame]:
        """
        批量获取日 K 线（单次连接内循环拉取，TDX socket 快、无限速）

        Args:
            symbols: 代码列表
            count: 每只拉取根数（最多 800）
            is_index: 是否指数列表

        Returns:
            {symbol: DataFrame} 字典，失败的 symbol 不包含在结果中
        """
        if not self._connect():
            return {}

        count = min(count, _MAX_BARS_PER_CALL)
        result: Dict[str, pd.DataFrame] = {}

        for symbol in symbols:
            market, code = self._symbol_to_pytdx(symbol)
            try:
                if is_index:
                    raw = self._api.get_index_bars(_DAILY_CATEGORY, market, code, 0, count)
                else:
                    raw = self._api.get_security_bars(_DAILY_CATEGORY, market, code, 0, count)
                df = self._raw_to_dataframe(raw, keep_amount=True)
                if not df.empty:
                    result[symbol] = df
                else:
                    logger.warning(f"pytdx {symbol} 日线返回空数据")
            except Exception as e:
                logger.warning(f"pytdx 获取 {symbol} 日线失败: {e}")

        logger.info(f"pytdx 批量日线完成: {len(result)}/{len(symbols)} 只成功")
        return result

    def close(self):
        """关闭连接"""
        self._disconnect()

    def __del__(self):
        self._disconnect()

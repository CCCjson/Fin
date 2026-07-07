"""
BenchmarkService — 基准指数数据服务

为回测模块提供大盘基准对比曲线（沪深300/恒生指数/标普500）。
数据获取策略：内存缓存 → DailyQuote DB → akshare/东方财富远程。
"""
import time
from datetime import date, datetime
from typing import Dict, Any, Optional, List

import pandas as pd
import requests
from loguru import logger


# 市场 → 基准指数映射（键用 canonical：a_share / hk_stock / us_stock）
MARKET_BENCHMARK: Dict[str, Dict[str, str]] = {
    "a_share":  {"symbol": "000300.SH", "name": "沪深300", "ak_code": "sh000300"},
    "hk_stock": {"symbol": "HSI",       "name": "恒生指数", "secid": "100.HSI"},
    "us_stock": {"symbol": "SPX",       "name": "标普500",  "secid": "100.SPX"},
}


class BenchmarkService:
    """基准指数数据服务（实例级缓存）"""

    def __init__(self):
        self._cache: Dict[str, pd.DataFrame] = {}

    def get_benchmark_curve(
        self,
        market: str,
        start_date: str,
        end_date: str,
        initial_capital: float = 100000.0,
    ) -> Optional[Dict[str, Any]]:
        """
        获取基准指数归一化曲线。

        Args:
            market: 市场类型（任意写法，内部归一到 canonical a_share/hk_stock/us_stock）
            start_date: 开始日期 YYYY-MM-DD
            end_date: 结束日期 YYYY-MM-DD
            initial_capital: 初始资金（用于归一化）

        Returns:
            {benchmark_name, benchmark_symbol, benchmark_return_pct, benchmark_curve: [{date, benchmark_value}]}
            获取失败时返回 None
        """
        from common.market import normalize_market
        market = normalize_market(market)
        bm = MARKET_BENCHMARK.get(market)
        if not bm:
            logger.warning(f"未知市场类型: {market}")
            return None

        try:
            df = self._get_index_data(market, start_date, end_date)
            if df is None or df.empty:
                logger.warning(f"获取基准数据为空: market={market}")
                return None

            # 过滤日期范围
            start_ts = pd.Timestamp(start_date)
            end_ts = pd.Timestamp(end_date)
            df = df[(df.index >= start_ts) & (df.index <= end_ts)]

            if df.empty:
                logger.warning(f"日期范围内无基准数据: {start_date} ~ {end_date}")
                return None

            # 归一化：benchmark_value = (close / base_close) * initial_capital
            base_close = df["close"].iloc[0]
            if base_close <= 0:
                return None

            curve = []
            for idx, row in df.iterrows():
                curve.append({
                    "date": idx.strftime("%Y-%m-%d"),
                    "benchmark_value": round(row["close"] / base_close * initial_capital, 2),
                })

            last_close = df["close"].iloc[-1]
            benchmark_return_pct = round((last_close - base_close) / base_close * 100, 2)

            return {
                "benchmark_name": bm["name"],
                "benchmark_symbol": bm["symbol"],
                "benchmark_return_pct": benchmark_return_pct,
                "benchmark_curve": curve,
            }

        except Exception as e:
            logger.error(f"获取基准曲线失败 (market={market}): {e}")
            return None

    # ==================== 内部方法 ====================

    def _get_index_data(self, market: str, start_date: str, end_date: str) -> Optional[pd.DataFrame]:
        """三层获取：缓存 → DB → 远程"""
        cache_key = f"{market}_{start_date}_{end_date}"
        if cache_key in self._cache:
            return self._cache[cache_key]

        # 层1: 从 DB 读取
        df = self._fetch_from_db(market, start_date, end_date)
        if df is not None and len(df) > 10:
            self._cache[cache_key] = df
            return df

        # 层2: 远程获取
        if market == "a_share":
            df = self._fetch_a_share_index(start_date, end_date)
        else:
            df = self._fetch_eastmoney_index(market, start_date, end_date)

        if df is not None and not df.empty:
            self._cache[cache_key] = df
            return df

        return None

    def _fetch_from_db(self, market: str, start_date: str, end_date: str) -> Optional[pd.DataFrame]:
        """从 DailyQuote 表读取基准数据"""
        bm = MARKET_BENCHMARK[market]
        symbol = bm["symbol"]
        try:
            from data_engine.storage.database import get_session
            from data_engine.storage.models import DailyQuote
            session = get_session()
            try:
                quotes = (
                    session.query(DailyQuote)
                    .filter(
                        DailyQuote.symbol == symbol,
                        DailyQuote.date >= start_date,
                        DailyQuote.date <= end_date,
                    )
                    .order_by(DailyQuote.date.asc())
                    .all()
                )
                if quotes and len(quotes) > 0:
                    data = [{"date": q.date, "close": float(q.close)} for q in quotes]
                    df = pd.DataFrame(data)
                    df["date"] = pd.to_datetime(df["date"])
                    df = df.set_index("date")
                    logger.debug(f"从 DB 获取基准 {symbol}: {len(df)} 条")
                    return df
            finally:
                session.close()
        except Exception as e:
            logger.debug(f"从 DB 读取基准失败 ({symbol}): {e}")
        return None

    def _fetch_a_share_index(self, start_date: str, end_date: str) -> Optional[pd.DataFrame]:
        """通过 akshare 获取沪深300日线"""
        try:
            import akshare as ak
            from net import domestic_akshare
            df = domestic_akshare(ak.stock_zh_index_daily_em, symbol="sh000300")
            if df is None or df.empty:
                return None

            df["date"] = pd.to_datetime(df["date"])
            df = df.set_index("date")
            df = df[["close"]].copy()
            df["close"] = df["close"].astype(float)

            logger.info(f"从 akshare 获取沪深300: {len(df)} 条")
            return df

        except Exception as e:
            logger.warning(f"akshare 获取沪深300失败: {e}，降级到东方财富")
            return self._fetch_eastmoney_index("a_share", start_date, end_date)

    def _fetch_eastmoney_index(self, market: str, start_date: str, end_date: str) -> Optional[pd.DataFrame]:
        """从东方财富历史K线接口获取指数数据"""
        bm = MARKET_BENCHMARK[market]

        # 确定 secid
        if market == "a_share":
            secid = "1.000300"
        else:
            secid = bm.get("secid", "")
        if not secid:
            logger.warning(f"无法确定 secid: market={market}")
            return None

        beg = start_date.replace("-", "")
        end = end_date.replace("-", "")

        url = "https://push2his.eastmoney.com/api/qt/stock/kline/get"
        params = {
            "secid": secid,
            "fields1": "f1,f2,f3,f4,f5,f6",
            "fields2": "f51,f52,f53,f54,f55,f56,f57,f58,f59,f60,f61",
            "klt": 101,       # 日线
            "fqt": 1,         # 前复权
            "beg": beg,
            "end": end,
            "ut": "bd1d9ddb04089700cf9c27f6f7426281",
            "_": str(int(time.time() * 1000)),
        }

        headers = {
            "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36",
            "Referer": "https://finance.eastmoney.com/",
        }

        try:
            from net import domestic_json
            data = domestic_json(url, params=params, headers=headers, timeout=10)
            if data is None:
                logger.warning(f"东方财富请求失败: secid={secid}")
                return None
            klines = data.get("data", {}).get("klines", [])
            if not klines:
                logger.warning(f"东方财富返回空数据: secid={secid}")
                return None

            records = []
            for line in klines:
                fields = line.split(",")
                # fields: 日期,开盘,收盘,最高,最低,成交量,...
                records.append({
                    "date": fields[0],
                    "close": float(fields[2]),
                })

            df = pd.DataFrame(records)
            df["date"] = pd.to_datetime(df["date"])
            df = df.set_index("date")

            logger.info(f"从东方财富获取 {bm['name']}: {len(df)} 条")
            return df

        except Exception as e:
            logger.error(f"东方财富获取基准失败 ({bm['name']}): {e}")
            return None

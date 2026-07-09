"""
A股数据获取器 - 基于 akshare
"""
from typing import List, Dict
from datetime import datetime
import pandas as pd
import re
from loguru import logger

from common.market import add_exchange_suffix
from data_engine.fetchers.base import BaseFetcher, MarketDataRequest, MarketDataResponse


def _safe_symbol(code) -> str | None:
    """裸码补后缀，脏码返回 None 由调用方跳过。

    akshare 的股票列表里偶有非常规代码（退市整理、权证等）。单只坏码不该炸掉
    整张列表，故在此吞掉 ValueError——fetcher 层失败返回空结构而非抛异常。
    """
    try:
        return add_exchange_suffix(str(code).strip())
    except ValueError as exc:
        logger.warning(f"跳过无法识别的 A股代码 {code!r}: {exc}")
        return None


class AShareFetcher(BaseFetcher):
    """A股数据获取器"""

    def __init__(self, config: Dict = None):
        super().__init__(config or {})
        self.source = "akshare"

    def fetch_daily(self, request: MarketDataRequest) -> MarketDataResponse:
        """获取日线数据"""
        try:
            import akshare as ak
            from net import domestic_akshare

            # akshare 使用 6 位代码
            symbol_code = request.symbol[:6]

            logger.info(f"正在获取 {request.symbol} 的日线数据...")

            # 获取数据（统一网络层：快代理轮换重试，绝不直连兜底，Clash 无关）
            df = domestic_akshare(
                ak.stock_zh_a_hist,
                symbol=symbol_code,
                period="daily",
                start_date=request.start_date.strftime("%Y%m%d"),
                end_date=request.end_date.strftime("%Y%m%d"),
                adjust=request.adjust
            )

            if df.empty:
                logger.warning(f"{request.symbol} 没有数据")
                return MarketDataResponse(
                    symbol=request.symbol,
                    market="a_share",
                    data=pd.DataFrame(),
                    metadata={"source": self.source, "message": "No data"}
                )

            # 字段映射
            df = df.rename(columns={
                "日期": "date",
                "开盘": "open",
                "收盘": "close",
                "最高": "high",
                "最低": "low",
                "成交量": "volume",
                "成交额": "amount",
                "振幅": "amplitude",
                "涨跌幅": "change_pct",
                "涨跌额": "change",
                "换手率": "turnover"
            })

            # 选择需要的列
            required_cols = ['date', 'open', 'high', 'low', 'close', 'volume']
            optional_cols = ['amount', 'turnover']

            # 确保必需列存在
            for col in required_cols:
                if col not in df.columns:
                    raise ValueError(f"Missing required column: {col}")

            # 添加可选列（如果不存在则填充 None）
            for col in optional_cols:
                if col not in df.columns:
                    df[col] = None

            # 选择最终列
            final_cols = required_cols + [c for c in optional_cols if c in df.columns]
            df = df[final_cols]

            logger.info(f"成功获取 {request.symbol} 的 {len(df)} 条数据")

            return MarketDataResponse(
                symbol=request.symbol,
                market="a_share",
                data=df,
                metadata={
                    "source": self.source,
                    "fetched_at": datetime.now().isoformat(),
                    "adjust": request.adjust,
                    "records": len(df)
                }
            )

        except Exception as e:
            logger.error(f"获取 {request.symbol} 数据失败: {e}")
            raise

    def fetch_realtime(self, symbols: List[str]) -> List[Dict]:
        """获取实时行情（个股+指数）。

        委托给 realtime.fetch_quotes_by_symbols：走东财 ulist.np 批量接口 +
        快代理 IP 池 + 失败切 IP 重试（trust_env=False 绕开系统 Clash）。
        secid 的「市场.代码」前缀天然区分沪深，000001.SH(上证指数) 与
        000001.SZ(平安银行) 不再混淆。
        """
        from data_engine.fetchers.realtime import fetch_quotes_by_symbols
        result = fetch_quotes_by_symbols(symbols)
        logger.info(f"成功获取 {len(result)}/{len(symbols)} 只标的的实时行情")
        return result

    def validate_symbol(self, symbol: str) -> bool:
        """验证股票代码"""
        # A股代码格式: 6位数字 + 市场后缀 (.SH/.SZ/.BJ)
        pattern = r"^\d{6}\.(SH|SZ|BJ)$"
        return bool(re.match(pattern, symbol))

    def search_symbol(self, keyword: str) -> List[Dict]:
        """搜索股票"""
        try:
            import akshare as ak
            from net import domestic_akshare

            logger.info(f"搜索股票: {keyword}")

            # 获取所有股票列表（统一网络层）
            df = domestic_akshare(ak.stock_zh_a_spot_em)

            # 搜索代码或名称包含关键字的股票
            matched = df[
                df["代码"].str.contains(keyword, na=False) |
                df["名称"].str.contains(keyword, na=False)
            ]

            result = []
            for _, row in matched.iterrows():
                symbol = _safe_symbol(row["代码"])
                if symbol is None:
                    continue

                result.append({
                    "symbol": symbol,
                    "name": row["名称"],
                    "market": "a_share",
                    "price": float(row["最新价"]) if row["最新价"] else None,
                    "change_percent": float(row["涨跌幅"]) if row["涨跌幅"] else None
                })

            logger.info(f"找到 {len(result)} 只匹配的股票")
            return result[:20]  # 最多返回20个结果

        except Exception as e:
            logger.error(f"搜索股票失败: {e}")
            raise

    def get_stock_list(self) -> List[Dict]:
        """获取所有 A股列表"""
        try:
            import akshare as ak
            from net import domestic_akshare

            logger.info("获取 A股股票列表...")

            df = domestic_akshare(ak.stock_info_a_code_name)

            result = []
            for _, row in df.iterrows():
                symbol = _safe_symbol(row["code"])
                if symbol is None:
                    continue

                result.append({
                    "symbol": symbol,
                    "name": row["name"],
                    "market": "a_share"
                })

            logger.info(f"获取到 {len(result)} 只 A股")
            return result

        except Exception as e:
            logger.error(f"获取股票列表失败: {e}")
            raise

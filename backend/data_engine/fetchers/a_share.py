"""
A股数据获取器 - 基于 akshare
"""
from typing import List, Dict
from datetime import datetime
import pandas as pd
import re
from loguru import logger

from data_engine.fetchers.base import BaseFetcher, MarketDataRequest, MarketDataResponse


class AShareFetcher(BaseFetcher):
    """A股数据获取器"""

    def __init__(self, config: Dict = None):
        super().__init__(config or {})
        self.source = "akshare"

    def fetch_daily(self, request: MarketDataRequest) -> MarketDataResponse:
        """获取日线数据"""
        try:
            import akshare as ak

            # akshare 使用 6 位代码
            symbol_code = request.symbol[:6]

            logger.info(f"正在获取 {request.symbol} 的日线数据...")

            # 获取数据
            df = ak.stock_zh_a_hist(
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
        """获取实时行情"""
        try:
            import akshare as ak

            result = []

            # 获取实时行情
            df = ak.stock_zh_a_spot_em()

            for symbol in symbols:
                symbol_code = symbol[:6]

                # 查找对应股票
                stock = df[df["代码"] == symbol_code]

                if stock.empty:
                    logger.warning(f"未找到 {symbol} 的实时行情")
                    continue

                stock = stock.iloc[0]

                result.append({
                    "symbol": symbol,
                    "name": stock["名称"],
                    "price": float(stock["最新价"]),
                    "change": float(stock["涨跌额"]),
                    "change_percent": float(stock["涨跌幅"]),
                    "volume": int(stock["成交量"]),
                    "amount": float(stock["成交额"]),
                    "open": float(stock["今开"]),
                    "high": float(stock["最高"]),
                    "low": float(stock["最低"]),
                    "timestamp": datetime.now().isoformat()
                })

            logger.info(f"成功获取 {len(result)}/{len(symbols)} 只股票的实时行情")
            return result

        except Exception as e:
            logger.error(f"获取实时行情失败: {e}")
            raise

    def validate_symbol(self, symbol: str) -> bool:
        """验证股票代码"""
        # A股代码格式: 6位数字 + 市场后缀 (.SH/.SZ)
        pattern = r"^\d{6}\.(SH|SZ)$"
        return bool(re.match(pattern, symbol))

    def search_symbol(self, keyword: str) -> List[Dict]:
        """搜索股票"""
        try:
            import akshare as ak

            logger.info(f"搜索股票: {keyword}")

            # 获取所有股票列表
            df = ak.stock_zh_a_spot_em()

            # 搜索代码或名称包含关键字的股票
            matched = df[
                df["代码"].str.contains(keyword, na=False) |
                df["名称"].str.contains(keyword, na=False)
            ]

            result = []
            for _, row in matched.iterrows():
                # 判断市场（6开头是上海，其他是深圳）
                market_suffix = "SH" if row["代码"].startswith("6") else "SZ"

                result.append({
                    "symbol": f"{row['代码']}.{market_suffix}",
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

            logger.info("获取 A股股票列表...")

            df = ak.stock_info_a_code_name()

            result = []
            for _, row in df.iterrows():
                market_suffix = "SH" if row["code"].startswith("6") else "SZ"

                result.append({
                    "symbol": f"{row['code']}.{market_suffix}",
                    "name": row["name"],
                    "market": "a_share"
                })

            logger.info(f"获取到 {len(result)} 只 A股")
            return result

        except Exception as e:
            logger.error(f"获取股票列表失败: {e}")
            raise

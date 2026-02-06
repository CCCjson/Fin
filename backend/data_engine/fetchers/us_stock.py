"""
美股数据获取器 - 基于 yfinance
"""
from typing import List, Dict
from datetime import datetime
import pandas as pd
import re
from loguru import logger

from data_engine.fetchers.base import BaseFetcher, MarketDataRequest, MarketDataResponse


class USStockFetcher(BaseFetcher):
    """美股数据获取器"""

    def __init__(self, config: Dict = None):
        super().__init__(config or {})
        self.source = "yfinance"

    def fetch_daily(self, request: MarketDataRequest) -> MarketDataResponse:
        """获取日线数据"""
        try:
            import yfinance as yf

            logger.info(f"正在获取 {request.symbol} 的日线数据...")

            ticker = yf.Ticker(request.symbol)
            df = ticker.history(
                start=request.start_date,
                end=request.end_date,
                interval=request.frequency
            )

            if df.empty:
                logger.warning(f"{request.symbol} 没有数据")
                return MarketDataResponse(
                    symbol=request.symbol,
                    market="us_stock",
                    data=pd.DataFrame(),
                    metadata={"source": self.source, "message": "No data"}
                )

            # 标准化列名
            df = df.reset_index()
            df.columns = [col.lower() for col in df.columns]

            # 选择需要的列
            df = df[["date", "open", "high", "low", "close", "volume"]]

            logger.info(f"成功获取 {request.symbol} 的 {len(df)} 条数据")

            return MarketDataResponse(
                symbol=request.symbol,
                market="us_stock",
                data=df,
                metadata={
                    "source": self.source,
                    "fetched_at": datetime.now().isoformat(),
                    "records": len(df)
                }
            )

        except Exception as e:
            logger.error(f"获取 {request.symbol} 数据失败: {e}")
            raise

    def fetch_realtime(self, symbols: List[str]) -> List[Dict]:
        """获取实时行情"""
        try:
            import yfinance as yf

            result = []

            for symbol in symbols:
                try:
                    ticker = yf.Ticker(symbol)
                    info = ticker.info

                    result.append({
                        "symbol": symbol,
                        "price": info.get("currentPrice", 0),
                        "change": info.get("regularMarketChange", 0),
                        "change_percent": info.get("regularMarketChangePercent", 0),
                        "volume": info.get("volume", 0),
                        "timestamp": datetime.now().isoformat()
                    })
                except Exception as e:
                    logger.warning(f"获取 {symbol} 实时行情失败: {e}")

            logger.info(f"成功获取 {len(result)}/{len(symbols)} 只股票的实时行情")
            return result

        except Exception as e:
            logger.error(f"获取实时行情失败: {e}")
            raise

    def validate_symbol(self, symbol: str) -> bool:
        """验证股票代码"""
        # 美股代码格式: 大写字母，长度1-5
        pattern = r"^[A-Z]{1,5}$"
        return bool(re.match(pattern, symbol))

    def search_symbol(self, keyword: str) -> List[Dict]:
        """搜索股票（美股搜索功能有限）"""
        logger.warning("美股搜索功能有限，建议使用完整代码")
        return []

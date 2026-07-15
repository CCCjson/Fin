"""
美股数据获取器 - 基于 yfinance
"""
from typing import List, Dict
from datetime import datetime
import pandas as pd
import re
from loguru import logger

from acquisition.markets.base import BaseFetcher, MarketDataRequest, MarketDataResponse


class USStockFetcher(BaseFetcher):
    """美股数据获取器"""

    def __init__(self, config: Dict = None):
        super().__init__(config or {})
        self.source = "yfinance"

    def fetch_daily(self, request: MarketDataRequest) -> MarketDataResponse:
        """获取日线数据"""
        try:
            import yfinance as yf

            from acquisition.markets.yf_batch import configure_yf_proxy
            configure_yf_proxy()

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
        """获取实时行情（一次批量 download 替代逐只 ticker.info）"""
        try:
            from acquisition.markets.yf_batch import fetch_yf_realtime_batch
            return fetch_yf_realtime_batch(symbols)
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

    def get_stock_list(self) -> List[Dict]:
        """获取所有美股列表（代码 + 名称），用于填充 StockInfo 表。

        直接分页调用东财 clist/get 原始接口（f12=代码 f14=名称），不走 akshare
        的 stock_us_spot_em——那个接口内部一次性顺序翻 130+ 页，中途代理一断
        整体就得从第 1 页重来，而且它的"代码"列还是拼接过的 "编码.简称" 格式。
        这里直接用 f12（本身就是裸 ticker，如 "AAPL"），按页调用 domestic_json，
        每页自带独立的代理轮换重试，某页最终还是失败就停下，返回已拿到的部分。
        """
        from net import domestic_json

        logger.info("获取美股股票列表...")
        url = "https://72.push2.eastmoney.com/api/qt/clist/get"
        base_params = {
            "po": "1", "np": "1",
            "ut": "bd1d9ddb04089700cf9c27f6f7426281",
            "fltt": "2", "invt": "2", "fid": "f12",
            "fs": "m:105,m:106,m:107",
            "fields": "f12,f14",
        }

        result = []
        page = 1
        total = None
        while total is None or len(result) < total:
            params = {**base_params, "pn": str(page), "pz": "100"}
            data = domestic_json(url, params=params, max_rounds=5)
            rows = ((data or {}).get("data") or {}).get("diff") or []
            if not rows:
                logger.warning(f"美股列表第 {page} 页拉取失败，停止（已获取 {len(result)}/{total or '?'} 条）")
                break
            if total is None:
                total = (data.get("data") or {}).get("total", 0)
            for r in rows:
                ticker = str(r.get("f12", "")).strip()
                name = r.get("f14")
                if ticker and name:
                    result.append({"symbol": ticker, "name": name, "market": "us_stock"})
            page += 1

        if not result:
            raise RuntimeError("美股列表一页都没拉到，检查代理/网络")

        logger.info(f"获取到 {len(result)} 只美股")
        return result

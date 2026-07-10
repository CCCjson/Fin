"""
数据获取器工厂
"""
from typing import Dict
from acquisition.markets.base import BaseFetcher
from acquisition.markets.a_share import AShareFetcher
from acquisition.markets.hk_stock import HKStockFetcher
from acquisition.markets.us_stock import USStockFetcher


class FetcherFactory:
    """数据获取器工厂"""

    _fetchers = {
        "a_share": AShareFetcher,
        "hk_stock": HKStockFetcher,
        "us_stock": USStockFetcher
    }

    @classmethod
    def create(cls, market: str, config: Dict = None) -> BaseFetcher:
        """创建数据获取器"""
        fetcher_class = cls._fetchers.get(market)
        if not fetcher_class:
            raise ValueError(f"Unknown market: {market}")
        return fetcher_class(config or {})

    @classmethod
    def get_fetcher_for_symbol(cls, symbol: str, config: Dict = None) -> BaseFetcher:
        """根据股票代码自动识别市场并创建获取器（后缀推断委托 common.market）"""
        from common.market import infer_market_from_symbol
        return cls.create(infer_market_from_symbol(symbol), config)

    @classmethod
    def list_markets(cls) -> list:
        """列出所有支持的市场"""
        return list(cls._fetchers.keys())

"""
数据获取器工厂
"""
from typing import Dict
from data_engine.fetchers.base import BaseFetcher
from data_engine.fetchers.a_share import AShareFetcher
from data_engine.fetchers.hk_stock import HKStockFetcher
from data_engine.fetchers.us_stock import USStockFetcher


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
        """根据股票代码自动识别市场并创建获取器"""
        if symbol.endswith(('.SH', '.SZ')):
            return cls.create("a_share", config)
        elif symbol.endswith('.HK'):
            return cls.create("hk_stock", config)
        else:
            # 默认认为是美股
            return cls.create("us_stock", config)

    @classmethod
    def list_markets(cls) -> list:
        """列出所有支持的市场"""
        return list(cls._fetchers.keys())

"""
数据获取器抽象基类
"""
from abc import ABC, abstractmethod
from typing import List, Dict, Optional
from pydantic import BaseModel
import pandas as pd


class MarketDataRequest(BaseModel):
    """数据请求模型"""
    symbol: str
    start_date: str  # YYYY-MM-DD
    end_date: str  # YYYY-MM-DD
    freq: str = "1d"
    adjust: str = "qfq"  # qfq: 前复权, hfq: 后复权, none: 不复权


class MarketDataResponse(BaseModel):
    """数据响应模型"""
    symbol: str
    market: str
    data: pd.DataFrame
    metadata: Dict

    class Config:
        arbitrary_types_allowed = True


class BaseFetcher(ABC):
    """数据获取器抽象基类"""

    def __init__(self, config: Dict):
        self.config = config
        self.retry_times = config.get("retry_times", 3)
        self.retry_delay = config.get("retry_delay", 5)
        self.timeout = config.get("timeout", 30)

    @abstractmethod
    def fetch_daily(self, request: MarketDataRequest) -> MarketDataResponse:
        """获取日线数据"""
        pass

    @abstractmethod
    def fetch_realtime(self, symbols: List[str]) -> List[Dict]:
        """获取实时数据"""
        pass

    @abstractmethod
    def validate_symbol(self, symbol: str) -> bool:
        """验证股票代码有效性"""
        pass

    @abstractmethod
    def search_symbol(self, keyword: str) -> List[Dict]:
        """搜索股票"""
        pass

    def _retry_wrapper(self, func, *args, **kwargs):
        """重试装饰器"""
        from tenacity import retry, stop_after_attempt, wait_exponential
        import time

        @retry(
            stop=stop_after_attempt(self.retry_times),
            wait=wait_exponential(multiplier=1, min=2, max=10)
        )
        def wrapper():
            return func(*args, **kwargs)

        return wrapper()

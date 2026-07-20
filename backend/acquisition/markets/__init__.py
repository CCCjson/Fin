"""
行情 / 财务数据获取 —— 各市场 fetcher 与主备源路由。

原 `data_engine/fetchers/`（13.4-2 整体迁入）：data_engine 只留调度与落库
（storage / updater / scheduler / deep_history），出网取数全部归这一层。

`eastmoney_crawler` 此前住在 `scripts/`，害得三个生产模块（daily_updater /
deep_history.a_share_job / report_engine.web_searcher）各写一段 `sys.path.insert`
才 import 得到它。搬进正位后那三段补丁一并消失。

⚠️ `pytdx_fetcher` 走 TCP socket（通达信协议），塞不进 HTTP 代理层。它是
CODING_STANDARDS §8.4 的第三类合法例外：非 HTTP 协议数据源。引擎层不得直接
`import pytdx`，一律经这里的门面。
"""
from acquisition.markets.a_share import AShareFetcher
from acquisition.markets.base import BaseFetcher, MarketDataRequest, MarketDataResponse
from acquisition.markets.crypto import CryptoFetcher
from acquisition.markets.factory import FetcherFactory
from acquisition.markets.hk_stock import HKStockFetcher
from acquisition.markets.us_stock import USStockFetcher

__all__ = [
    'BaseFetcher',
    'MarketDataRequest',
    'MarketDataResponse',
    'AShareFetcher',
    'HKStockFetcher',
    'USStockFetcher',
    'CryptoFetcher',
    'FetcherFactory',
]

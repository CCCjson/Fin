"""
数据引擎模块 —— 只做调度与落库。

出网取数已于 13.4-2 整体迁往 `acquisition/markets/`（原 `data_engine/fetchers/`）。
本包保留 storage / updater / scheduler / deep_history / processors。
`FetcherFactory` 不再从这里 re-export——它属于 acquisition 层，且零调用方依赖此出口。
"""
from data_engine.engine import DataEngine
from data_engine.storage import init_db, get_session
from data_engine.processors import DataNormalizer, DataValidator, DataCleaner

__all__ = [
    'DataEngine',
    'init_db',
    'get_session',
    'DataNormalizer',
    'DataValidator',
    'DataCleaner'
]

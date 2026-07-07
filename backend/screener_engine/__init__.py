"""基本面选股器引擎 — 多因子筛选全市场（财务 + 估值）。"""
from screener_engine.service import (  # noqa: F401
    OPS,
    SCREENER_FIELDS,
    Filter,
    ScreenRequest,
    is_main_board,
    resolve_universe,
    run_screen,
)

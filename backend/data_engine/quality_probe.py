"""把「库/源的实际状态」翻译成 `common.context_quality` 的质量态。

**分工**：`common/context_quality.py` 是纯逻辑（什么算 stale、怎么算分），本模块
负责查库拿事实（这只票的 bar 到哪天了、行情是哪个源出的）。两边都不许越界 ——
判定阈值只准住在 common，DB 查询只准住在这里。

放 `data_engine/` 而不是 `agents/tools/`：多个工具（get_daily_data /
get_realtime_quote / cockpit / recommend）都要问同一个问题「这批数据可不可信」，
写在工具层就是每个工具抄一遍。
"""
from __future__ import annotations

from datetime import date

from common.context_quality import (
    FieldStatus,
    QualityBlock,
    QualityField,
    block_from_fields,
    status_for_bars_behind,
)
from common.market import infer_market_from_symbol


def daily_bars_block(symbol: str, market: str | None = None,
                     today: date | None = None) -> QualityBlock:
    """这只票的日线 bar 跟不跟得上市场 —— `daily_bars` 块。

    「落后市场参考交易日 ≥1 个交易日」= stale。注意参考日是**市场级**的
    （最近一个覆盖数达标的日期），不是 `max(date)` 也不是 `today` ——
    盘中 A 股还没收盘时，所有票都「落后 today」，那不叫 stale 那叫正常。
    """
    from data_engine.health import get_symbol_staleness
    from data_engine.storage.database import get_session

    market = market or infer_market_from_symbol(symbol)
    session = get_session()
    try:
        st = get_symbol_staleness(session, symbol, market, today)
    finally:
        session.close()

    behind = st.get("bars_behind")
    return block_from_fields(
        {"bars": QualityField(
            status=status_for_bars_behind(behind),
            source="db:daily_quote",
            as_of=st.get("symbol_latest"),
            missing_reason=("库里没有这只票的日线" if st.get("symbol_latest") is None
                            else None),
        )},
        source="db:daily_quote",
        as_of=st.get("symbol_latest"),
    )


def quote_block(quotes: list[dict], requested: list[str],
                failures: dict[str, str] | None = None) -> QualityBlock:
    """实时行情的质量 —— `quote` 块。

    Args:
        quotes: `fetch_quotes` 的 canonical 行（带 `source` / `as_of`）。
        requested: 本来要查哪些票 —— 用来算「谁没回来」。
        failures: `engine.get_realtime_quotes_with_status` 的第二个返回值
            `{market: reason}`。**这是 `fetch_failed` 与 `missing` 的分水岭**：
            有失败记录 = 确实试了且失败（fetch_failed）；没失败记录却少了票 =
            源正常但就是没有这只（missing，多半是退市/无效码）。
    """
    failures = failures or {}
    got = {q.get("symbol") for q in quotes}
    fields: dict[str, QualityField] = {}

    for sym in requested:
        if sym in got:
            row = next(q for q in quotes if q.get("symbol") == sym)
            fields[sym] = QualityField(
                status=FieldStatus.AVAILABLE,
                source=row.get("source"),
                # as_of 可能是 None（百度那类不给报价时刻的源）——照实传，
                # 不许拿 timestamp（抓取时刻）顶替。
                as_of=row.get("as_of"),
            )
        elif failures:
            reason = next(iter(failures.values()))
            fields[sym] = QualityField(
                status=FieldStatus.FETCH_FAILED,
                missing_reason=reason,
            )
        else:
            # 源没报错却没这只票 —— 是「查无此票」不是「抓取故障」。
            # 照抄蓝本的克制用法：别把「本来就没有」误报成故障，那会让人去修
            # 一个不存在的问题。
            fields[sym] = QualityField(
                status=FieldStatus.MISSING,
                missing_reason="全源均无此标的（多半是退市/无效码）",
            )
    return block_from_fields(fields)

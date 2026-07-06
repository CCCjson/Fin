"""
选股筛选类工具 —— 包 api/routes/screener 的多因子筛选（用户任意条件）。

分工：这里按用户给的条件筛出列表，不打分不荐股；「帮我推荐/今天买什么」
让位给 recommend_stocks（那边有荐股纪律 + 深度评级闸门）。
调用方式同 recommend_tools._fetch_screener_candidates（薄包 _run_screen）。
"""
from typing import Any, Optional

from pydantic import BaseModel, Field

from agents.registry import tool
from agents.tool_envelope import ToolEnvelope
from agents.turn_monitor import verdict_hook

# 常用因子写进 schema 描述，帮 LLM 直接映射用户口语（完整白名单见 SCREENER_FIELDS）
_FIELDS_DOC = (
    "可用字段：pe(市盈率静)/pe_ttm(市盈率TTM)/pb(市净率)/total_mv(总市值,元)/"
    "circ_mv(流通市值,元)/dividend_yield(股息率%)/roe/roa/net_margin(净利率)/"
    "gross_margin(毛利率)/revenue_yoy(营收增速%)/net_profit_yoy(净利润增速%)/"
    "debt_ratio(资产负债率%)/current_ratio(流动比率)/eps/bvps"
)


class ScreenFilterItem(BaseModel):
    # op 不用 Literal 强枚举：非法/未知 op 由业务层过滤进 ignored_conditions 并如实
    # 提示（宽容降级，不是硬性拒绝），schema 只保证结构（三个键都在、类型对）。
    field: str = Field(..., description="因子字段名，见工具描述")
    op: str = Field(..., description="gt/gte/lt/lte/eq/between")
    value: Any = Field(..., description="阈值；between 时给 [低,高] 数组")


class ScreenStocksArgs(BaseModel):
    filters: list[ScreenFilterItem] = Field(
        ..., min_length=1, description="筛选条件列表（AND），如 [{field:'pe',op:'lt',value:15}]")
    pool_id: Optional[str] = Field(
        None, description="可选：限定范围，sse50/csi300/csi500 或行业名（如 银行）")
    sort_by: Optional[str] = Field(None, description="排序字段（白名单内），默认不排序")
    sort_desc: bool = Field(True, description="是否降序，默认 true")
    limit: int = Field(20, ge=1, le=100, description="最多返回条数，默认 20")
    affordable_only: bool = Field(True, description="只留按当前资金买得起 1 手的票，默认 true")


@tool(
    name="screen_stocks",
    description=(
        "多因子选股筛选：按用户给的财务/估值条件（AND 关系）筛全市场或指定池，"
        "返回符合条件的股票列表。回答「筛出 PE<15 且 ROE>10 的银行股」这类条件查询；"
        "只筛不荐——「帮我推荐/今天买什么」请改用 recommend_stocks。" + _FIELDS_DOC
    ),
    args_model=ScreenStocksArgs,
    category="analysis",
    group="screener",
)
def screen_stocks(filters: list, pool_id: Optional[str] = None,
                  sort_by: Optional[str] = None, sort_desc: bool = True,
                  limit: int = 20, affordable_only: bool = True) -> ToolEnvelope:
    from screener_engine.service import (
        run_screen as _run_screen, ScreenRequest, Filter, SCREENER_FIELDS, OPS as _OPS,
    )

    limit = max(1, min(int(limit or 20), 100))
    valid, ignored = [], []
    for f in filters or []:
        field, op = f.get("field"), f.get("op")
        if field in SCREENER_FIELDS and op in _OPS:
            valid.append(Filter(field=field, op=op, value=f.get("value")))
        else:
            ignored.append(f"{field}({op})")
    if not valid:
        return ToolEnvelope(business_result="negative", message=f"没有合法的筛选条件。{_FIELDS_DOC}")

    rows = _run_screen(ScreenRequest(
        filters=valid, pool_id=pool_id,
        sort_by=sort_by if sort_by in SCREENER_FIELDS else None,
        sort_desc=bool(sort_desc), limit=limit,
        affordable_only=bool(affordable_only),
    ))

    used_fields = {f.field for f in valid} | ({sort_by} if sort_by in SCREENER_FIELDS else set())
    stocks = []
    for r in rows:
        item = {"symbol": r["symbol"], "name": r.get("name"), "price": r.get("price")}
        for field in used_fields:
            item[field] = r.get(field)
        stocks.append(item)

    summary = {
        "count": len(stocks),
        "conditions": [f"{f.field} {f.op} {f.value}" for f in valid],
        "pool_id": pool_id,
        "affordable_only": bool(affordable_only),
        "stocks": stocks,
    }
    if ignored:
        summary["ignored_conditions"] = ignored
    if not stocks:
        note = "没有符合全部条件的股票，可放宽条件重试"
        # 用了估值字段但估值快照为空时，真实原因是数据缺失而非条件太严
        val_fields = [f.field for f in valid
                      if SCREENER_FIELDS[f.field]["source"] == "valuation"]
        if val_fields and not _has_valuation_data():
            note = (f"估值快照为空，{'/'.join(val_fields)} 等估值字段暂无数据——"
                    "需先在选股器页面刷新全市场估值，或先只用财务字段(roe/净利率等)筛")
        summary["note"] = note
    return ToolEnvelope(data=summary)


@verdict_hook("screen_stocks")
def _screen_verdict(s: str):
    """筛出 0 只是完整答案（条件太严），note 已引导放宽条件，不该进坏 streak。"""
    return "ok" if '"conditions"' in s else None


def _has_valuation_data() -> bool:
    from sqlalchemy import func
    from data_engine.storage.database import get_session
    from data_engine.storage.models import StockValuation
    session = get_session()
    try:
        return bool(session.query(func.count(StockValuation.id)).scalar())
    except Exception:  # noqa: BLE001
        return True  # 查不到就别误导，维持默认提示
    finally:
        session.close()

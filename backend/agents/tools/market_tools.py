"""
大盘环境类工具 —— 盘面全景快照（指数/涨跌家数/涨停跌停/板块/资金流向）。

定位：回答「现在盘面怎么样/今天市场情绪如何」，是「该不该动手」的环境闸门；
个股报价用 get_realtime_quote，个股盘中强弱用 get_intraday_check。
四路数据全部复用现成抓取（东财，走快代理，不依赖 Clash）。
"""
from typing import Any, Dict, List, Optional

from loguru import logger
from pydantic import BaseModel

from agents.registry import tool
from agents.tool_envelope import ToolEnvelope

_EM_UT = "bd1d9ddb04089700cf9c27f6f7426281"


def _fetch_indices() -> List[Dict]:
    """主要指数实时（东财 ulist，走 net 层：失败只换快代理 IP 重试，绝不降级直连）。"""
    from net import domestic_json
    data = domestic_json(
        "https://push2.eastmoney.com/api/qt/ulist.np/get",
        params={
            "fltt": 2, "invt": 2, "fields": "f2,f3,f4,f6,f12,f14", "ut": _EM_UT,
            # 上证/深证成指/创业板/沪深300/中证500/科创50/恒生
            "secids": ("1.000001,0.399001,0.399006,1.000300,"
                       "1.000905,1.000688,100.HSI"),
        },
        timeout=10,
    )
    diff = ((data or {}).get("data") or {}).get("diff") or []
    return [{"code": it.get("f12"), "name": it.get("f14"),
             "price": it.get("f2"), "change_pct": it.get("f3"),
             "change_amount": it.get("f4"), "amount": it.get("f6")}
            for it in diff]


def _fetch_sector_boards(fid: str) -> List[Dict]:
    """行业板块榜（fid=f3 涨跌排序 / f62 主力净流入排序），东财 clist 走 net 层。"""
    from net import domestic_json
    data = domestic_json(
        "https://push2.eastmoney.com/api/qt/clist/get",
        params={"pn": 1, "pz": 100, "po": 1, "np": 1, "ut": _EM_UT,
                "fltt": 2, "invt": 2, "fid": fid, "fs": "m:90+t:2+f:!50",
                "fields": "f3,f14,f62,f128,f136,f184"},
        timeout=15,
    )
    diff = ((data or {}).get("data") or {}).get("diff") or []
    return [{"name": it.get("f14"), "change_pct": it.get("f3"),
             "leader": it.get("f128"), "leader_pct": it.get("f136"),
             "net_inflow": it.get("f62"), "net_inflow_pct": it.get("f184")}
            for it in diff]


def _top_bottom(rows: List[Dict], top_n: int, bottom_n: int,
                top_tag: str, bottom_tag: str,
                drop: tuple = ()) -> List[Dict]:
    bottom_start = max(top_n, len(rows) - bottom_n)
    out = [{**r, "rank": top_tag} for r in rows[:top_n]]
    out += [{**r, "rank": bottom_tag} for r in rows[bottom_start:]]
    return [_slim_row(r, drop) for r in out]


def _slim_row(row: Dict, drop: tuple) -> Dict:
    """去掉指定字段 + 浮点收敛（净流入取整、百分比留 2 位），控回灌 token。"""
    out = {}
    for k, v in row.items():
        if k in drop:
            continue
        if isinstance(v, float):
            v = round(v) if abs(v) >= 1000 else round(v, 2)
        out[k] = v
    return out


def _breadth_stats() -> Optional[Dict[str, Any]]:
    """全市场涨跌家数 + 涨停/跌停家数（复用共享 TTL 缓存的全市场快照）。"""
    from data_engine.fetchers.realtime import compute_statistics as _compute_statistics, fetch_a_share_realtime_cached
    rows = fetch_a_share_realtime_cached(ttl=60.0)
    if not rows:
        return None
    return _compute_statistics(rows)


class GetMarketPulseArgs(BaseModel):
    pass


@tool(
    name="get_market_pulse",
    description=(
        "大盘环境全景：主要指数实时行情、全市场涨跌家数与涨停/跌停家数、"
        "行业板块涨跌 Top、板块主力资金流入/流出 Top，并给一句话情绪判读。"
        "回答「现在盘面怎么样/今天市场情绪如何/该不该出手」的环境判断时用。"
        "个股报价请用 get_realtime_quote。非交易时段返回最近快照。"
    ),
    args_model=GetMarketPulseArgs,
    category="analysis",
    group="core",
)
def get_market_pulse() -> ToolEnvelope:
    from agents.tools.recommend_tools import _session_phase
    from agents.widgets import metric_cards_widget

    phase = _session_phase()
    summary: Dict[str, Any] = {
        "session_phase": phase,
        "as_of": "realtime" if phase == "intraday" else "最近收盘/延迟快照",
    }

    # 1) 大盘指数（东财 ulist，含沪深300/中证500/恒生）
    indices: List[Dict] = []
    try:
        indices = _fetch_indices()
    except Exception as e:  # noqa: BLE001 — 独立数据路，失败不拖累其它三路
        logger.warning(f"get_market_pulse 指数获取失败: {e}")
    summary["indices"] = indices

    # 2) 涨跌家数 + 涨停/跌停（全市场快照，约 5-8s，一次拉取多处共享）
    stats = None
    try:
        stats = _breadth_stats()
    except Exception as e:  # noqa: BLE001
        logger.warning(f"get_market_pulse 涨跌统计失败: {e}")
    summary["breadth"] = stats

    # 3+4) 行业板块涨跌 Top / 板块主力资金流 Top（失败优雅降级为空）
    try:
        summary["sectors"] = _top_bottom(_fetch_sector_boards("f3"),
                                         5, 3, "top", "bottom")
    except Exception as e:  # noqa: BLE001
        logger.warning(f"get_market_pulse 板块涨跌失败: {e}")
        summary["sectors"] = []
    try:
        summary["money_flow"] = _top_bottom(_fetch_sector_boards("f62"),
                                            5, 3, "inflow", "outflow",
                                            drop=("leader", "leader_pct"))
    except Exception as e:  # noqa: BLE001
        logger.warning(f"get_market_pulse 资金流失败: {e}")
        summary["money_flow"] = []

    if not indices and not stats:
        return ToolEnvelope(business_result="negative", message="盘面数据暂时全部获取失败，请稍后再试。")

    from analysis_engine.market_mood import mood_readout
    summary["mood"] = mood_readout(stats, indices)

    cards = []
    for i in indices[:3]:
        pct = i.get("change_pct")
        cards.append({"label": i.get("name") or i.get("code"),
                      "value": f"{pct:+.2f}%" if isinstance(pct, (int, float)) else "—",
                      "type": "neutral",
                      "positive": isinstance(pct, (int, float)) and pct >= 0})
    if stats:
        cards.append({"label": "涨/跌", "value": f"{stats['up']}/{stats['down']}",
                      "type": "neutral", "positive": stats["up"] >= stats["down"]})
        cards.append({"label": "涨停/跌停",
                      "value": f"{stats['limit_up']}/{stats['limit_down']}",
                      "type": "risk",
                      "positive": stats["limit_up"] >= stats["limit_down"]})
    return ToolEnvelope(data=summary, widget=metric_cards_widget(cards, title="🌡️ 大盘脉搏"))

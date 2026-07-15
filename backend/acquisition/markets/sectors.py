"""
东财板块榜 —— 行业板块 / 概念板块，clist/get 走 `net.domestic_json`（铁律：
快代理→轮换重试，绝不静默直连）。

13.4-2 S7c：板块榜真源归位 acquisition/markets。此前散在两处——
`agents/tools/market_tools._fetch_sector_boards`（行业，已走 net）与
`report_engine/web_searcher`（行业/概念/资金流，裸 requests）。concept 与 industry
只差 `t:2`/`t:3` 与字段，合于一处。
"""

_EM_UT = "bd1d9ddb04089700cf9c27f6f7426281"
_CLIST_URL = "https://push2.eastmoney.com/api/qt/clist/get"


def fetch_boards(fid: str) -> list[dict]:
    """行业板块榜原始行（fid=f3 涨跌排序 / f62 主力净流入排序），`t:2`。

    返回 [{name, change_pct, leader, leader_pct, net_inflow, net_inflow_pct}]，
    由调用方自行取 top/bottom（如 market_tools._top_bottom）。
    """
    from net import domestic_json
    data = domestic_json(
        _CLIST_URL,
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


def fetch_concept_boards() -> list[dict]:
    """概念板块榜原始行（按涨跌幅 f3 排序），`t:3`。

    返回 [{name, change_pct, up_count, down_count, leader, leader_pct}]，
    由调用方自行取 top/bottom。
    """
    from net import domestic_json
    data = domestic_json(
        _CLIST_URL,
        params={"pn": 1, "pz": 100, "po": 1, "np": 1, "ut": _EM_UT,
                "fltt": 2, "invt": 2, "fid": "f3", "fs": "m:90+t:3+f:!50",
                "fields": "f3,f14,f104,f105,f128,f136"},
        timeout=15,
    )
    diff = ((data or {}).get("data") or {}).get("diff") or []
    return [{"name": it.get("f14"), "change_pct": it.get("f3"),
             "up_count": it.get("f104"), "down_count": it.get("f105"),
             "leader": it.get("f128"), "leader_pct": it.get("f136")}
            for it in diff]

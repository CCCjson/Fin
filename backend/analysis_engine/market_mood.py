"""
大盘情绪判读 —— 由全市场涨跌家数 / 涨停跌停梯度给出一句话盘面情绪。

从 agents.tools.market_tools 下沉：工具层只负责取数与展示（大盘脉搏卡片），
「普涨/普跌/分化 + 涨停家数梯度」这类判定规则集中在分析引擎层，
判定阈值与措辞集中一处维护。
"""
from typing import Dict, List, Optional


def mood_readout(stats: Optional[Dict], indices: List[Dict]) -> str:
    """一句话情绪判读：普涨/普跌/分化 + 涨停家数梯度。"""
    if not stats:
        return "全市场统计暂不可用"
    up, down = stats.get("up", 0), stats.get("down", 0)
    lu, ld = stats.get("limit_up", 0), stats.get("limit_down", 0)
    if up > down * 2:
        tone = "普涨"
    elif down > up * 2:
        tone = "普跌"
    else:
        tone = "涨跌分化"
    if ld > lu:
        heat = "跌停多于涨停，情绪冰点，建议谨慎"
    elif lu >= 80:
        heat = f"涨停 {lu} 家，赚钱效应强"
    elif lu >= 30:
        heat = f"涨停 {lu} 家，情绪一般"
    else:
        heat = f"涨停仅 {lu} 家，情绪偏弱"
    sh = next((i for i in indices if "上证" in (i.get("name") or "")), None)
    idx_part = (f"上证 {sh['change_pct']:+.2f}%，" if sh and
                isinstance(sh.get("change_pct"), (int, float)) else "")
    return f"{idx_part}{tone}（涨{up}/跌{down}），{heat}"

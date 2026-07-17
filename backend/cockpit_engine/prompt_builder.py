"""
决策驾驶舱 — LLM 文字总结 prompt 构建

数值已由 scorer 算好，LLM 仅负责把五维结果叙述成人类可读的决策理由，
不重新打分、不改变结论。
"""
import json
from typing import Dict

# Prompt 口径的版本戳。**改本文件里任何 prompt 文案必须 bump。**
# 注意这只管「LLM 怎么叙述」；驾驶舱的**评级口径**版本是
# cockpit_engine/scorer.py 的 SCORER_VERSION（那才是真正产生买卖结论的地方）。
PROMPT_VERSION = "cockpit-v1"

SYSTEM_PROMPT = (
    "你是一位资深量化投资顾问。下面给你一只股票的「决策驾驶舱」结构化数据，"
    "其中综合评分、各维度分值、买卖建议、建议仓位、止损位，以及"
    "「基于用户真实总资金算出的建议买入金额/股数」都已由系统确定性算法算好。"
    "你的任务是：用简洁、专业、口语化的中文，把这些结论讲清楚——"
    "解释为什么是这个建议、各维度（技术/基本面/情感/ML/持仓）分别在说什么、"
    "需要注意的风险点。\n"
    "要求：①不要改变系统给出的建议、分值和金额；②缺失的维度要点明「数据不足」；"
    "③务必结合用户资金给出可执行操作——明确『按你的资金建议买 X 股(约 ¥Y)』，"
    "若 affordable=false（买不起 1 手）必须直说『以你目前的资金买不起这只，建议换更低价的标的』，"
    "不要给用户买不起的建议；④结尾一句含止损的操作建议；⑤控制在 300 字以内；"
    "⑥末尾附一句风险提示。"
)


def build_summary_prompt(cockpit: Dict) -> str:
    """把聚合结果序列化成 user prompt"""
    payload = {
        "股票": f"{cockpit.get('name')} ({cockpit.get('symbol')})",
        "综合评分": cockpit.get("composite"),
        "建议": cockpit.get("recommendation"),
        "目标仓位%": cockpit.get("suggested_position_pct"),
        "止损位": cockpit.get("stop_loss"),
        "现价": (cockpit.get("price") or {}).get("latest"),
        "估值": cockpit.get("valuation"),
        "我的总资金": cockpit.get("total_capital"),
        "可用现金": cockpit.get("available_cash"),
        "当前持仓": cockpit.get("current_position"),
        "按资金的买入建议": cockpit.get("suggested"),
        "五维": {
            k: {"分": v.get("score"), "详情": v.get("detail")}
            for k, v in (cockpit.get("dimensions") or {}).items()
        },
    }
    return (
        "请基于以下决策驾驶舱数据，给出文字解读：\n\n"
        + json.dumps(payload, ensure_ascii=False, indent=2)
    )

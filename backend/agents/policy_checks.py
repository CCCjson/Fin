"""
PolicyChecker —— 用代码校验业务规则，而不是靠提示词自觉遵守（Jason 定的原则）。

只在 orchestrator._loop 判定"本轮无 tool_calls，准备给最终回答"的收尾点介入：
扫描本 turn 内已经产生的工具调用/结果，判断最终回答是否符合两条可校验的规则：

1. 股票代码必须有本轮工具活动背书，不能凭记忆报代码（选股推荐纪律的可校验子集）。
2. 本轮发生过 subagent 调用时，最终收尾不能太长（subagent 报告已经完整流式展示
   给用户了，收尾复述是重复劳动，曾导致"重复+矛盾建议"）。

局限（诚实说明，不假装能代码兜底）：
- 规则 1 只能拦"裸代码"，拦不住不带代码的文字推荐（比如"我觉得贵州茅台不错"）；
  这类纯语义判断没有可校验的不变量，继续留在 skills/monitor.md 的提示词里。
- 查询 vs 推荐 vs 筛选该走哪个工具，是意图理解问题，本模块完全不碰。

回滚开关：环境变量 AGENT_POLICY_CHECK=off。
"""
import os
import re
from typing import Optional

# 不用 \b：中文与数字在 Python 的 Unicode 感知正则里都算"词字符"，紧挨中文的代码
# （中文场景下极常见，如"买入600519.SH，"）会因为两侧都是词字符而没有边界，\b 会
# 匹配失败。改用显式的"前面不是数字/后面不是字母数字"负向断言。
_STOCK_CODE_RE = re.compile(r"(?<!\d)\d{6}\.(?:SH|SZ)(?![A-Za-z0-9])")

# 「子任务出的是成品，主 agent 收尾只能一句话」这条规则**只适用于自成一篇的子任务**。
# 五个报告章节 subagent（report_market/news/positions/strategy/picks）**故意不在此列**：
# 废掉全量报告后，Ch1「纵览 & 操作计划」正是要 MoneyBill 看着几章摘要亲自写出来的，
# 把它们加进来会把纵览强行压成一句话。
_SUBAGENT_NAMES = {"run_deep_stock", "run_news_analysis", "run_alpha_lab"}
_SUBAGENT_TAIL_MAX_CHARS = 150


def policy_check_enabled() -> bool:
    return os.getenv("AGENT_POLICY_CHECK", "on").lower() not in ("off", "0", "false")


def _backed_symbols(turn_messages: list[dict]) -> set[str]:
    """本 turn 内"有依据"的股票代码集合：出现在任意 tool_call 的参数里，或出现在
    任意 tool 结果文本里的代码——只要模型这轮真的对某个代码调用过/查到过东西，
    就不算凭记忆瞎报。用正则扫文本而不是严格 JSON 解析，因为 tool 结果内容可能
    被 truncate_json_safe 截断成非严格 JSON（截断标注文字会破坏 json.loads）。"""
    backed: set[str] = set()
    for m in turn_messages:
        if m.get("role") == "assistant":
            for tc in (m.get("tool_calls") or []):
                args_str = (tc.get("function") or {}).get("arguments") or ""
                backed |= set(_STOCK_CODE_RE.findall(args_str))
        elif m.get("role") == "tool":
            content = m.get("content")
            if isinstance(content, str):
                backed |= set(_STOCK_CODE_RE.findall(content))
    return backed


def _had_subagent_call(turn_messages: list[dict]) -> bool:
    for m in turn_messages:
        if m.get("role") != "assistant":
            continue
        for tc in (m.get("tool_calls") or []):
            name = (tc.get("function") or {}).get("name")
            if name in _SUBAGENT_NAMES:
                return True
    return False


class PolicyChecker:
    def check(self, session, final_text: str) -> Optional[str]:
        """返回 None=放行；返回非空字符串=注入这条 system 提示并强制多跑一轮。"""
        if not final_text:
            return None
        turn_messages = session.messages[session.turn_start_idx:]

        unbacked = set(_STOCK_CODE_RE.findall(final_text)) - _backed_symbols(turn_messages)
        if unbacked:
            codes = "、".join(sorted(unbacked))
            return (
                f"（系统提示：你的回答里出现了股票代码 {codes}，"
                "但本轮没有任何工具调用/结果涉及这些代码——推荐/信号类结论只能来自"
                "真实调用的工具输出，不能凭记忆报代码。请基于已调用工具的真实结果重新回答；"
                "如果这些代码确实需要提及，先调用相应工具查证。）"
            )

        if _had_subagent_call(turn_messages) and len(final_text) > _SUBAGENT_TAIL_MAX_CHARS:
            return (
                "（系统提示：子任务的完整报告已经流式展示给用户了，你刚才的收尾复述过长。"
                "收尾只能是一句话，禁止复述报告里的指标/价位/结论，禁止另给操作建议。"
                "请重新给出一句简短收尾。）"
            )
        return None

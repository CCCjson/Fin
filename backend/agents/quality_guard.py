"""MoneyBill 最终回答的数据质量硬传导 —— 「数据不可信时不许声称高把握」。

## 和 PolicyChecker 是两回事，别合并

`policy_checks.py` 是**软纠偏**：发现问题 → 注入 system 提示 → 让 LLM **重答一遍**。
本模块是**硬约束**：发现问题 → **代码自己追加一条更正**，不求 LLM 配合。

为什么不能走 PolicyChecker 那条路（三个理由，都实测过）：

1. **文本是边流边发的**（`orchestrator.py:157` 每个增量立刻 yield）。等我们看到完整
   文本时，Jason 屏幕上早就有了 —— **发出去的收不回**。
2. **nudge 会让用户看到两段文本**。前端 `useAgentChat.ts:72-77` 是 append-only 累加，
   `continue` 重跑一轮 = 模型重新流式输出一整段，前一段错的还在上面挂着。
3. **`policy_nudged` 是每 turn 一次的全局闸**，本模块要抢它的配额；而数据降级的
   触发频率远高于「凭记忆报代码」。

所以走「追加一条更正 chunk」：零 LLM 依赖、不重跑、不产生重复文本，**代码说了算**。
代价是那段乐观的话仍然被 Jason 看见了 —— 但紧跟着一条系统更正，比「悄悄地没人管」
强得多。真要根治得让 confidence 别走自由文本（那是更大的改动，见 doc14 P3-1）。

## 为什么不抄蓝本的否定检测

蓝本 `phase_decision_guardrail.py` 用「marker + 回看窗口 endswith 否定词」在中文里
猜方向。两个毛病：
  - 否定词表末位有个裸 `"不"` → `"不得不立即买入"` 的 prefix `"不得不"` endswith
    `"不"` → 被判成否定 → **护栏漏放**。
  - grep 确认蓝本 `tests/` 里**没有任何测试覆盖那段**（唯一沾边的用例走的是
    `decision_type` 兜底路径，`_is_negated_marker` 一次都没被调用）。
    它是 doc14 反复引用的卖点，实际是未经测试的代码。

我们不猜方向 —— 只找「声称高把握」的**措辞**，那是个正面模式，不涉及否定。
真要判方向，`DecisionLog.action` 是结构化的，直接读。
"""
from __future__ import annotations

import os
import re

# 「我很有把握」的措辞。**只匹配正面声称，不做否定检测**（见模块 docstring）。
#
# 刻意窄：宁可漏判也不误伤。「这只票很强势」不算声称把握（那是对标的的判断），
# 「我非常确定它会涨」才算（那是对自己判断的元断言）。漏判的代价是少一条更正，
# 误伤的代价是每次正常回答后面都挂一句莫名其妙的系统提示 —— 后者更坏。
_HIGH_CONFIDENCE_PATTERNS = [
    r"置信度\s*[:：]?\s*高",
    r"高置信",
    r"很有把握", r"非常有把握", r"极有把握",
    r"非常确定", r"十分确定", r"可以确定",
    r"确定性(?:很|极)高",
    r"把握(?:很|极)大",
    r"(?:强烈|坚定)(?:建议|看好)",
]
_HIGH_CONFIDENCE_RE = re.compile("|".join(_HIGH_CONFIDENCE_PATTERNS))

# 更正文案。**跟着质量降级的具体内容走**，不是一句空泛的「请注意风险」——
# 那种话说多了就是噪声，Jason 会学会无视它。
_CORRECTION = (
    "\n\n---\n"
    "⚠️ **系统更正（非 AI 生成）**：本轮结论所依赖的核心数据已降级（{limitations}），"
    "上文中的高把握表述**不成立**。数据质量分 {score}/100（{level}）。"
    "请把这条建议当作「数据待补齐后再确认」，不要据此下单。"
)


def quality_guard_enabled() -> bool:
    """回滚开关，同 `AGENT_POLICY_CHECK` 的范式。"""
    return os.getenv("AGENT_QUALITY_GUARD", "on").lower() not in ("off", "0", "false")


def claims_high_confidence(text: str) -> bool:
    """这段回答有没有声称高把握。"""
    return bool(text) and bool(_HIGH_CONFIDENCE_RE.search(text))


def worst_turn_quality(qualities: list[dict]) -> dict | None:
    """本 turn 各工具上报的数据质量里**最差**的那份。

    木桶取短板：一轮里调了五个工具，只要有一个说「核心数据降级」，这轮的结论就
    建立在降级数据上。取最差而不是取平均 —— 平均会让一个健康工具把一个烂工具
    冲淡成「大体还行」，正是 P0-2 要消灭的那种稀释。

    Returns:
        最差的那份 quality dict；本轮没有任何工具上报质量则 None（→ 不更正，
        没有证据说数据不好就不许瞎报）。
    """
    worst = None
    for q in qualities:
        if not isinstance(q, dict):
            continue
        if worst is None or _is_worse(q, worst):
            worst = q
    return worst


def _is_worse(a: dict, b: dict) -> bool:
    """a 比 b 更差？core_degraded 优先于分数 —— 它是硬传导的触发条件。"""
    if bool(a.get("core_degraded")) != bool(b.get("core_degraded")):
        return bool(a.get("core_degraded"))
    return (a.get("overall_score") or 0) < (b.get("overall_score") or 0)


def correction_for(final_text: str, quality: dict | None) -> str | None:
    """要不要追加一条更正？要的话返回文案，不要返回 None。

    两个条件缺一不可：**核心数据降级** 且 **回答声称了高把握**。
    数据降级但回答本来就很谨慎 → 不用更正（它已经诚实了，别啰嗦）。
    """
    if not quality or not quality.get("core_degraded"):
        return None
    if not claims_high_confidence(final_text):
        return None
    limitations = quality.get("limitations") or ()
    return _CORRECTION.format(
        limitations="；".join(str(x) for x in limitations) or "核心数据不可信",
        score=quality.get("overall_score", "?"),
        level=quality.get("level", "?"),
    )

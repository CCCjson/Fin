"""`DecisionLog.source` 的单一真源 —— 「这一行是**谁**写的」。

与 `common/decision_kind.py` 是一对：

    source     = 谁写的（advisor / cockpit / crypto / ...）
    entry_kind = 写的是什么（advice / execution / ops）

**为什么要有这个模块（S0 §2.2）**：`agents/tools/decision_tools.py` 的 `_SOURCES`
是一串**手写 Literal**，靠人记得同步 —— 于是它漏了 `crypto`、`crypto_cockpit`、
`crypto_earn`，后果是 **MoneyBill 根本没法按 crypto 查胜率**（pydantic 直接拒掉参数）。

⚠️ 一处更正：曾经写过「crypto 的置信度校准永远只看 cockpit」——**不对**。
打分链路上 `crypto_intel_engine/cockpit.py` 早就在调 `get_calibration_factor("crypto_cockpit")`，
校准本身是好的。缺的只有**查询/复盘这条读路径**。

⚠️ 这与 `entry_kind` 那颗炸弹是**同款成因**：靠命名约定 + 人肉同步的分类，
下一个新 source 照样会漏。所以这里建表 + 上门禁（`tests/test_decision_source_registry.py`）：
新增写入点用了没登记的 source → 门禁红。

放 `common/` 是分层铁律（`docs/CODING_STANDARDS.md` §0）：只有最底层能同时被
`decision_log.py`、`agents/tools/` 和各引擎 import 而不产生反向依赖。
"""
from __future__ import annotations

from typing import Any

from common.decision_kind import ADVICE, EXECUTION, OPS

# ── source 全表 ───────────────────────────────────────────────────────────
#
# `kinds` 是**集合**而不是单值，因为 `moneybill` 天生是混的：`agents/confirm_gate.py`
# 对每一次经确认的工具调用留痕，下单记 execution、加自选股/建预警记 ops
# （运行期按 `CONFIRMED_TOOL_KINDS` 分流）。硬写成单值会立刻撒谎。
#
# `advice_hint`：这个 source 里没有 AI 建议时，该去哪儿找。用于 §2.3 的「指路」——
# LLM 想查 crypto 胜率却传了 `source="crypto"`（那是订单回执），拿到 0 条 + 空 stats
# 会回答「没有 crypto 记录」。**静默的空结果比报错糟**，所以要指路。
SOURCES: dict[str, dict[str, Any]] = {
    # ── AI 的可证伪断言（进胜率分母）──
    "advisor": {
        "label": "AI 投资顾问",
        "kinds": frozenset({ADVICE}),
        "advice_hint": None,
    },
    "cockpit": {
        "label": "决策驾驶舱（股票）",
        "kinds": frozenset({ADVICE}),
        "advice_hint": None,
    },
    "crypto_cockpit": {
        "label": "决策驾驶舱（加密货币）",
        "kinds": frozenset({ADVICE}),
        "advice_hint": None,
    },
    "moneybill_recommend": {
        "label": "MoneyBill 选股推荐",
        "kinds": frozenset({ADVICE}),
        "advice_hint": None,
    },
    "report_picks": {
        "label": "研究报告选股",
        "kinds": frozenset({ADVICE}),
        "advice_hint": None,
    },
    # ── 既成事实（不进胜率分母）──
    "moneybill": {
        # confirm_gate 的留痕：下单 → execution，加自选股/建预警/编策略 → ops
        "label": "MoneyBill 对话操作回执（下单 / 加自选 / 建预警…）",
        "kinds": frozenset({EXECUTION, OPS}),
        "advice_hint": "moneybill_recommend",
    },
    "crypto": {
        "label": "币安现货订单回执（成交 / 挂单）",
        "kinds": frozenset({EXECUTION}),
        "advice_hint": "crypto_cockpit",
    },
    "crypto_earn": {
        "label": "币安理财申购/赎回",
        "kinds": frozenset({OPS}),
        "advice_hint": "crypto_cockpit",
    },
}

ALL_SOURCES: tuple[str, ...] = tuple(sorted(SOURCES))

# 只含 AI 建议的 source —— 「胜率 / 校准该查哪些来源」的唯一答案。
ADVICE_SOURCES: tuple[str, ...] = tuple(
    s for s in ALL_SOURCES if ADVICE in SOURCES[s]["kinds"]
)


def is_known(source: str | None) -> bool:
    return (source or "").strip() in SOURCES


def kinds_of(source: str | None) -> frozenset[str]:
    """这个 source 底下可能出现哪些 entry_kind。未登记 → 空集（**别猜**）。"""
    spec = SOURCES.get((source or "").strip())
    return spec["kinds"] if spec else frozenset()


def label_of(source: str | None) -> str:
    spec = SOURCES.get((source or "").strip())
    return spec["label"] if spec else (source or "")


def has_advice(source: str | None) -> bool:
    """这个 source 里有没有可评胜率的 AI 建议。"""
    return ADVICE in kinds_of(source)


def advice_alternative(source: str | None) -> str | None:
    """这个 source 没有建议时，AI 建议在哪个 source 里。"""
    spec = SOURCES.get((source or "").strip())
    return spec.get("advice_hint") if spec else None


def describe_for_prompt() -> str:
    """给工具 description 用的一行式清单 —— **由表生成，不手抄**。

    手抄的下场就是 `_SOURCES` 那串漏了 4 个的 Literal。这里生成一次，
    表一改文案自动跟上；门禁再补一刀防有人改回硬编码。
    """
    return "；".join(f"{s}={SOURCES[s]['label']}" for s in ALL_SOURCES)

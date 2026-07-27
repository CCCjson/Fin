"""成交的**来源归因**真源 —— 「这笔单是从哪儿来的」。

与 `common/decision_kind.py`（这条留痕记的是什么）、`common/decision_source.py`
（这条留痕是谁写的）凑成三件套，但管的是**另一张表**：`crypto_trades`。

# 为什么要有（S1 §2）

「我那条 BTC 策略赚了多少」这个问题，在此之前**算不出来**：

    crypto_strategy_runs.pnl_realized_today
        = cost_basis.realized_on()          ← 遍历所有 symbol = **整个账户**当日盈亏
    卫冕者下的单 + Jason 在 MoneyBill 手动下的单 + 待确认单成交，全混在同一个币安账户里

⛔ **别看到 `pnl_realized_today` 这个字段名就拿来当策略收益用。** 它喂账户级熔断是对的。

而卡片原本设想的归因链路 **实测是断的**：

    run.executed_order_ids        实际存的是待确认单的 `CPO-…` 引用，**不是币安 orderId**
            ↓                     （模型 docstring 写的「币安 orderId 列表」是句假话）
    CryptoPendingOrder.executed_order_id   ← 这张桥表的 FILLED 行 **24 小时后被 cleanup 删掉**
            ↓
    CryptoTrade.order_id

也就是说超过 24 小时，「这笔成交属于哪条策略」**永久查不回来**。所以归因必须写在
**成交台账本身**（`CryptoTrade.source_kind` / `source_ref`），而不是靠事后 join 桥表。

⭐ 这一套同时是 S4「这笔单是 AI 建议还是 Jason 自主」的答案 —— 两者是同一个问题的两个
切面（一笔交易从哪儿来）。**别为 S4 再建第二套**（S1 §2.4：分两次建必然长歪）。
"""
from __future__ import annotations

# ── 取值 ──────────────────────────────────────────────────────────────────
# 自动策略引擎排的单。`source_ref` = 策略号 `CS-<ts>-<hex6>`。
# ⭐ 直接存策略号而不是 CPO 引用：策略盈亏是**按策略聚合**的，存 CPO 还得再 join 一次
# 那张会被清理的桥表 —— 那正是这套东西要绕开的病。
STRATEGY = "strategy"
# 经 MoneyBill 对话下的单。
# ⚠️ **它的含义是「渠道」不是「谁拍的板」**：AI 在环，但确认键是 Jason 按的。
# 别把它读成「AI 决定买的」—— 那会让 S4 的「AI vs 自主」统计从一开始就偏。
# `source_ref` 目前留空：`agents/confirm_gate.py` 是在工具**返回之后**才写 DecisionLog，
# 下单那一刻还没有 decision_id。把它接上属于 S4。
AI_ADVICE = "ai_advice"
# Jason 自己在别处下的单，事后补录进系统。
MANUAL = "manual"
# 对账产生的调整行。
RECONCILE = "reconcile"
# 判不出来 / 存量行。**不是给新写入点用的默认值**，见 `normalize()`。
UNKNOWN = "unknown"

TRADE_SOURCES = frozenset({STRATEGY, AI_ADVICE, MANUAL, RECONCILE, UNKNOWN})

# 这些来源**必须**带 `source_ref`，否则归因等于没做（知道「来自某条策略」但不知道哪条）。
REF_REQUIRED = frozenset({STRATEGY})


def normalize(kind: str | None) -> str:
    """非法/缺省值一律回落到 `unknown`。

    ⚠️ **注意这里和 `decision_kind.normalize()` 的方向是相反的，别照抄。**

    那边回落到 `advice`（「宁可多评一条噪声，也不能把真建议静默排除在评估外」）；
    这边回落到 `unknown` —— 因为猜错的代价不对称：把一笔来路不明的成交算进某条策略，
    等于**凭空捏造它的战绩**，而战绩是要拿来决定「切不切换策略」的。

    宁可让某条策略的盈亏显示「有 N 笔来源不明没算进去」，也不能给它记一笔不属于它的钱。
    """
    k = (kind or "").strip().lower()
    return k if k in TRADE_SOURCES else UNKNOWN


def needs_ref(kind: str | None) -> bool:
    return normalize(kind) in REF_REQUIRED


LABELS: dict[str, str] = {
    STRATEGY: "自动策略引擎",
    AI_ADVICE: "MoneyBill 对话下单（AI 在环，Jason 确认）",
    MANUAL: "手动补录",
    RECONCILE: "对账调整",
    UNKNOWN: "来源不明",
}


def label_of(kind: str | None) -> str:
    return LABELS[normalize(kind)]

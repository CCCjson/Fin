"""`DecisionLog.entry_kind` 的单一真源 —— 「这一行记的是哪一类事」。

**为什么要有这个模块（P0-4）**：`decision_logs` 里躺着三类语义完全不同的行，
在此之前它们靠 `source` 的命名约定区分，于是：

    crypto 的**成交回执**（entry_price = 成交价）和 AI 的**买卖建议**
    （entry_price = 建议入场价）共用同一张表 + 同一个评估管道。

后果是 39 条 `BTCUSDT.BN BUY entry=100.0` 的假成交排队等着被 `outcome_eval` 评成
`(65000-100)/100 ≈ +64900%` 的 win，占全表 40%，直接架空 P0-3 的校准
（`compute_calibration` 只下调不上抬 → 100% 命中率 → factor 恒 1.0，**永久失效且不报错**）。

⛔ **别再用 source 分类**：`source` 是「**谁**写的」，`entry_kind` 是「写的是**什么**」。
下一个新 source 一定会忘（这次就是这么出的问题）。

放 `common/` 是分层铁律（`docs/CODING_STANDARDS.md` §0，`engines → acquisition →
common/net`）：只有最底层能同时被 `decision_log.py`、`agents/confirm_gate.py` 和各
引擎 import 而不产生反向依赖。
"""
from __future__ import annotations

# ── 三个取值 ──────────────────────────────────────────────────────────────
# AI 的**可证伪断言**：「买茅台，入场 1650，止损 1560」。P0-1 后验评估的唯一对象。
ADVICE = "advice"
# 订单回执：成交 / 挂单 / 补录真实成交。**已发生的事实，没有对错可评** ——
# 拿它算胜率等于问「这笔成交的准确率是多少」。
EXECUTION = "execution"
# 非交易操作：加删自选股 / 建删预警 / 改设置 / 编策略。连价格都没有。
OPS = "ops"

ENTRY_KINDS = frozenset({ADVICE, EXECUTION, OPS})

# **只有 advice 进后验评估 / 胜率 / 置信度校准。**
# 单元素集合是刻意的：写成集合而不是 `== ADVICE`，是为了让「以后要不要把
# execution 也纳入某种统计」变成改这一行，而不是全库 grep 相等判断。
EVALUABLE_KINDS = frozenset({ADVICE})


def normalize(kind: str | None) -> str:
    """非法/缺省值一律回落到 `advice`。

    **为什么回落到 advice 而不是丢弃**：留痕绝不能因为分类不认识就少记一行
    （`record_decision` 的设计原则是吞掉一切异常不坏主流程）。回落到 advice 是
    「宁可多评一条也不少留一条」——多评的后果是一条噪声，少留的后果是审计断线。
    真正防脏值的是 `tests/` 里那条门禁：写入点必须显式归类。
    """
    k = (kind or "").strip().lower()
    return k if k in ENTRY_KINDS else ADVICE


# ── 确认门（agents/confirm_gate.py）的工具归类表 ──────────────────────────
#
# confirm_gate 对**每一次经确认的工具调用**留痕，而受确认门管的 9 个工具里只有 3 个
# 是真的下单。其余 6 个（加自选股、建预警、改设置、编策略…）没有方向、没有入场价，
# 记进胜率分母纯属噪声。
#
# ⚠️ **这张表必须覆盖全部 `requires_confirmation=True` 的工具**，由
# `tests/test_decision_entry_kind.py::test_every_confirmed_tool_is_classified` 钉死：
# 新加一个受确认门的工具却忘了归类 → 门禁红。**别把它改成「默认 ops」就完事** ——
# 「靠默认值兜底」正是 P0-4 那颗炸弹的成因。
CONFIRMED_TOOL_KINDS: dict[str, str] = {
    # 真·下单 / 真·记账 —— 钱动了
    "place_order": EXECUTION,
    "place_crypto_order": EXECUTION,
    "record_manual_trade": EXECUTION,        # 补录券商 App 的真实成交，也是既成事实
    # 非交易操作
    "add_to_watchlist": OPS,
    "remove_from_watchlist": OPS,
    "create_price_alert": OPS,
    "delete_price_alert": OPS,
    "update_setting": OPS,
    "compile_crypto_strategy": OPS,          # 编一条策略 DSL，不等于下单
    # S2「策略变更必须人工审核」的两个承载物。仍是 ops：改策略/上线策略没有方向、
    # 没有入场价，连价格都没有 —— 拿它们进胜率分母纯属噪声。
    # ⚠️ 但别因此觉得它们「不重要」：`arm_crypto_strategy` 是整条自动交易链路的开关。
    "apply_strategy_proposal": OPS,          # 把提案落成新版本（草稿，不上线）
    "arm_crypto_strategy": OPS,              # 让某个版本上 live（旧版本自动停跑）
    "set_strategy_benchmark": OPS,           # 标记基准线，不动钱也不动策略内容
}


def kind_for_confirmed_tool(tool_name: str | None) -> str:
    """确认门里那次工具调用该记成哪一类。

    未登记的工具回落到 `OPS` —— 一个没进过分类表的新工具**绝不能**被当成 AI 建议
    混进胜率分母（那正是本卡要修的病）。漏登记这件事本身由门禁抓，不靠运行期兜。
    """
    return CONFIRMED_TOOL_KINDS.get((tool_name or "").strip(), OPS)

"""DSL 条件树纯函数评估器 —— 无网络、无副作用、离线可测。

输入：一个 `ConditionGroup` + 一张**分析卡** dict。
输出：(是否命中, 命中的条件人话列表)。数据缺失（原语取值 None）一律视为该条不满足，
绝不猜——宁可不下单也不拿脏数据成交。

## ⭐ 市场无关（S5 批次1）

条件树只做「取值 + 比较」，跟标的是什么毫无关系。绑市场的只有**字段注册表**：
crypto 的卡来自 `analyze_crypto_symbol`，股票的来自 `CockpitAggregator.aggregate`。
所以这里加了个 `market` 参数走 `common/strategy_fields` 查表。

⚠️ 默认值是 `CRYPTO`，纯粹为了**不动现有调用方**（那是正在跑真钱的代码）。
新代码请显式传 market。
"""
from typing import Any

from common.market import CRYPTO
from common.strategy_fields import resolve as _resolve_by_market
from crypto_intel_engine.dsl import Condition, ConditionGroup


def _apply_op(op: str, actual: Any, expected: Any) -> bool:
    if actual is None:
        return False           # 缺数据 = 不满足（不猜）
    try:
        if op == "gt":
            return actual > expected
        if op == "gte":
            return actual >= expected
        if op == "lt":
            return actual < expected
        if op == "lte":
            return actual <= expected
        if op == "eq":
            # 类别型字符串忽略大小写；数值型直接比
            if isinstance(actual, str) or isinstance(expected, str):
                return str(actual).lower() == str(expected).lower()
            return actual == expected
        if op == "in":
            vals = [str(x).lower() for x in expected]
            return str(actual).lower() in vals
        if op == "between":
            lo, hi = expected
            return lo <= actual <= hi
    except TypeError:
        return False
    return False


_OP_LABELS = {"gt": ">", "gte": "≥", "lt": "<", "lte": "≤", "eq": "=", "in": "属于", "between": "介于"}
_VALUE_LABELS = {
    "bull": "牛市", "bear": "熊市", "unknown": "未知",
    "BUY": "买入", "SELL": "卖出", "HOLD": "持有",
    "pass": "通过", "caution": "谨慎", "avoid": "回避",
}


def _v(x: Any) -> str:
    return _VALUE_LABELS.get(str(x), str(x))


def _cond_label(c: Condition, actual: Any, ok: bool, market: str = CRYPTO) -> str:
    """把一条判定翻成人话（别露原始字段名/算子），供待确认单与运行日志展示。"""
    from common.strategy_fields import registry_for
    mark = "✓" if ok else "✗"
    name = registry_for(market).labels.get(c.field, c.field)
    op = _OP_LABELS.get(c.op, c.op)
    pct = "%" if c.field.endswith("_pct") else ""
    if c.op == "in" and isinstance(c.value, (list, tuple)):
        val = "/".join(_v(x) for x in c.value)
    elif c.op == "between" and isinstance(c.value, (list, tuple)) and len(c.value) == 2:
        val = f"{c.value[0]}{pct}~{c.value[1]}{pct}"
    else:
        val = f"{_v(c.value)}{pct}"
    act = f"{_v(actual)}{pct}" if actual is not None else "缺"
    return f"{mark} {name} {op} {val}（实际：{act}）"


def eval_condition(c: Condition, analysis: dict, market: str = CRYPTO) -> tuple[bool, str]:
    actual = _resolve_by_market(market, c.field, analysis)
    ok = _apply_op(c.op, actual, c.value)
    return ok, _cond_label(c, actual, ok, market)


def evaluate(group: ConditionGroup, analysis: dict,
             market: str = CRYPTO) -> tuple[bool, list[str]]:
    """组命中 = all_of 全过 且（any_of 空 或 至少一过）。返回 (matched, 每条判定人话)。

    ⚠️ `market` 决定用哪张字段表。传错的话所有条件都会**取不到值 → 静默不满足**
    （DSL 语义就是「缺数据 = 不满足」），看上去像「行情没到条件」。
    编译期由 `StrategySpec` 的 validator 挡住，运行期这里不再重复校验。
    """
    fired: list[str] = []

    all_pass = True
    for c in group.all_of:
        ok, label = eval_condition(c, analysis, market)
        fired.append(label)
        if not ok:
            all_pass = False

    any_pass = True
    if group.any_of:
        any_pass = False
        for c in group.any_of:
            ok, label = eval_condition(c, analysis, market)
            fired.append(f"（任一）{label}")
            if ok:
                any_pass = True

    return (all_pass and any_pass), fired

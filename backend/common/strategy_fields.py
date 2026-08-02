"""策略 DSL 的**字段注册表**——按市场分叉的那一层（S5 批次1）。

## 为什么要有这一层

crypto 的 DSL（`crypto_intel_engine/dsl.py`）里，条件树（`Condition` /
`ConditionGroup` / `evaluate`）是**完全市场无关**的 —— 它只做「取值 + 比较」。
真正绑死 crypto 的只有一样东西：**字段注册表**
（`FIELD_RESOLVERS` / `FIELD_LABELS` / `CATEGORICAL_FIELDS`），
因为它是按 `analyze_crypto_symbol` 的返回形状写的。

股票的分析卡是 `cockpit_engine.CockpitAggregator.aggregate()`，形状**高度同构**
（都有 composite / recommendation / dimensions / price / 仓位），但字段名不同：
crypto 有资金费率和大户持仓比，股票有估值和财务。

所以这一层做的事只有一件：**让「哪些字段可用、怎么取值」按市场查表**，
条件树本身一行都不用改。

⛔ **别把股票字段塞进 crypto 那张表**（反之亦然）。混在一起的话，
一条 crypto 策略能写出 `pe_ttm < 20` 这种**永远取不到值**的条件，
而 DSL 的语义是「取不到 = 不满足」—— 于是它静默地永远不触发，
看上去像「行情没到条件」，其实是字段根本不存在。

## ⚠️ 缺失就是缺失

`resolve_field` 取不到值一律返回 `None`，由 `evaluate` 判成「该条不满足」。
⛔ 别在这里给默认值 —— 「数据没取到」和「值恰好是 0」必须可区分，
这是全项目反复踩过的那类坑。
"""
from collections.abc import Callable
from typing import Any

from common.market import A_SHARE, CRYPTO, US_STOCK


def _g(d: dict | None, *path: str) -> Any:
    """安全逐层取值，任一层不是 dict 或缺失 → None。"""
    cur: Any = d
    for k in path:
        if not isinstance(cur, dict):
            return None
        cur = cur.get(k)
    return cur


# ──────────────────── 股票字段注册表 ────────────────────
#
# 取值源：`CockpitAggregator.aggregate(symbol)` 的返回 dict。
# ⚠️ 与 crypto 那张表**刻意保持同名同义**的有：composite / raw_composite /
#    recommendation / price.change_5d_pct / price.change_20d_pct /
#    current_position_pct / suggested_position_pct / dim.technical / dim.sentiment
#    —— 同一个概念在两个市场用同一个名字，AI 写规则时不用记两套。

STOCK_FIELD_RESOLVERS: dict[str, Callable[[dict], Any]] = {
    # 综合与维度
    "composite": lambda r: _g(r, "composite"),
    "raw_composite": lambda r: _g(r, "raw_composite"),
    "dim.technical": lambda r: _g(r, "dimensions", "technical", "score"),
    "dim.fundamental": lambda r: _g(r, "dimensions", "fundamental", "score"),
    "dim.sentiment": lambda r: _g(r, "dimensions", "sentiment", "score"),
    "dim.position": lambda r: _g(r, "dimensions", "position", "score"),
    # 价格
    "price.change_5d_pct": lambda r: _g(r, "price", "change_5d_pct"),
    "price.change_20d_pct": lambda r: _g(r, "price", "change_20d_pct"),
    # 估值（股票独有；crypto 没有市盈率这回事）
    "valuation.pe": lambda r: _g(r, "valuation", "pe"),
    "valuation.pe_ttm": lambda r: _g(r, "valuation", "pe_ttm"),
    "valuation.pb": lambda r: _g(r, "valuation", "pb"),
    "valuation.total_mv": lambda r: _g(r, "valuation", "total_mv"),
    "valuation.circ_mv": lambda r: _g(r, "valuation", "circ_mv"),
    # 仓位
    "current_position_pct": lambda r: _g(r, "current_position", "pct"),
    "suggested_position_pct": lambda r: _g(r, "suggested", "target_pct"),
    # 类别型
    "recommendation": lambda r: _g(r, "recommendation"),
    # ⭐ 数据质量是**可以写进规则**的：`data_quality.level in [good, usable]`
    #    能让一条策略在数据不可信时自己退出，而不是拿脏数据交易。
    "data_quality.level": lambda r: _g(r, "data_quality", "level"),
    "data_quality.score": lambda r: _g(r, "data_quality", "overall_score"),
}

# ⛔ **`dim.ml` 刻意不在上表里。**
#
# `CockpitAggregator._score_ml` 在没有训练模型时返回 None，而这个项目
# **一个训好的模型都没有**（P1-6「ML 25% 是幽灵维度」未开工）。
# 暴露它 = 邀请 AI 写出**永远不触发**的规则，而 DSL 的语义是「取不到 = 不满足」，
# 于是那条规则会**静默地永不成立**，看上去像「行情没到条件」。
# 等 P1-6 把 ML 真正建起来再加进来。
_STOCK_DELIBERATELY_ABSENT = {
    "dim.ml": "当前项目没有任何训练好的模型（P1-6 未开工），取值恒为缺失",
}

STOCK_FIELD_LABELS: dict[str, str] = {
    "composite": "综合分", "raw_composite": "原始综合分",
    "dim.technical": "技术分", "dim.fundamental": "基本面分",
    "dim.sentiment": "情绪分", "dim.position": "持仓分",
    "price.change_5d_pct": "近5日涨幅", "price.change_20d_pct": "近20日涨幅",
    "valuation.pe": "市盈率", "valuation.pe_ttm": "市盈率TTM", "valuation.pb": "市净率",
    "valuation.total_mv": "总市值", "valuation.circ_mv": "流通市值",
    "current_position_pct": "当前持仓", "suggested_position_pct": "建议仓位",
    "recommendation": "系统建议",
    "data_quality.level": "数据质量档", "data_quality.score": "数据质量分",
}

STOCK_CATEGORICAL_FIELDS = {"recommendation", "data_quality.level"}


# ──────────────────── 注册表 ────────────────────

class FieldRegistry:
    """一个市场的字段全集。"""

    __slots__ = ("resolvers", "labels", "categorical", "absent")

    def __init__(self, resolvers: dict, labels: dict, categorical: set,
                 absent: dict[str, str] | None = None):
        self.resolvers = resolvers
        self.labels = labels
        self.categorical = categorical
        # 「刻意不提供」的字段 → 理由。用来给出**说得清的错误**，
        # 而不是让人对着「未知字段」猜为什么。
        self.absent = absent or {}

    @property
    def numeric(self) -> set[str]:
        return set(self.resolvers) - self.categorical


_REGISTRY: dict[str, FieldRegistry] = {}


def register(market: str, registry: FieldRegistry) -> None:
    _REGISTRY[market] = registry


def registry_for(market: str) -> FieldRegistry:
    """取某个市场的字段表。未注册的市场直接抛 —— ⛔ 别静默回落到 crypto。"""
    reg = _REGISTRY.get(market)
    if reg is None:
        raise KeyError(
            f"市场 {market!r} 还没有字段注册表（已注册：{sorted(_REGISTRY)}）。"
            f"⛔ 不会回落到别的市场 —— 那会让一条策略拿着另一个市场的字段跑，"
            f"而所有条件都取不到值、静默永不触发。")
    return reg


def resolve(market: str, field: str, card: dict) -> Any:
    """取一个字段在这张分析卡上的实际值（缺失/未知 → None）。"""
    fn = registry_for(market).resolvers.get(field)
    return fn(card) if fn else None


def known_fields(market: str) -> set[str]:
    return set(registry_for(market).resolvers)


def explain_unknown(market: str, field: str) -> str:
    """字段不认识时给一句**说得清**的话（而不是干巴巴的「未知字段」）。"""
    reg = registry_for(market)
    if field in reg.absent:
        return f"{market} 刻意不提供 `{field}`：{reg.absent[field]}"
    other = [m for m, r in _REGISTRY.items() if field in r.resolvers and m != market]
    if other:
        return (f"`{field}` 是 {'/'.join(other)} 的字段，{market} 没有。"
                f"⚠️ 硬写进 {market} 的规则会**取不到值 → 静默永不触发**")
    return f"{market} 不认识字段 `{field}`（可用：{sorted(reg.resolvers)}）"


# 股票两市场共用同一张表（分析卡形状一致）。
# ⚠️ 港股 Jason 已拍板不做（2026-08-02），故**不注册** —— 真要跑港股会在
#    `registry_for` 那里直接抛，而不是拿 A 股的表凑合。
register(A_SHARE, FieldRegistry(STOCK_FIELD_RESOLVERS, STOCK_FIELD_LABELS,
                                STOCK_CATEGORICAL_FIELDS, _STOCK_DELIBERATELY_ABSENT))
register(US_STOCK, FieldRegistry(STOCK_FIELD_RESOLVERS, STOCK_FIELD_LABELS,
                                 STOCK_CATEGORICAL_FIELDS, _STOCK_DELIBERATELY_ABSENT))


def _register_crypto() -> None:
    """crypto 的表仍住在 `crypto_intel_engine/dsl.py`（那是它的真源，别搬）。

    这里只是**登记一份引用**，让按市场查表这条路对 crypto 同样成立。
    延迟 import 是为了避开循环依赖（dsl.py 会 import 本模块）。
    """
    from crypto_intel_engine.dsl import (
        CATEGORICAL_FIELDS,
        FIELD_LABELS,
        FIELD_RESOLVERS,
    )
    register(CRYPTO, FieldRegistry(FIELD_RESOLVERS, FIELD_LABELS, CATEGORICAL_FIELDS))

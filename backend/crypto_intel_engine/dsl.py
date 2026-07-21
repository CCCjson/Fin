"""crypto 自主策略 DSL —— MoneyBill 把 Jason 人话编译成的**确定性规则对象**。

设计原则：
- **确定性、可复核、可回测**：不是 AI 生成代码（那是将来逃生口），是结构化条件树。
- **条件原语直接吃 `analyze_crypto_symbol` 的输出**（技术/衍生品/大势/排雷都已算好），
  引擎每 tick 只做「取值 + 比较」，不重复造分析轮子。
- **手续费+滑点是一等字段**（`CostModel`）：套利类强制「最小净边际 > 往返成本」，
  否则一买一卖被 0.2% taker + 滑点吃光甚至倒亏。

字段原语（`field`）→ `analyze_crypto_symbol` 结果路径见 `FIELD_RESOLVERS`。
"""
from collections.abc import Callable
from typing import Any, Literal, get_args

from pydantic import BaseModel, Field, field_validator, model_validator

# ──────────────────── 字段原语注册表 ────────────────────
# 每个原语 = 从 analyze_crypto_symbol 结果 dict 安全取值（缺失返 None）。


def _g(d: dict | None, *path: str) -> Any:
    cur: Any = d
    for k in path:
        if not isinstance(cur, dict):
            return None
        cur = cur.get(k)
    return cur


FIELD_RESOLVERS: dict[str, Callable[[dict], Any]] = {
    # 数值型（0-100 分 / 比率 / 百分比）
    "composite": lambda r: _g(r, "composite"),
    "raw_composite": lambda r: _g(r, "raw_composite"),
    "dim.technical": lambda r: _g(r, "dimensions", "technical", "score"),
    "dim.derivatives": lambda r: _g(r, "dimensions", "derivatives", "score"),
    "dim.regime": lambda r: _g(r, "dimensions", "regime", "score"),
    "funding_rate": lambda r: _g(r, "derivatives_snapshot", "funding", "funding_rate"),
    "long_short_ratio": lambda r: _g(r, "derivatives_snapshot", "long_short", "ratio"),
    "screen.score": lambda r: _g(r, "screen", "score"),
    "fear_greed": lambda r: _fng_num(_g(r, "market_context", "fear_greed", "value")),
    "price.change_5d_pct": lambda r: _g(r, "price", "change_5d_pct"),
    "price.change_20d_pct": lambda r: _g(r, "price", "change_20d_pct"),
    "price.change_60d_pct": lambda r: _g(r, "price", "change_60d_pct"),
    "suggested_position_pct": lambda r: _g(r, "suggested_position_pct"),
    "current_position_pct": lambda r: _g(r, "current_position_pct"),
    # 类别型（字符串）
    "recommendation": lambda r: _g(r, "recommendation"),
    "screen.verdict": lambda r: _g(r, "screen", "verdict"),
    "btc_regime": lambda r: _g(r, "btc_regime", "regime"),
}

# 类别型字段只支持 eq / in（其余为数值型，支持 gt/gte/lt/lte/eq/between）
CATEGORICAL_FIELDS = {"recommendation", "screen.verdict", "btc_regime"}
NUMERIC_FIELDS = set(FIELD_RESOLVERS) - CATEGORICAL_FIELDS

# 字段 → 中文名（给待确认单/运行日志的条件展示用人话，别露原始字段名）
FIELD_LABELS: dict[str, str] = {
    "composite": "综合分", "raw_composite": "原始综合分",
    "dim.technical": "技术分", "dim.derivatives": "衍生品分", "dim.regime": "大势分",
    "funding_rate": "资金费率", "long_short_ratio": "多空比",
    "screen.score": "排雷分", "screen.verdict": "排雷结论",
    "fear_greed": "恐慌贪婪", "btc_regime": "BTC大势", "recommendation": "系统建议",
    "price.change_5d_pct": "近5日涨幅", "price.change_20d_pct": "近20日涨幅",
    "price.change_60d_pct": "近60日涨幅",
    "suggested_position_pct": "建议仓位", "current_position_pct": "当前持仓",
}

# 合法字段名枚举 —— 塞进 Condition.field 的 JSON schema，让 MoneyBill 只能从中选、不再猜错。
FieldName = Literal[
    "composite", "raw_composite",
    "dim.technical", "dim.derivatives", "dim.regime",
    "funding_rate", "long_short_ratio",
    "screen.score", "screen.verdict",
    "fear_greed", "btc_regime", "recommendation",
    "price.change_5d_pct", "price.change_20d_pct", "price.change_60d_pct",
    "suggested_position_pct", "current_position_pct",
]
# 防漂移：枚举必须与真源 FIELD_RESOLVERS 完全一致（改一处必改另一处）
assert set(get_args(FieldName)) == set(FIELD_RESOLVERS), "FieldName 与 FIELD_RESOLVERS 不一致"

_NUMERIC_OPS = {"gt", "gte", "lt", "lte", "eq", "between"}
_CATEGORICAL_OPS = {"eq", "in"}


def _fng_num(v: Any) -> float | None:
    """恐慌贪婪值可能是字符串数字（'32'）→ 归一成 float。"""
    if v is None:
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def resolve_field(field: str, analysis: dict) -> Any:
    """取一个原语在本次分析结果里的实际值（缺失/未知返 None）。"""
    fn = FIELD_RESOLVERS.get(field)
    return fn(analysis) if fn else None


# ──────────────────── 条件树 ────────────────────


class Condition(BaseModel):
    field: FieldName = Field(
        ...,
        description="条件字段（只能从枚举里选）：综合分 composite / 三维分 dim.technical|dim.derivatives|"
                    "dim.regime / 资金费率 funding_rate / 多空比 long_short_ratio / 排雷 screen.score|"
                    "screen.verdict(pass|caution|avoid|unknown) / 恐慌贪婪 fear_greed / BTC大势 "
                    "btc_regime(bull|bear|unknown) / 建议 recommendation(BUY|HOLD|SELL) / 涨幅 "
                    "price.change_5d_pct|20d|60d / 仓位 suggested_position_pct|current_position_pct")
    op: Literal["gt", "gte", "lt", "lte", "eq", "in", "between"]
    value: Any = Field(..., description="比较值：数值型给数字/区间[lo,hi]；类别型给字符串或字符串列表")

    @model_validator(mode="after")
    def _op_value_sane(self) -> "Condition":
        cat = self.field in CATEGORICAL_FIELDS
        allowed = _CATEGORICAL_OPS if cat else _NUMERIC_OPS
        if self.op not in allowed:
            kind = "类别型" if cat else "数值型"
            raise ValueError(f"字段 '{self.field}'（{kind}）不支持算子 '{self.op}'，仅 {sorted(allowed)}")
        if self.op == "between":
            if not (isinstance(self.value, (list, tuple)) and len(self.value) == 2):
                raise ValueError("between 的 value 必须是 [下界, 上界]")
        if self.op == "in" and not isinstance(self.value, (list, tuple)):
            raise ValueError("in 的 value 必须是列表")
        return self


class ConditionGroup(BaseModel):
    """all_of 全真 AND；any_of 至少一真 OR。组命中 = all_of 全过 且（any_of 空 或 至少一过）。"""
    all_of: list[Condition] = Field(default_factory=list)
    any_of: list[Condition] = Field(default_factory=list)

    @model_validator(mode="after")
    def _non_empty(self) -> "ConditionGroup":
        if not self.all_of and not self.any_of:
            raise ValueError("条件组不能为空（all_of / any_of 至少给一条）")
        return self


# ──────────────────── 顶层 DSL 子对象 ────────────────────


class Universe(BaseModel):
    symbols: list[str] = Field(..., min_length=1, description="白名单交易对（带 .BN 后缀）")
    screen_filter: ConditionGroup | None = Field(None, description="可选动态筛选（在白名单基础上再过一层）")

    @field_validator("symbols")
    @classmethod
    def _suffix(cls, v: list[str]) -> list[str]:
        bad = [s for s in v if not s.upper().endswith(".BN")]
        if bad:
            raise ValueError(f"交易对须带 .BN 后缀：{bad}")
        return [s.upper() for s in v]


class EntryRules(BaseModel):
    when: ConditionGroup
    cooldown_minutes: int = Field(60, ge=0, description="同币两次进场最小间隔，防抖/防churn")


class ExitRules(BaseModel):
    when: ConditionGroup
    use_atr_stop: bool = True       # 触及 analyze_crypto 给的 ATR 止损即卖
    use_take_profit: bool = True    # 触及止盈即卖


class PositionPolicy(BaseModel):
    target_pct_source: Literal["suggested", "fixed"] = "suggested"
    fixed_target_pct: float | None = Field(None, gt=0, le=1, description="target_pct_source=fixed 时用")
    max_position_pct: float | None = Field(None, gt=0, le=1, description="覆盖风控单仓上限（不填用全局）")
    per_symbol_exposure_cap_pct: float = Field(0.15, gt=0, le=1, description="单币敞口占总资产上限")

    @model_validator(mode="after")
    def _fixed_needs_pct(self) -> "PositionPolicy":
        if self.target_pct_source == "fixed" and not self.fixed_target_pct:
            raise ValueError("target_pct_source=fixed 时必须给 fixed_target_pct")
        return self


class CostModel(BaseModel):
    taker_fee_pct: float = Field(0.001, ge=0, description="单边 taker 费率（币安现货 0.1%）")
    slippage_pct: float = Field(0.0005, ge=0, description="单边滑点假设（v1 固定 bp）")
    min_net_edge_pct: float = Field(0.0, ge=0, description="扣完往返成本后仍要求的最小净边际")
    target_edge_source: Literal["take_profit", "fixed"] = "take_profit"
    fixed_target_edge_pct: float | None = Field(None, gt=0, description="target_edge_source=fixed 时的毛边际")

    @model_validator(mode="after")
    def _fixed_needs_edge(self) -> "CostModel":
        if self.target_edge_source == "fixed" and not self.fixed_target_edge_pct:
            raise ValueError("target_edge_source=fixed 时必须给 fixed_target_edge_pct")
        return self


class Guardrails(BaseModel):
    per_order_notional_usdt: float = Field(..., gt=0, description="单笔名义金额上限")
    max_orders_per_day: int = Field(..., gt=0, description="单日最多下单笔数")
    max_round_trips_per_day: int | None = Field(None, gt=0, description="单日最多完整买卖往返")
    max_fees_per_day_usdt: float | None = Field(None, gt=0, description="单日累计手续费上限（费用漂移护栏）")
    daily_loss_pct: float = Field(0.03, gt=0, le=1, description="当日回撤熔断阈值（对齐硬风控 3%）")
    symbol_whitelist: list[str] = Field(default_factory=list, description="额外白名单镜像（与 universe 双重卡）")
    max_confirm_slippage_pct: float = Field(
        0.02, gt=0, le=1,
        description="确认时价格较决策时漂移超此比例→自动拦下要求重新决策（防隔久了追已变行情）")


class CryptoStrategySpec(BaseModel):
    """一条完整策略规格 —— MoneyBill 编译产物，落 CryptoStrategy 表。"""
    name: str = Field(..., min_length=1)
    strategy_kind: Literal["swing", "arb", "long_hold"] = "swing"
    interval_minutes: int = Field(30, ge=1, description="tick 节奏（分钟）")
    universe: Universe
    entry_rules: EntryRules
    exit_rules: ExitRules
    position_policy: PositionPolicy = Field(default_factory=PositionPolicy)
    cost_model: CostModel = Field(default_factory=CostModel)
    guardrails: Guardrails
    capital_basis: Literal["config", "real_total_value"] = "config"
    mode: Literal["paper", "live"] = "paper"

    @model_validator(mode="after")
    def _arb_needs_edge_above_cost(self) -> "CryptoStrategySpec":
        # 套利本质靠薄差价高频吃，成本不覆盖必亏 → 强制最小净边际严格大于往返成本
        if self.strategy_kind == "arb":
            rt = round_trip_cost(self.cost_model)
            if self.cost_model.min_net_edge_pct <= rt:
                raise ValueError(
                    f"套利策略 min_net_edge_pct({self.cost_model.min_net_edge_pct}) "
                    f"必须 > 往返成本({rt:.4%})，否则手续费+滑点吃光利润")
        return self


# ──────────────────── 成本助手 ────────────────────


def round_trip_cost(cm: CostModel) -> float:
    """一买一卖的总成本率 = 2×taker 费 + 滑点（单边滑点在买卖各计一次亦近似 2×，v1 取一次保守缓冲）。"""
    return 2 * cm.taker_fee_pct + cm.slippage_pct


def gross_target_edge(cm: CostModel, entry: float | None, take_profit: float | None) -> float | None:
    """毛目标边际：take_profit 源 = (止盈/入场-1)；fixed 源 = 配置值。取不到返 None。"""
    if cm.target_edge_source == "fixed":
        return cm.fixed_target_edge_pct
    if entry and take_profit and entry > 0:
        return take_profit / entry - 1
    return None

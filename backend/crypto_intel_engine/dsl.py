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
    "dim.flow": lambda r: _g(r, "dimensions", "flow", "score"),
    "dim.sentiment": lambda r: _g(r, "dimensions", "sentiment", "score"),
    "funding_rate": lambda r: _g(r, "derivatives_snapshot", "funding", "funding_rate"),
    "funding_pctile": lambda r: _g(r, "dimensions", "derivatives", "detail", "funding_pctile"),
    "long_short_ratio": lambda r: _g(r, "derivatives_snapshot", "long_short", "ratio"),
    "top_trader_ratio": lambda r: _g(r, "derivatives_snapshot", "top_trader", "ratio"),
    "taker_ratio": lambda r: _g(r, "derivatives_snapshot", "taker_flow", "ratio"),
    "basis_rate": lambda r: _g(r, "derivatives_snapshot", "basis", "basis_rate"),
    "oi_change_pct": lambda r: _g(r, "dimensions", "derivatives", "detail", "oi_price", "oi_change_pct"),
    "spot_taker_buy_ratio": lambda r: _g(r, "dimensions", "flow", "detail", "spot_taker_buy_ratio"),
    "stablecoin_change_pct": lambda r: _g(r, "dimensions", "flow", "detail", "stablecoin_change_pct"),
    "category_change_24h": lambda r: _g(r, "dimensions", "flow", "detail", "category_change_24h"),
    "quote_volume_24h": lambda r: _g(r, "dimensions", "flow", "detail", "quote_volume_24h"),
    "news_sentiment": lambda r: _g(r, "dimensions", "sentiment", "detail", "net_sentiment"),
    "unlock_pct_30d": lambda r: _g(r, "screen", "unlock", "pct_of_supply"),
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
    "tf_4h_trend": lambda r: _g(r, "dimensions", "technical", "detail",
                                "timeframe_4h", "tf_4h_trend"),
    "tf_4h_aligned": lambda r: _bool_str(
        _g(r, "dimensions", "technical", "detail", "timeframe_4h", "aligned")),
}

# 类别型字段只支持 eq / in（其余为数值型，支持 gt/gte/lt/lte/eq/between）
CATEGORICAL_FIELDS = {"recommendation", "screen.verdict", "btc_regime",
                      "tf_4h_trend", "tf_4h_aligned"}
NUMERIC_FIELDS = set(FIELD_RESOLVERS) - CATEGORICAL_FIELDS

# 字段 → 中文名（给待确认单/运行日志的条件展示用人话，别露原始字段名）
FIELD_LABELS: dict[str, str] = {
    "composite": "综合分", "raw_composite": "原始综合分",
    "dim.technical": "技术分", "dim.derivatives": "衍生品分", "dim.regime": "大势分",
    "dim.flow": "资金流分", "dim.sentiment": "新闻情绪分",
    "funding_rate": "资金费率", "funding_pctile": "资金费率分位",
    "long_short_ratio": "散户多空比", "top_trader_ratio": "大户持仓比",
    "taker_ratio": "合约主动买卖比", "basis_rate": "期现基差",
    "oi_change_pct": "未平仓变化",
    "spot_taker_buy_ratio": "现货主动买入占比", "stablecoin_change_pct": "稳定币供应变化",
    "category_change_24h": "所属赛道24h涨跌", "quote_volume_24h": "24h成交额",
    "news_sentiment": "新闻净情绪", "unlock_pct_30d": "未来30天解锁占比",
    "screen.score": "排雷分", "screen.verdict": "排雷结论",
    "fear_greed": "恐慌贪婪", "btc_regime": "BTC大势", "recommendation": "系统建议",
    "tf_4h_trend": "4h趋势", "tf_4h_aligned": "4h与日线同向",
    "price.change_5d_pct": "近5日涨幅", "price.change_20d_pct": "近20日涨幅",
    "price.change_60d_pct": "近60日涨幅",
    "suggested_position_pct": "建议仓位", "current_position_pct": "当前持仓",
}

# 合法字段名枚举 —— 塞进 Condition.field 的 JSON schema，让 MoneyBill 只能从中选、不再猜错。
FieldName = Literal[
    "composite", "raw_composite",
    "dim.technical", "dim.derivatives", "dim.regime", "dim.flow", "dim.sentiment",
    "funding_rate", "funding_pctile", "long_short_ratio", "top_trader_ratio",
    "taker_ratio", "basis_rate", "oi_change_pct",
    "spot_taker_buy_ratio", "stablecoin_change_pct", "category_change_24h",
    "quote_volume_24h", "news_sentiment", "unlock_pct_30d",
    "screen.score", "screen.verdict",
    "fear_greed", "btc_regime", "recommendation",
    "tf_4h_trend", "tf_4h_aligned",
    "price.change_5d_pct", "price.change_20d_pct", "price.change_60d_pct",
    "suggested_position_pct", "current_position_pct",
    # ── 股票（A股/美股）独有的字段（S5 批次1）──
    # ⚠️ 它们**在这个枚举里**只是为了让 `Condition` 能构造出来；
    #    「这个字段属不属于本策略的市场」由 `StrategySpec._fields_belong_to_this_market`
    #    查表判。两层各管一件事：
    #      枚举 → 给 LLM 的 JSON schema 提示（别瞎猜字段名）
    #      市场校验 → 防止跨市场混用（否则会**静默永不触发**）
    #    ⛔ 别把两层合并成一个 —— 合并就得给每个市场各造一份 Condition 类。
    "dim.fundamental", "dim.position",
    "valuation.pe", "valuation.pe_ttm", "valuation.pb",
    "valuation.total_mv", "valuation.circ_mv",
    "data_quality.level", "data_quality.score",
]

# 防漂移：crypto 的字段必须与真源 FIELD_RESOLVERS 完全一致（改一处必改另一处）。
# ⚠️ 枚举是**所有市场的并集**，所以这里只能查「crypto 的没漏」，不能查相等。
assert set(FIELD_RESOLVERS) <= set(get_args(FieldName)), "FieldName 漏了 crypto 的字段"

_NUMERIC_OPS = {"gt", "gte", "lt", "lte", "eq", "between"}
_CATEGORICAL_OPS = {"eq", "in"}


def _bool_str(v: Any) -> str | None:
    """布尔 → 'yes'/'no' 字符串（类别型字段只吃字符串，让 MoneyBill 写 eq: 'yes' 更直观）。"""
    if v is None:
        return None
    return "yes" if v else "no"


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
        description=(
            "条件字段（只能从枚举里选）："
            "综合分 composite / 五维分 dim.technical|dim.derivatives|dim.regime|dim.flow|dim.sentiment"
            " / 衍生品 funding_rate(绝对值)|funding_pctile(历史分位0-100,越高越拥挤)|"
            "long_short_ratio(散户,反向)|top_trader_ratio(大户,顺向)|taker_ratio(合约主动买卖)|"
            "basis_rate(期现基差)|oi_change_pct(未平仓变化%)"
            " / 资金流 spot_taker_buy_ratio(现货主动买入占比,0-1,>0.5买方主动)|"
            "stablecoin_change_pct(稳定币供应30日变化%)|category_change_24h(所属赛道)|"
            "quote_volume_24h(24h成交额USDT,流动性)"
            " / 事件 news_sentiment(-1到1) / 解锁 unlock_pct_30d(未来30天解锁占流通%)"
            " / 排雷 screen.score|screen.verdict(pass|caution|avoid|unknown)"
            " / 恐慌贪婪 fear_greed / BTC大势 btc_regime(bull|bear|unknown)"
            " / 多周期 tf_4h_trend(bullish|bearish)|tf_4h_aligned(yes|no)"
            " / 建议 recommendation(BUY|HOLD|SELL) / 涨幅 price.change_5d_pct|20d|60d"
            " / 仓位 suggested_position_pct|current_position_pct"))
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
    def _normalize(cls, v: list[str]) -> list[str]:
        """只做大写归一。

        ⚠️ 后缀校验**挪到了 spec 层**（`_symbols_match_market`），因为它跟市场绑：
        crypto 要 `.BN`、A 股要 `.SH/.SZ/.BJ`、美股是裸 ticker。
        `Universe` 自己不知道它属于哪个市场，在这里写死 `.BN` 会让股票策略
        连编译都过不了（S5 批次1 踩到）。
        """
        blank = [s for s in v if not s or not s.strip()]
        if blank:
            raise ValueError("交易标的不能是空串")
        return [s.upper().strip() for s in v]


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


class SubStrategy(BaseModel):
    """组合策略的一条子策略（S8 裁决 6）。

    ⭐ 存在的意义是让「60% 趋势 + 40% 均值回归」**能被表达出来**。
    在此之前 DSL 只有一个 universe 加一套规则，权重根本无处可写。

    🔴 **v1 硬性要求：各子策略的 universe 不许重叠。**

    因为交易所侧**一个币只有一个仓位**。同一个币若被两条子策略各管一半，
    同一根 bar 上 A 说买、B 说卖时无法净额处理 ——
    「A 持 0.5 BTC、B 持 −0.3 BTC」在现货上根本不存在（引擎不支持做空）。
    要支持重叠得先有一层「仓位归属」账本，那是另一张卡的体量。

    ⚠️ 所以「同一批币、两套规则」这种经典写法 **v1 表达不了**，
    可表达的是「这几个币走趋势、那几个币走均值回归」。
    ⛔ 别为了绕过这条限制去掉校验 —— 去掉之后回测数字会变成一个说不清含义的东西。
    """
    name: str = Field(..., min_length=1, description="子策略名（同一 spec 内唯一）")
    weight: float = Field(..., gt=0, le=1, description="资金权重，同一 spec 内合计 = 1.0")
    universe: Universe = Field(..., description="这条子策略管哪些币（⛔ 不许与兄弟重叠）")
    entry_rules: EntryRules
    exit_rules: ExitRules


class RuleSet(BaseModel):
    """一套「管哪些币 + 什么时候进出 + 分多少资金」——`CryptoStrategySpec.rule_sets()` 的产物。

    ⭐ 它的作用是让**单策略和组合策略在下游长成同一个形状**：
    单策略 = 一条 weight=1.0 的 RuleSet，组合策略 = N 条。
    于是回测闸 / 逐日回放 / 实盘引擎都只需要写一套「遍历 rule_sets」的逻辑，
    不用到处 `if spec.sub_strategies:` 分叉 —— 那种分叉迟早会漏一处。
    """
    name: str
    weight: float
    universe: Universe
    entry_rules: EntryRules
    exit_rules: ExitRules


class CostModel(BaseModel):
    taker_fee_pct: float = Field(0.001, ge=0, description="单边 taker 费率（币安现货 0.1%）")
    slippage_pct: float = Field(0.0005, ge=0, description="单边滑点假设（fixed 档用；measured 取不到时的兜底）")
    slippage_source: Literal["fixed", "measured"] = Field(
        "measured",
        description="fixed=用上面的固定假设；measured=下单前扫真实盘口按本单名义额实测滑点"
                    "（拿不到盘口自动回落 fixed，并在决策里标注）")
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
    # 这条策略跑哪个市场 —— 决定用哪张**字段注册表**（S5 批次1）。
    # ⚠️ 默认 crypto 是为了**不动存量数据**（库里的行都没有这个字段）。
    # ⛔ 港股 Jason 已拍板不做，写了会在字段校验那里直接抛。
    market: Literal["crypto", "a_share", "us_stock"] = "crypto"
    universe: Universe
    # ⚠️ 单策略写法：这两个必填。**组合策略（给了 sub_strategies）时必须缺席**，
    #    互斥由 `_rules_xor_sub_strategies` 强制 —— 两边都写会让「到底按谁的规则交易」
    #    变成一个没有答案的问题，而那种歧义在真钱系统里不能靠约定俗成。
    #    ⛔ 别直接读这两个字段，走 `rule_sets()`（组合策略下它们是 None）。
    entry_rules: EntryRules | None = None
    exit_rules: ExitRules | None = None
    sub_strategies: list[SubStrategy] | None = Field(
        None, description="组合策略：子策略 + 权重（裁决 6）。给了它就不能再给顶层 entry/exit")
    position_policy: PositionPolicy = Field(default_factory=PositionPolicy)
    cost_model: CostModel = Field(default_factory=CostModel)
    guardrails: Guardrails
    capital_basis: Literal["config", "real_total_value"] = "config"
    mode: Literal["paper", "live"] = "paper"

    @model_validator(mode="after")
    def _symbols_match_market(self) -> "CryptoStrategySpec":
        """标的必须属于本策略声明的市场。

        ⚠️ 这条校验从 `Universe` 挪上来的：后缀规则跟市场绑（crypto `.BN` /
        A 股 `.SH/.SZ/.BJ` / 美股裸 ticker），而 `Universe` 自己不知道市场。

        🔴 判据走 `infer_market_from_symbol`（全项目唯一的后缀推断实现），
        ⛔ 别在这里再手写一遍后缀表 —— 那种复制迟早跟真源漂移。
        （已知坑：裸 `BTC` 会被它判成美股，所以 crypto 必须带 `.BN`。）
        """
        from common.market import infer_market_from_symbol

        all_syms = list(self.universe.symbols)
        for sub in (self.sub_strategies or []):
            all_syms += list(sub.universe.symbols)

        bad = {s: infer_market_from_symbol(s) for s in all_syms
               if infer_market_from_symbol(s) != self.market}
        if bad:
            detail = "、".join(f"{s}(看起来是 {m})" for s, m in sorted(bad.items()))
            raise ValueError(
                f"这些标的不属于 {self.market}：{detail}。"
                f"⚠️ 市场写错的话，字段表也会取错 —— 所有条件都会取不到值而**静默永不触发**。")
        return self

    @model_validator(mode="after")
    def _fields_belong_to_this_market(self) -> "CryptoStrategySpec":
        """🔴 **跨市场字段必须在编译期炸掉，不能留到运行期。**

        DSL 的语义是「取不到值 = 该条不满足」。所以一条 a_share 策略里写了
        `funding_rate`（crypto 的字段）时，它不会报错 —— 它会**静默地永不触发**，
        看上去像「行情一直没到条件」。这种失败模式在真钱系统里最难查：
        策略一单不下，而每一层看上去都正常。

        所以在这里查表挡住，并给出**说得清的理由**（这个字段是哪个市场的 /
        为什么刻意不提供），而不是干巴巴一句「未知字段」。
        """
        from common.strategy_fields import explain_unknown, known_fields

        try:
            allowed = known_fields(self.market)
        except KeyError as e:      # 没注册的市场（比如港股）
            raise ValueError(str(e)) from e

        bad: list[str] = []
        for rs in self._raw_rule_groups():
            for c in list(rs.all_of) + list(rs.any_of):
                if c.field not in allowed:
                    bad.append(c.field)
        if bad:
            uniq = sorted(set(bad))
            raise ValueError(
                "；".join(explain_unknown(self.market, f) for f in uniq))
        return self

    def _raw_rule_groups(self) -> list["ConditionGroup"]:
        """本 spec 里所有条件组（顶层的或子策略的）—— 只给校验用。

        ⚠️ 不能走 `rule_sets()`：那个方法断言「顶层规则非空」，
        而字段校验跑在 `_rules_xor_sub_strategies` **之前**（pydantic 按定义顺序），
        此刻 spec 可能还处在非法形状。
        """
        groups: list[ConditionGroup] = []
        for rules in (self.entry_rules, self.exit_rules):
            if rules is not None:
                groups.append(rules.when)
        for sub in (self.sub_strategies or []):
            groups.append(sub.entry_rules.when)
            groups.append(sub.exit_rules.when)
        return groups

    @model_validator(mode="after")
    def _rules_xor_sub_strategies(self) -> "CryptoStrategySpec":
        """单策略与组合策略**二选一**，且组合的 universe 不许重叠。

        ⛔ 两边都写 = 「到底按谁的规则交易」没有答案。真钱系统里这种歧义
        不能靠约定俗成，必须在编译期就炸掉。
        """
        has_top = self.entry_rules is not None or self.exit_rules is not None
        subs = self.sub_strategies

        if not subs:
            if self.entry_rules is None or self.exit_rules is None:
                raise ValueError("单策略必须同时给 entry_rules 和 exit_rules"
                                 "（要写组合策略请给 sub_strategies）")
            return self

        if has_top:
            raise ValueError("给了 sub_strategies 就不能再给顶层 entry_rules/exit_rules —— "
                             "两套规则并存的话，「按谁的规则交易」没有答案")
        if len(subs) < 2:
            raise ValueError("组合策略至少要两条子策略；只有一条的话直接写成单策略即可"
                             "（同一件事有两种写法，迟早会读错）")

        names = [s.name for s in subs]
        if len(set(names)) != len(names):
            raise ValueError(f"子策略名重复：{sorted({n for n in names if names.count(n) > 1})}")

        total = sum(s.weight for s in subs)
        if abs(total - 1.0) > 1e-6:
            raise ValueError(f"子策略权重合计必须等于 1.0，当前是 {total:.4f}"
                             f"（{'、'.join(f'{s.name}={s.weight}' for s in subs)}）")

        # 🔴 universe 不许重叠 —— 理由见 SubStrategy 的 docstring（一个币只有一个仓位，
        #    同根 bar 上 A 买 B 卖无法净额，而现货不能做空）
        seen: dict[str, str] = {}
        for sub in subs:
            for sym in sub.universe.symbols:
                if sym in seen:
                    raise ValueError(
                        f"{sym} 同时被子策略「{seen[sym]}」和「{sub.name}」管着。"
                        f"v1 不支持重叠 universe：交易所侧一个币只有一个仓位，"
                        f"同一根 bar 上一个说买一个说卖时无法净额处理"
                        f"（现货不能做空）。请把币分开，或合并成一条子策略。")
                seen[sym] = sub.name

        # 子策略的币必须都在顶层 universe 里 —— 顶层是「这条策略碰哪些币」的总闸
        outside = sorted(set(seen) - set(self.universe.symbols))
        if outside:
            raise ValueError(f"这些币不在顶层 universe 里：{outside}。"
                             f"顶层 universe 是总闸，子策略只能在它的范围内切分。")
        return self

    def rule_sets(self) -> list["RuleSet"]:
        """归一化访问器：**单策略与组合策略在这里长成同一个形状**。

        ⭐ 所有消费方（回测闸 / 逐日回放 / 实盘引擎 / agent 工具）都走这里，
        ⛔ 别再直接读 `spec.entry_rules` —— 组合策略下它是 None，
        直接读会得到一个 `AttributeError` 或者更糟：静默把组合策略当成没有规则。

        单策略 → 一条 `RuleSet`（weight=1.0，universe = 顶层）。
        """
        if self.sub_strategies:
            return [RuleSet(name=s.name, weight=s.weight, universe=s.universe,
                            entry_rules=s.entry_rules, exit_rules=s.exit_rules)
                    for s in self.sub_strategies]
        assert self.entry_rules is not None and self.exit_rules is not None  # validator 保证
        return [RuleSet(name=self.name, weight=1.0, universe=self.universe,
                        entry_rules=self.entry_rules, exit_rules=self.exit_rules)]

    def owner_of(self, symbol: str) -> "RuleSet | None":
        """这个币归哪条规则集管（universe 不重叠，所以最多一条）。"""
        for rs in self.rule_sets():
            if symbol in rs.universe.symbols:
                return rs
        return None

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


# ──────────────────── 登记进按市场查表的注册表（S5）────────────────────
#
# ⚠️ crypto 的字段表**真源仍是本文件**，别搬去 `common/strategy_fields.py`。
# 那边只是登记一份引用，让「按市场取字段」这条路对 crypto 同样成立 ——
# 股票（A股/美股）的表住在那边，两边形状同构但字段不同。
# ⛔ 别把两张表合并：合并之后一条 crypto 策略能写出 `valuation.pe < 20`，
#    而 DSL 的语义是「取不到 = 不满足」→ 那条规则**静默地永不触发**。
from common.strategy_fields import _register_crypto as _reg_crypto  # noqa: E402

_reg_crypto()

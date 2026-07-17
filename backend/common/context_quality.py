"""数据质量状态机 —— 「这个数到底可不可信」。

> MoneyBill 跟你说「腾讯买入」。你不知道的是：这次港股实时行情抓失败了，用的是
> 三天前的收盘价。它照样敢说，因为缺失字段传给它就是个 `None`，**它看不出区别**。

**别和 P0-1 混淆**：`outcome_eval` 测的是「AI 的嘴对不对」（事后）；本模块测的是
「喂给 AI 的数据可不可信」（事前）。P0 闭环的两道防线，一前一后。

设计同 `common/outcome_eval.py`：**纯逻辑、DB 无关、无时间副作用**。所有判定入参
显式传入（`bars_behind` 而不是自己查库、自己看表），所以整个模块可以零 fixture、
零 mock、零 DB 地测。放 `common/` 是分层铁律 `engines → acquisition → common/net`
——状态机跨三层（acquisition 产状态、engines 传递、agents 消费），只有最底层能被
它们同时 import 而不产生反向依赖（2026-07-17 Jason 拍板，B-1）。

---

## 与外部蓝本 `daily_stock_analysis` 的四处**刻意分歧**

蓝本（`src/services/analysis_context_builder.py`）是本模块的结构参考，但下面四条
**别照抄**，每条都有实据：

1. **`not_supported` 不参与扣分**（本模块最重要的一条）。蓝本给它 70 分
   （`available` 是 100）。后果它自己没发现：它的港美日韩台股 `capital_flow` 恒为
   `not_supported` → **非 A 股的 buy 被系统性降级**。我们改成**权重剔除后重归一化**
   ——「这个市场压根没这个数据」是**正常**，不是故障，凭什么扣分。
   真实风险不是理论上的：本项目 `FinancialData` 只有 a_share 4414 只，**港美股各 0 只**
   （2026-07-17 查库）。天真地扣分 = 港美股全线拿不到高置信度，而 Jason 正在接通港美股。

2. **block 状态要算出来，不是硬编码**。蓝本每个 `_build_*_block()` 各自显式写死
   block.status，与 items 并列而非聚合——代价是 block 和 items 会打架，且没有任何
   东西保证它们一致。我们从 fields 聚合（`aggregate_block`）。

3. **权重照结构不照数值**。蓝本的 25/25/25/10/10/5 是**拍的**，无任何回测依据
   （doc14 §10.8 批评的正是这点）。我们的数值同样是拍的 —— 所以下面标了 ⚠️，
   别把它当权威，将来该用 C++ 回测去验。

4. **否定检测整段不抄**（在硬传导层，不在本模块）。蓝本的否定词表末位有个裸「不」，
   `"不得不立即买入"` 的 prefix `"不得不"` endswith `"不"` → 被判成否定 → **护栏漏放**。
   且 grep 确认蓝本 `tests/` 里**一个测试都没覆盖那段**。我们的 `action` 是结构化的，
   压根不需要从中文里猜方向。

## 该抄的（都已核实）

- 八态语义与「业务结果不映射质量态」的边界。
- `fetch_failed` 的**克制用法**：只在「确实尝试过抓取且失败」时用；空新闻、未启用
  的能力仍是 `missing`/`not_supported`，避免把「没开这个功能」误报成「抓取故障」。
- **只算固定块，不随辅助块缺失重归一化** → 分数跨版本可比。
- **`limitations` 分核心/辅助两档**：辅助块单纯 `missing` **不进** limitations ——
  「避免把新闻缺失解释成利好/利空」。这条是真洞察。
- **稳定标识符**（`adjustments`）作返回值：文案会变、会本地化，标识符不会。
"""
from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from enum import Enum

# 判定口径的版本戳。**改状态分 / 块权重 / stale 阈值 / 分档规则 = 必须 bump。**
# 同 `outcome_eval.ENGINE_VERSION`：不 bump 就是让新旧口径的结论混在一张表里，
# 而且你永远不知道。
QUALITY_VERSION = "context-quality-v1"


class FieldStatus(str, Enum):
    """字段/数据块的**质量**状态。

    ⚠️ **这八个态描述「数据可不可信」，不描述「业务成功没成功」。** 别把
    `triggered`/`skipped`（告警结果）、`sent`/`no_channel`（通知结果）、
    `completed`/`error`（任务状态）映射进来 —— 业务结果污染质量枚举，会让
    「这条建议没触发」和「这个数据没取到」变成同一件事。
    """

    AVAILABLE = "available"          # 有值，来源和时间都说得清
    STALE = "stale"                  # 有值，但不够新（落后市场参考交易日）
    FALLBACK = "fallback"            # 首选源不可用，走了备用源
    ESTIMATED = "estimated"          # 估算值，不是实测事实（如盘中用实时价补当日 bar）
    PARTIAL = "partial"              # 块内部分可用部分缺失
    MISSING = "missing"              # 该有却没取到（库里没有 / 源没返回这个字段）
    FETCH_FAILED = "fetch_failed"    # **确实尝试过抓取且本次失败** —— 见下方克制用法
    NOT_SUPPORTED = "not_supported"  # 这个市场/源压根没有这个数据 —— **正常，不是故障**


# `fetch_failed` 的克制用法（照抄蓝本，这条它做对了）：
#   只有在「已经发起过抓取、并且拿到了明确的失败信号」时才用。
#   - 新闻搜了但一条没有        → MISSING（搜到空 ≠ 抓取失败）
#   - 这个市场没接财务数据      → NOT_SUPPORTED（没启用的能力 ≠ 故障）
#   - 代理耗尽，一个请求都没发  → FETCH_FAILED（确实试了，确实失败了）
# 把「未启用的能力」误报成 `fetch_failed`，会让人去修一个根本不存在的故障。

# 各状态的可信度分。⚠️ 这组数值是**拍的**（同蓝本），没有回测依据。
#
# 🔒 **`NOT_SUPPORTED` 刻意不在此表** —— 它不参与扣分，走权重剔除。给它任何分数
# （蓝本给 70）都会让「这个市场天生没这数据」变成一种扣分项，港美股因此系统性
# 低人一等。有门禁 `test_not_supported_does_not_penalize` 盯着，别加进来。
_STATUS_SCORES: dict[FieldStatus, int] = {
    FieldStatus.AVAILABLE: 100,
    FieldStatus.PARTIAL: 75,
    FieldStatus.ESTIMATED: 75,
    FieldStatus.FALLBACK: 65,
    FieldStatus.STALE: 50,
    FieldStatus.MISSING: 35,
    FieldStatus.FETCH_FAILED: 25,
}

# 核心块 —— 这三块降级时**不许说高置信度**（硬传导的触发条件）。
# 行情/K线/技术面是「怎么买怎么卖」的地基，它们不可信时结论就是不可信。
CORE_BLOCKS = ("quote", "daily_bars", "technical")
AUX_BLOCKS = ("fundamentals", "news")

# 块权重。⚠️ **拍的**，同蓝本，没验证过。核心块重、辅助块轻。
# 本项目不做筹码分布（蓝本的 chip 块），所以只有五块。
_BLOCK_WEIGHTS: dict[str, int] = {
    "quote": 25,
    "daily_bars": 25,
    "technical": 25,
    "fundamentals": 15,
    "news": 10,
}

# 核心块：这些态一律写进 limitations（除 available 外全算降级）。
_CORE_LIMITATION_STATUSES = frozenset({
    FieldStatus.STALE, FieldStatus.FALLBACK, FieldStatus.MISSING,
    FieldStatus.FETCH_FAILED, FieldStatus.PARTIAL, FieldStatus.ESTIMATED,
})

# 辅助块：**只有这三个态**进 limitations，单纯 `missing` 不进。
#
# 抄蓝本的洞察：「今天没有腾讯的新闻」写进限制说明，会被 LLM 读成一种信号
# （利好？利空？），而它其实什么都不是。`fetch_failed`/`fallback`/`stale` 不同 ——
# 那是「本来有、这次没拿到」，值得说。
_AUX_LIMITATION_STATUSES = frozenset({
    FieldStatus.FETCH_FAILED, FieldStatus.FALLBACK, FieldStatus.STALE,
})

# 核心块「算降级」的态 = 除 available / not_supported 之外的全部。
# `not_supported` 不在此列：它是正常态，不该触发硬传导（同上方分歧 1 的理由）。
_CORE_DEGRADED_STATUSES = frozenset({
    FieldStatus.STALE, FieldStatus.FALLBACK, FieldStatus.MISSING,
    FieldStatus.FETCH_FAILED, FieldStatus.PARTIAL, FieldStatus.ESTIMATED,
})

# 质量分 → 档位。⚠️ 阈值同样是拍的（照蓝本）。
_LEVEL_THRESHOLDS = ((85, "good"), (70, "usable"), (55, "limited"))

# 落后几个交易日算 stale。与 `market_freshness.STALE_AFTER_WEEKDAYS` 是两回事：
# 那个管「整个市场断更没有」，这个管「这一只票的 bar 跟不跟得上市场」。
# 取 1 是因为对单只票而言，市场都更到今天了它还停在昨天，就是掉队（停牌/退市/漏抓）。
STALE_BARS_BEHIND = 1


@dataclass(frozen=True)
class QualityField:
    """一个字段的质量元数据。

    **刻意不存 value** —— 值走原来的数据通路，这里只描述「这个值可不可信」。
    存两份值就有两份真相，早晚对不上。
    """
    status: FieldStatus
    source: str | None = None
    as_of: str | None = None            # 数据自身的时点（不是抓取时刻）
    fallback_from: str | None = None    # status=FALLBACK 时：本来想用哪个源
    missing_reason: str | None = None   # status ∈ {MISSING, FETCH_FAILED} 时：为什么


@dataclass(frozen=True)
class QualityBlock:
    """一组相关字段的质量。`status` 由 fields **算出**，不许外部硬塞。"""
    status: FieldStatus
    fields: Mapping[str, QualityField] = field(default_factory=dict)
    source: str | None = None
    as_of: str | None = None


def aggregate_block(fields: Mapping[str, QualityField]) -> FieldStatus:
    """从字段状态聚合出块状态。

    规则（顺序即优先级，短路，不可换）：
      1. 空块 / 全是 not_supported  → NOT_SUPPORTED（这个市场就是没这块数据）
      2. 全部 available             → AVAILABLE
      3. 有任何一个「硬失败」态      → 取最差的那个（fetch_failed > missing > stale > ...）
      4. 其余混合                   → PARTIAL

    为什么算而不是让调用方填（分歧 2）：蓝本让每个 builder 自己写死 block.status，
    于是 block 说 available、items 里躺着三个 fetch_failed 这种自相矛盾**在结构上
    是可能的**。算出来就不可能。
    """
    if not fields:
        return FieldStatus.NOT_SUPPORTED

    statuses = [f.status for f in fields.values()]
    real = [s for s in statuses if s is not FieldStatus.NOT_SUPPORTED]
    if not real:
        return FieldStatus.NOT_SUPPORTED
    if all(s is FieldStatus.AVAILABLE for s in real):
        return FieldStatus.AVAILABLE

    # 最差的那个说了算 —— 分越低越差，一个 fetch_failed 不该被一堆 available 稀释
    worst = min(real, key=lambda s: _STATUS_SCORES.get(s, 0))
    if worst is FieldStatus.AVAILABLE:
        return FieldStatus.AVAILABLE
    # 部分可用部分不可用，且最差的那个不算「硬失败」→ PARTIAL 更贴切
    if worst in (FieldStatus.PARTIAL, FieldStatus.ESTIMATED) and \
            any(s is FieldStatus.AVAILABLE for s in real):
        return FieldStatus.PARTIAL
    return worst


def status_for_bars_behind(bars_behind: int | None) -> FieldStatus:
    """「落后几个交易日」→ 质量态。`None` = 无从判断 → MISSING（不是 available）。"""
    if bars_behind is None:
        return FieldStatus.MISSING
    return FieldStatus.STALE if bars_behind >= STALE_BARS_BEHIND else FieldStatus.AVAILABLE


@dataclass(frozen=True)
class DataQuality:
    """一次分析的数据质量总览。"""
    overall_score: int
    level: str                                   # good | usable | limited | poor
    block_scores: Mapping[str, int]
    limitations: tuple[str, ...]
    core_degraded: bool
    version: str = QUALITY_VERSION


def _level(score: int) -> str:
    for threshold, name in _LEVEL_THRESHOLDS:
        if score >= threshold:
            return name
    return "poor"


def _block_status(blocks: Mapping[str, QualityBlock], key: str) -> FieldStatus:
    """块不存在 = MISSING（该有却没有），不是 NOT_SUPPORTED。

    这两者的区别是本模块的立身之本：「没取到」要修，「这市场没有」不用修。
    调用方要表达 not_supported 必须**显式**给一个 NOT_SUPPORTED 的块 —— 不填就
    默认成「这市场没有」的话，任何一次漏填都会变成静默豁免。
    """
    block = blocks.get(key)
    return block.status if block else FieldStatus.MISSING


def is_core_degraded(blocks: Mapping[str, QualityBlock],
                     scope: Iterable[str] | None = None) -> bool:
    """核心块里有没有降级的 —— 硬传导的触发条件。

    `scope` = 本次**该看**哪些块（见 `compute_quality`）；不传则看全部五块。

    写成**函数**而不是 `DataQuality` 上的一个独立字段：可降级性是块状态的
    **函数**，不是独立事实。独立字段会允许「core_degraded=False 但 quote=stale」
    这种不可能状态存在（同 `outcome_eval.is_retryable` 的道理）。
    """
    keys = set(scope) if scope is not None else set(_BLOCK_WEIGHTS)
    return any(_block_status(blocks, k) in _CORE_DEGRADED_STATUSES
               for k in CORE_BLOCKS if k in keys)


def _limitations(blocks: Mapping[str, QualityBlock],
                 scope: set[str]) -> tuple[str, ...]:
    """限制说明，格式 `"block: status"`，最多 5 条。"""
    out: list[str] = []
    for key in CORE_BLOCKS:
        if key not in scope:
            continue
        status = _block_status(blocks, key)
        if status in _CORE_LIMITATION_STATUSES:
            out.append(f"{key}: {status.value}")
    for key in AUX_BLOCKS:
        if key not in scope:
            continue
        status = _block_status(blocks, key)
        if status in _AUX_LIMITATION_STATUSES:   # 单纯 missing 不进，见常量注释
            out.append(f"{key}: {status.value}")
    return tuple(out[:5])


def compute_quality(blocks: Mapping[str, QualityBlock],
                    scope: Iterable[str] | None = None) -> DataQuality:
    """块状态 → 质量分 + 档位 + 限制说明。

    Args:
        blocks: 块名 → 块状态。
        scope: 本次**该看**哪些块。不传 = 全部五块（整体分析的场景，如 cockpit）。

    ## `scope` 与「块没填」的区别（别混，这是本模块最容易用错的地方）

      - **不在 `scope` 里** = 「这次压根不看它」。`get_daily_data` 只查日线，
        它的产出不该因为「没有实时行情块」而扣分 —— 那不是缺陷，是这个工具就
        不管实时行情。
      - **在 `scope` 里但 `blocks` 没这个键** = 「该有却没有」→ MISSING → 扣分。

    差别是刻意的：若从 `blocks` 的键自动推断 scope，任何一次**漏填**都会变成
    静默豁免 —— 忘了传 quote 反而不扣分，状态机就废了。所以 scope 必须显式。

    **`not_supported` 的块：权重剔除后重归一化，不打低分**（分歧 1）。
    效果：一个只有行情没有财务的港股，和一个财务齐全的 A 股，在「各自能拿到的
    数据都健康」时拿到**同样的分**。它不该因为「港股没有基本面数据接入」而低人
    一等 —— 那不是数据质量问题，是我们还没接（P1-2 的活）。

    **scope 内只算固定块，不因辅助块缺席就重归一化**（抄蓝本）：同一个 scope 下
    新增块不会自动改变历史分数，`GROUP BY version` 才有意义。
    """
    keys = set(scope) if scope is not None else set(_BLOCK_WEIGHTS)
    block_scores: dict[str, int] = {}
    weighted_sum = 0
    total_weight = 0

    for key, weight in _BLOCK_WEIGHTS.items():
        if key not in keys:
            continue                      # ← 本次不看它，既不计分也不计分母
        status = _block_status(blocks, key)
        if status is FieldStatus.NOT_SUPPORTED:
            continue                      # ← 权重剔除，不计分也不计分母
        score = _STATUS_SCORES.get(status, _STATUS_SCORES[FieldStatus.MISSING])
        block_scores[key] = score
        weighted_sum += score * weight
        total_weight += weight

    # 全部 not_supported = 什么都没有可评的。给 0 分而不是满分 ——
    # 「无从评价」绝不能因为「没有任何降级证据」就被当成完美。
    overall = int(round(weighted_sum / total_weight)) if total_weight else 0

    return DataQuality(
        overall_score=overall,
        level=_level(overall) if total_weight else "poor",
        block_scores=block_scores,
        limitations=_limitations(blocks, keys),
        core_degraded=is_core_degraded(blocks, keys),
    )


def clamp_confidence(
    raw_confidence: float | None,
    quality: DataQuality,
    *,
    cap: float = 60.0,
) -> tuple[float | None, tuple[str, ...]]:
    """核心数据降级时把置信度**强行打下来** —— 这是 P0-2 的硬约束层。

    不是求 LLM 诚实、不是 prompt 里写句「请谨慎」，是代码说了算。

    Args:
        raw_confidence: 原始置信度，**0-100 量纲**（与 `DecisionLog.confidence` 一致；
            那一列有三道防线盯着量纲，别在这里引入 0-1）。
        quality: `compute_quality` 的产出。
        cap: 核心块降级时的置信度上限。默认 60 —— 落在「HOLD」区间
            （`cockpit_engine.scorer._recommendation` 的 BUY 线是 65），即：
            **数据不可信时，不许给出买入级别的把握**。

    Returns:
        `(clamped_confidence, adjustments)` —— `adjustments` 是**稳定标识符**元组
        （不是给人看的文案）。文案会改、会本地化，标识符不会，测试断言打在它上面。
        没有任何调整时是空元组。

    `raw_confidence` 由调用方一并留痕（P0-3 校准要审计「打压前是多少」）。
    """
    adjustments: list[str] = []
    if raw_confidence is None:
        return None, ()

    confidence = raw_confidence
    if quality.core_degraded and confidence > cap:
        confidence = cap
        adjustments.append("confidence_capped_core_data_degraded")

    return confidence, tuple(adjustments)


def limitation_text(quality: DataQuality) -> str:
    """限制说明 → 一句中文，注给 LLM / 展示给 Jason。空 limitations 返回空串。"""
    if not quality.limitations:
        return ""
    return "；".join(quality.limitations)


def block_from_fields(fields: Mapping[str, QualityField],
                      source: str | None = None,
                      as_of: str | None = None) -> QualityBlock:
    """建块的正道 —— 状态从 fields 聚合，不许外部指定。"""
    return QualityBlock(status=aggregate_block(fields), fields=fields,
                        source=source, as_of=as_of)


def not_supported_block(reason: str | None = None) -> QualityBlock:
    """「这个市场压根没这块数据」—— 显式声明，不扣分。

    用在真正**定义上不适用**的地方（北向资金之于美股、涨跌停之于美股）。
    ⛔ **别拿它当「我们还没接这个数据」的挡箭牌** —— 那是 MISSING，是待办不是天命。
    """
    return QualityBlock(
        status=FieldStatus.NOT_SUPPORTED,
        fields={"_": QualityField(status=FieldStatus.NOT_SUPPORTED,
                                  missing_reason=reason)},
    )


def worst_status(statuses: Iterable[FieldStatus]) -> FieldStatus:
    """一组状态里最差的那个（not_supported 不参与比较）。"""
    real = [s for s in statuses if s is not FieldStatus.NOT_SUPPORTED]
    if not real:
        return FieldStatus.NOT_SUPPORTED
    return min(real, key=lambda s: _STATUS_SCORES.get(s, 0))

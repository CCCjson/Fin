"""硬传导 —— 数据不可信时，代码强行把结论打下来。纯规则测试，零 DB 零 LLM。

这是 P0-2 的立身之本：**不是 prompt 求 LLM 谨慎，是算完之后 Python 说了算**。
cockpit 这条路径压根没有 LLM，所以这里连「求它听话」的余地都没有 —— 直接改数。

带 🔒 的是防复发门禁。
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

from cockpit_engine.scorer import (  # noqa: E402
    CLAMP_CAP,
    WEIGHTS,
    _recommendation,
    score_cockpit,
)
from common.context_quality import (  # noqa: E402
    FieldStatus,
    QualityField,
    block_from_fields,
    compute_quality,
)

# 五维全高 → 未钳时 composite = 85（稳稳的 BUY）
_BULLISH = {"technical": 85.0, "ml": 85.0, "fundamental": 85.0,
            "sentiment": 85.0, "position": 85.0}


def _quality(status: FieldStatus):
    return compute_quality({"daily_bars": block_from_fields({"bars": QualityField(status=status)})},
                           scope=["daily_bars"])


_HEALTHY = _quality(FieldStatus.AVAILABLE)
_STALE = _quality(FieldStatus.STALE)


# ════════════════ 🔒 硬钳 ════════════════

def test_stale_data_caps_composite():
    """🔒 核心数据陈旧 → composite 被强行打到 CLAMP_CAP。"""
    out = score_cockpit(_BULLISH, quality=_STALE)
    assert out["composite"] == CLAMP_CAP
    assert out["raw_composite"] == 85.0            # 原始分留痕，没丢
    assert "composite_capped_core_data_degraded" in out["adjustments"]


def test_clamp_also_downgrades_recommendation():
    """🔒 钳了分就必须连带改建议 —— 钳完照旧给 BUY，这条硬约束就是摆设。"""
    healthy = score_cockpit(_BULLISH, quality=_HEALTHY)
    stale = score_cockpit(_BULLISH, quality=_STALE)

    assert healthy["recommendation"] == "BUY"
    assert stale["recommendation"] != "BUY"        # 数据不可信 → 不给买入


def test_clamp_also_shrinks_position_advice():
    """🔒 钳了分还建议满仓 = 白钳。目标仓位必须跟着降。"""
    healthy = score_cockpit(_BULLISH, quality=_HEALTHY, max_position_pct=0.5)
    stale = score_cockpit(_BULLISH, quality=_STALE, max_position_pct=0.5)

    assert stale["suggested_position_pct"] < healthy["suggested_position_pct"]


def test_clamp_cap_sits_below_buy_line():
    """🔒 CLAMP_CAP 必须落在 BUY 线之下，否则「数据不可信」还能给买入。"""
    assert _recommendation(CLAMP_CAP) != "BUY"


def test_healthy_data_is_not_clamped():
    out = score_cockpit(_BULLISH, quality=_HEALTHY)
    assert out["composite"] == 85.0
    assert out["adjustments"] == ()


def test_no_quality_means_no_clamp():
    """不传 quality 的老调用方行为完全不变。"""
    out = score_cockpit(_BULLISH)
    assert out["composite"] == 85.0
    assert out["adjustments"] == ()


def test_clamp_never_raises_a_low_score():
    """🔒 打压是单向的 —— 数据烂不该把一个本来就低的分**抬上去**。"""
    bearish = {k: 20.0 for k in WEIGHTS}
    out = score_cockpit(bearish, quality=_STALE)
    assert out["composite"] == 20.0
    assert out["adjustments"] == ()


def test_fetch_failed_also_triggers_clamp():
    """不止 stale —— fetch_failed / missing 同样是核心块降级。"""
    for status in (FieldStatus.FETCH_FAILED, FieldStatus.MISSING,
                   FieldStatus.FALLBACK, FieldStatus.ESTIMATED):
        out = score_cockpit(_BULLISH, quality=_quality(status))
        assert out["composite"] == CLAMP_CAP, f"{status.value} 应触发硬钳"


def test_not_supported_does_not_trigger_clamp():
    """🔒 「这个市场压根没这数据」不是降级，不许触发硬钳。

    港美股正在接入的当口，把 not_supported 当降级 = 它们全线拿不到买入建议。
    """
    from common.context_quality import not_supported_block
    q = compute_quality({"daily_bars": not_supported_block("这市场没有")},
                        scope=["daily_bars"])
    out = score_cockpit(_BULLISH, quality=q)
    assert out["composite"] == 85.0
    assert out["adjustments"] == ()


# ════════════════ dimension_coverage ════════════════

def test_dimension_coverage_exposes_light_mode():
    """🔒 light 档系统性丢掉 40% 权重（ml+sentiment），此前从不吭声。

    一维算出的 65 与五维算出的 65 在下游长得一模一样 —— dimension_coverage
    就是那个此前不存在的区分。批量选股 recommend_stocks 走的正是 light 档。
    """
    full = score_cockpit(_BULLISH)
    light = score_cockpit({**_BULLISH, "ml": None, "sentiment": None})

    assert full["dimension_coverage"] == 1.0
    assert light["dimension_coverage"] == 0.6      # 丢了 ml(.25)+sentiment(.15)
    assert full["composite"] == light["composite"]  # 分一样 —— 正是问题所在


def test_single_dimension_coverage_is_honest():
    only_tech = score_cockpit({"technical": 65.0, "ml": None, "fundamental": None,
                               "sentiment": None, "position": None})
    assert only_tech["dimension_coverage"] == 0.3
    assert only_tech["composite"] == 65.0          # 一维顶满 —— 靠 coverage 才看得出来


def test_no_dimensions_reports_zero_coverage():
    out = score_cockpit({k: None for k in WEIGHTS})
    assert out["composite"] is None
    assert out["recommendation"] == "N/A"
    assert out["dimension_coverage"] == 0.0


# ════════════════ 留痕契约 ════════════════

def test_adjustments_are_stable_identifiers():
    """🔒 断言打在稳定标识符上（文案会改，标识符不会）。"""
    out = score_cockpit(_BULLISH, quality=_STALE)
    for a in out["adjustments"]:
        assert a.islower() and " " not in a


def test_raw_composite_survives_for_calibration():
    """🔒 P0-3 校准要审计「打压前是多少」，钳前的分不许丢。

    且钳的口径以后会改（CLAMP_CAP / core_degraded 的定义）—— 只留钳后的分等于
    把原始信息永久丢掉，将来重算都没得算。
    """
    out = score_cockpit(_BULLISH, quality=_STALE)
    assert out["raw_composite"] == 85.0
    assert out["composite"] != out["raw_composite"]

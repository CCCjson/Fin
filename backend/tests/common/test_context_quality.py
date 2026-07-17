"""数据质量状态机 —— 纯规则测试，零 fixture 零 DB 零 mock 零 LLM。

本文件里带 🔒 的是**防复发门禁**，不是普通用例。它们各自钉着一个「照抄外部蓝本
就会踩进去」的坑，红了先读注释再动手。
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

from common.context_quality import (  # noqa: E402
    _STATUS_SCORES,
    CORE_BLOCKS,
    DataQuality,
    FieldStatus,
    QualityField,
    aggregate_block,
    block_from_fields,
    clamp_confidence,
    compute_quality,
    is_core_degraded,
    limitation_text,
    not_supported_block,
    status_for_bars_behind,
    worst_status,
)

_OK = QualityField(status=FieldStatus.AVAILABLE, source="tencent",
                   as_of="2026-07-17T15:34:59")


def _blocks(**statuses) -> dict:
    """按块名给状态，建一份 blocks。"""
    out = {}
    for key, status in statuses.items():
        if status is FieldStatus.NOT_SUPPORTED:
            out[key] = not_supported_block("这个市场没有")
        else:
            out[key] = block_from_fields({"f": QualityField(status=status)})
    return out


def _all_healthy() -> dict:
    return _blocks(quote=FieldStatus.AVAILABLE, daily_bars=FieldStatus.AVAILABLE,
                   technical=FieldStatus.AVAILABLE, fundamentals=FieldStatus.AVAILABLE,
                   news=FieldStatus.AVAILABLE)


# ════════════════ 🔒 门禁：not_supported 不许扣分 ════════════════

def test_not_supported_is_not_in_status_scores():
    """🔒 `NOT_SUPPORTED` 不许出现在状态分表里。

    蓝本给它 70 分（available=100）。后果它自己没发现：港美日韩台股的
    capital_flow 恒为 not_supported → **非 A 股 buy 被系统性降级**。
    给它任何分数，都是在说「这个市场天生没这数据」是种缺陷。
    """
    assert FieldStatus.NOT_SUPPORTED not in _STATUS_SCORES


def test_not_supported_does_not_penalize():
    """🔒 一个只有行情没有基本面的港股，不该比财务齐全的 A 股分低。

    实况：本项目 FinancialData 只有 a_share 4414 只，**港美股各 0 只**
    （2026-07-17 查库）。天真扣分 = 港美股全线拿不到高置信度，而这跟数据质量
    没关系 —— 那是我们还没接（P1-2 的活），不是港股的错。
    """
    a_share = _all_healthy()
    hk_stock = _all_healthy()
    hk_stock["fundamentals"] = not_supported_block("港股财务数据未接入")

    q_a = compute_quality(a_share)
    q_hk = compute_quality(hk_stock)

    assert q_hk.overall_score == q_a.overall_score == 100
    assert q_hk.level == "good"


def test_not_supported_is_excluded_from_denominator_not_scored_zero():
    """🔒 剔除权重 ≠ 记 0 分。记 0 分同样会把港美股打死。"""
    blocks = _all_healthy()
    blocks["fundamentals"] = not_supported_block()
    blocks["news"] = not_supported_block()

    q = compute_quality(blocks)
    assert q.overall_score == 100                    # 剩下三块都满 → 满分
    assert "fundamentals" not in q.block_scores      # 压根不参与
    assert "news" not in q.block_scores


def test_not_supported_core_block_does_not_trigger_hard_clamp():
    """🔒 `not_supported` 不算「降级」，不该触发硬传导。"""
    blocks = _all_healthy()
    blocks["technical"] = not_supported_block()
    assert is_core_degraded(blocks) is False


def test_all_not_supported_scores_zero_not_perfect():
    """🔒 「什么都评不了」不许因为「没有任何降级证据」而被当成完美。"""
    blocks = {k: not_supported_block() for k in
              ("quote", "daily_bars", "technical", "fundamentals", "news")}
    q = compute_quality(blocks)
    assert q.overall_score == 0
    assert q.level == "poor"


# ════════════════ 🔒 门禁：missing ≠ not_supported ════════════════

def test_absent_block_is_missing_not_not_supported():
    """🔒 块没填 = MISSING（该有却没有），不是 NOT_SUPPORTED。

    反过来的话，任何一次漏填都会变成静默豁免 —— 忘了传 quote 反而不扣分。
    要表达 not_supported 必须**显式**给一个 not_supported 的块。
    """
    q = compute_quality({})            # 什么都没传
    assert q.overall_score < 50
    assert q.core_degraded is True
    for key in CORE_BLOCKS:
        assert q.block_scores[key] == _STATUS_SCORES[FieldStatus.MISSING]


def test_fetch_failed_scores_worse_than_missing():
    """确实试了且失败 比 单纯没有 更值得警惕（它意味着系统有故障）。"""
    assert _STATUS_SCORES[FieldStatus.FETCH_FAILED] < _STATUS_SCORES[FieldStatus.MISSING]


# ════════════════ block 聚合（分歧 2：算出来，不硬编码）════════════════

def test_empty_block_is_not_supported():
    assert aggregate_block({}) is FieldStatus.NOT_SUPPORTED


def test_all_available_aggregates_to_available():
    assert aggregate_block({"a": _OK, "b": _OK}) is FieldStatus.AVAILABLE


def test_worst_field_wins_not_diluted_by_healthy_ones():
    """🔒 一个 fetch_failed 不许被一堆 available 稀释成「大体健康」。"""
    fields = {"a": _OK, "b": _OK, "c": QualityField(status=FieldStatus.FETCH_FAILED)}
    assert aggregate_block(fields) is FieldStatus.FETCH_FAILED


def test_stale_field_makes_block_stale():
    fields = {"a": _OK, "b": QualityField(status=FieldStatus.STALE)}
    assert aggregate_block(fields) is FieldStatus.STALE


def test_partial_when_mixed_with_soft_degradation():
    fields = {"a": _OK, "b": QualityField(status=FieldStatus.ESTIMATED)}
    assert aggregate_block(fields) is FieldStatus.PARTIAL


def test_block_of_only_not_supported_fields_is_not_supported():
    fields = {"a": QualityField(status=FieldStatus.NOT_SUPPORTED)}
    assert aggregate_block(fields) is FieldStatus.NOT_SUPPORTED


def test_not_supported_field_ignored_when_others_real():
    """块里混着「这个市场没有」的字段时，它不该影响其它字段的判定。"""
    fields = {"a": _OK, "b": QualityField(status=FieldStatus.NOT_SUPPORTED)}
    assert aggregate_block(fields) is FieldStatus.AVAILABLE


def test_block_from_fields_computes_status():
    """🔒 block 状态只能算出来，不许外部硬塞（蓝本让 builder 自己写死 → 会打架）。"""
    blk = block_from_fields({"a": QualityField(status=FieldStatus.STALE)})
    assert blk.status is FieldStatus.STALE


# ════════════════ bars_behind → 状态 ════════════════

def test_bars_behind_none_is_missing_not_available():
    """🔒 「不知道落后多少」不许当成「没落后」。"""
    assert status_for_bars_behind(None) is FieldStatus.MISSING


def test_bars_behind_zero_is_available():
    assert status_for_bars_behind(0) is FieldStatus.AVAILABLE


def test_bars_behind_one_is_stale():
    assert status_for_bars_behind(1) is FieldStatus.STALE
    assert status_for_bars_behind(5) is FieldStatus.STALE


# ════════════════ limitations 两档 ════════════════

def test_core_block_degradation_always_listed():
    blocks = _all_healthy()
    blocks["quote"] = block_from_fields({"f": QualityField(status=FieldStatus.STALE)})
    q = compute_quality(blocks)
    assert "quote: stale" in q.limitations


def test_aux_missing_is_not_listed():
    """🔒 辅助块单纯 missing **不进** limitations。

    抄蓝本的洞察：「今天没有腾讯的新闻」写进限制说明会被 LLM 读成一种信号
    （利好？利空？），而它其实什么都不是。
    """
    blocks = _all_healthy()
    blocks["news"] = block_from_fields({"f": QualityField(status=FieldStatus.MISSING)})
    q = compute_quality(blocks)
    assert not any(x.startswith("news") for x in q.limitations)


def test_aux_fetch_failed_is_listed():
    """但「本来有、这次没抓到」值得说 —— 那是故障不是空。"""
    blocks = _all_healthy()
    blocks["news"] = block_from_fields({"f": QualityField(status=FieldStatus.FETCH_FAILED)})
    q = compute_quality(blocks)
    assert "news: fetch_failed" in q.limitations


def test_limitations_capped_at_five():
    blocks = {k: block_from_fields({"f": QualityField(status=FieldStatus.FETCH_FAILED)})
              for k in ("quote", "daily_bars", "technical", "fundamentals", "news")}
    q = compute_quality(blocks)
    assert len(q.limitations) <= 5


def test_limitation_text_is_empty_when_healthy():
    assert limitation_text(compute_quality(_all_healthy())) == ""


# ════════════════ core_degraded ════════════════

def test_healthy_core_is_not_degraded():
    assert is_core_degraded(_all_healthy()) is False


def test_aux_degradation_does_not_make_core_degraded():
    """基本面没了不该触发硬传导 —— 只有核心三块说了算。"""
    blocks = _all_healthy()
    blocks["fundamentals"] = block_from_fields({"f": QualityField(status=FieldStatus.FETCH_FAILED)})
    assert is_core_degraded(blocks) is False


def test_each_core_block_can_trigger_degradation():
    for key in CORE_BLOCKS:
        blocks = _all_healthy()
        blocks[key] = block_from_fields({"f": QualityField(status=FieldStatus.STALE)})
        assert is_core_degraded(blocks) is True, f"{key} 降级必须触发"


# ════════════════ 🔒 硬传导 ════════════════

def test_clamp_pulls_high_confidence_down_when_core_degraded():
    """🔒 P0-2 的立身之本：核心数据降级 → 置信度**代码强行打下来**。

    不是 prompt 求 LLM 诚实，是算完之后 Python 说了算。
    """
    blocks = _all_healthy()
    blocks["quote"] = block_from_fields({"f": QualityField(status=FieldStatus.STALE)})
    q = compute_quality(blocks)

    clamped, adjustments = clamp_confidence(88.0, q)
    assert clamped == 60.0
    assert "confidence_capped_core_data_degraded" in adjustments


def test_clamp_does_not_touch_healthy_data():
    clamped, adjustments = clamp_confidence(88.0, compute_quality(_all_healthy()))
    assert clamped == 88.0
    assert adjustments == ()


def test_clamp_never_raises_a_low_confidence():
    """打压是单向的 —— 数据烂不该把一个本来就低的置信度**抬上去**。"""
    blocks = _all_healthy()
    blocks["quote"] = block_from_fields({"f": QualityField(status=FieldStatus.FETCH_FAILED)})
    clamped, _ = clamp_confidence(30.0, compute_quality(blocks))
    assert clamped == 30.0


def test_clamp_cap_sits_below_buy_threshold():
    """🔒 默认 cap=60 必须落在 BUY 线（cockpit 是 65）之下。

    否则「数据不可信」还能给出买入级别的把握，这条硬约束就是摆设。
    """
    from cockpit_engine.scorer import _recommendation
    blocks = _all_healthy()
    blocks["quote"] = block_from_fields({"f": QualityField(status=FieldStatus.STALE)})
    clamped, _ = clamp_confidence(99.0, compute_quality(blocks))
    assert _recommendation(clamped) != "BUY"


def test_clamp_none_confidence_is_noop():
    clamped, adjustments = clamp_confidence(None, compute_quality(_all_healthy()))
    assert clamped is None
    assert adjustments == ()


def test_adjustments_are_stable_identifiers_not_prose():
    """🔒 断言打在稳定标识符上 —— 文案会改，标识符不会（抄蓝本，这条它做对了）。"""
    blocks = _all_healthy()
    blocks["daily_bars"] = block_from_fields({"f": QualityField(status=FieldStatus.STALE)})
    _, adjustments = clamp_confidence(90.0, compute_quality(blocks))
    for a in adjustments:
        assert a.islower() and " " not in a


# ════════════════ 质量分 ════════════════

def test_healthy_is_full_score():
    q = compute_quality(_all_healthy())
    assert q.overall_score == 100
    assert q.level == "good"
    assert q.limitations == ()
    assert q.core_degraded is False


def test_score_is_versioned():
    """改口径必须能被 GROUP BY 分辨出来。"""
    assert compute_quality(_all_healthy()).version == DataQuality(
        overall_score=0, level="poor", block_scores={}, limitations=(),
        core_degraded=False).version


def test_core_block_weighs_more_than_aux():
    """核心块降级的扣分要比辅助块降级更狠。"""
    core_bad = _all_healthy()
    core_bad["quote"] = block_from_fields({"f": QualityField(status=FieldStatus.FETCH_FAILED)})
    aux_bad = _all_healthy()
    aux_bad["news"] = block_from_fields({"f": QualityField(status=FieldStatus.FETCH_FAILED)})

    assert compute_quality(core_bad).overall_score < compute_quality(aux_bad).overall_score


# ════════════════ worst_status ════════════════

def test_worst_status_ignores_not_supported():
    assert worst_status([FieldStatus.AVAILABLE, FieldStatus.NOT_SUPPORTED]) is FieldStatus.AVAILABLE
    assert worst_status([FieldStatus.NOT_SUPPORTED]) is FieldStatus.NOT_SUPPORTED
    assert worst_status([FieldStatus.AVAILABLE, FieldStatus.STALE]) is FieldStatus.STALE

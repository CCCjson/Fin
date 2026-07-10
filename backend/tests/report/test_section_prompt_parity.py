"""13.2-3 行为基线：章节工具的 prompt 与退役前的 build_multi 逐字相同。

这是「先原样搬、不改内容」这条决定的执行闸门。只要它绿着，新章节工具的产出
就能和 `scripts/data/baseline_samples/before_step13/report.md` 做逐章 diff——
差异只可能来自 LLM 随机性，而不是搬运时改坏了 prompt。

golden 是 `fixtures/section_prompts_golden.json`，在 build_multi 被删除**之前**从
它身上冻下来的（六个 report_type × 周末组合 × 每次调用的 system/user 哈希，
外加主路径 weekly|normal 的全文，好让 red 的时候能看清差在哪）。

输入是 13.1 冻结的 collect() 快照，纯函数，不打 LLM、不碰 DB。
"""
import hashlib
import json
import pathlib

import pytest

from report_engine.prompt_builder import SECTION_CHAPTERS, ReportPromptBuilder

pytestmark = pytest.mark.baseline

GOLDEN = json.loads(
    (pathlib.Path(__file__).parent / "fixtures" / "section_prompts_golden.json").read_text())

COMBOS = sorted(GOLDEN["hashes"])
# 五个章节工具覆盖旧的 8 章里的 7 章。Ch1「纵览 & 操作计划」不做成工具：
# 它需要全部前文，废掉全量报告后由 MoneyBill 主 agent 亲自撰写。
_ALL_CHAPTERS = {2, 3, 4, 5, 6, 7, 8}


def _sha(s: str) -> str:
    return hashlib.sha256(s.encode("utf-8")).hexdigest()


def _key(call) -> str:
    return f"8#{call.batch_data['batch_index']}" if call.chapter == 8 else str(call.chapter)


@pytest.fixture(scope="module")
def builder():
    return ReportPromptBuilder()


def _calls_by_key(builder, data, report_type) -> dict:
    out = {}
    for section in SECTION_CHAPTERS:
        for call in builder.build_section_calls(section, data, report_type):
            out[_key(call)] = call
    return out


# ── 覆盖面 ────────────────────────────────────────────────────────────────

def test_sections_cover_every_chapter_except_ch1(builder, report_data_weekly):
    calls = _calls_by_key(builder, report_data_weekly, "weekly")
    chapters = {c.chapter for c in calls.values()}
    assert chapters == _ALL_CHAPTERS
    assert 1 not in chapters, "Ch1 纵览应由 MoneyBill 撰写，不做成章节工具"


def test_every_section_maps_to_at_least_one_chapter():
    assert set(SECTION_CHAPTERS) == {"market", "news", "positions", "strategy", "picks"}
    assert sorted(c for chs in SECTION_CHAPTERS.values() for c in chs) == sorted(_ALL_CHAPTERS)


def test_golden_covers_all_six_report_type_weekend_combos():
    assert COMBOS == ["daily|normal", "daily|weekend", "monthly|normal",
                      "monthly|weekend", "weekly|normal", "weekly|weekend"]


# ── 逐字对齐 golden ───────────────────────────────────────────────────────

@pytest.mark.parametrize("combo", COMBOS)
def test_prompts_match_golden(combo, builder, report_data_weekly):
    report_type, weekend = combo.split("|")
    data = dict(report_data_weekly, is_weekend=(weekend == "weekend"))
    calls = _calls_by_key(builder, data, report_type)
    golden = GOLDEN["hashes"][combo]

    assert sorted(calls) == sorted(golden), f"{combo} 的调用集合变了"
    for key, want in golden.items():
        got = calls[key]
        assert _sha(got.system_prompt) == want["system"], f"{combo} {key} system_prompt 漂移"
        assert _sha(got.user_prompt) == want["user"], f"{combo} {key} user_prompt 漂移"
        assert got.temperature == want["temperature"], f"{combo} {key} temperature 漂移"
        assert got.label == want["label"], f"{combo} {key} label 漂移"


def test_weekly_normal_prompts_match_golden_verbatim(builder, report_data_weekly):
    """主路径存了全文：red 的时候能直接看出差在哪个字，而不是只看到哈希不等。"""
    calls = _calls_by_key(builder, report_data_weekly, "weekly")
    for key, want in GOLDEN["weekly_normal_full"].items():
        assert calls[key].system_prompt == want["system"], f"Ch{key} system_prompt"
        assert calls[key].user_prompt == want["user"], f"Ch{key} user_prompt"


def test_ch8_batches_are_deterministic_slices_of_four(builder, report_data_weekly):
    recs = report_data_weekly["top_stocks"]["buy_recommendations"]
    calls = builder.build_section_calls("picks", report_data_weekly, "weekly")
    assert len(calls) == -(-len(recs) // 4) > 1
    for i, call in enumerate(calls):
        bd = call.batch_data
        assert bd["batch_index"] == i
        assert bd["total_batches"] == len(calls)
        assert bd["is_first"] == (i == 0)
        assert bd["is_last"] == (i == len(calls) - 1)
        assert [s["symbol"] for s in bd["stocks"]] == [s["symbol"] for s in recs[i * 4:i * 4 + 4]]


# ── 降级与拒绝 ────────────────────────────────────────────────────────────

def test_no_buy_recommendations_degrades_to_single_watch_call(builder, report_data_weekly):
    """无买入推荐时退化为单次「观望」调用——与旧路径同样的降级。"""
    data = dict(report_data_weekly, top_stocks={"buy_recommendations": [], "sell_warnings": []})
    calls = builder.build_section_calls("picks", data, "weekly")
    assert len(calls) == 1
    assert calls[0].label == "正在生成第8章（今日无高质量买入信号）..."
    assert calls[0].batch_data == {"batch_index": 0, "total_batches": 1, "stocks": [],
                                   "is_first": True, "is_last": True}


def test_unknown_section_rejected(builder, report_data_weekly):
    with pytest.raises(ValueError, match="未知报告章节"):
        builder.build_section_calls("nope", report_data_weekly, "weekly")


# ── Ch8 补充调用的 prompt 也搬得逐字不差 ────────────────────────────────────

def test_ch8_supplement_prompt_keeps_the_old_shape(report_data_weekly):
    """build_ch8_supplement_prompt 是从 generator._retry_missing_stocks 里抠出来的。"""
    recs = report_data_weekly["top_stocks"]["buy_recommendations"][:2]
    prompt = ReportPromptBuilder.build_ch8_supplement_prompt(recs)
    assert "你在上面的分析中遗漏了以下标的" in prompt
    assert "⚠️ 必须分析的标的：" in prompt
    for r in recs:
        assert r["symbol"] in prompt
        assert f"综合评分: {r.get('composite_score', 'N/A')}/100" in prompt

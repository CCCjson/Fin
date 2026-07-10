"""13.2-3 行为基线：章节工具的 prompt 与旧 build_multi 逐字相同。

这是「先原样搬、不改内容」这条决定的执行闸门。只要它绿着，新章节工具的产出
就能和 `scripts/data/baseline_samples/before_step13/report.md` 做逐章 diff——
差异只可能来自 LLM 随机性，而不是搬运时改坏了 prompt。

用 13.1 冻结的 collect() 快照当输入，纯函数，不打 LLM、不碰 DB。
"""
import pytest

from report_engine.prompt_builder import SECTION_CHAPTERS, ReportPromptBuilder

pytestmark = pytest.mark.baseline

# 五个章节工具覆盖旧的 8 章里的 7 章。Ch1「纵览 & 操作计划」不做成工具：
# 它需要全部前文，废掉全量报告后由 MoneyBill 主 agent 亲自撰写。
_CHAPTER_TO_SECTION = {2: "market", 4: "market", 3: "news",
                       5: "positions", 6: "strategy", 7: "strategy", 8: "picks"}


@pytest.fixture(scope="module")
def builder():
    return ReportPromptBuilder()


@pytest.fixture(scope="module")
def old_specs(builder, report_data_weekly):
    """旧全量路径的 10 个 CallSpec（含 Ch8 三批）。"""
    return builder.build_multi(report_data_weekly, "weekly")


@pytest.fixture(scope="module")
def new_calls(builder, report_data_weekly):
    """新章节工具展开出的全部 ChapterCall，按章节号分组。"""
    out: dict[int, list] = {}
    for section in SECTION_CHAPTERS:
        for call in builder.build_section_calls(section, report_data_weekly, "weekly"):
            out.setdefault(call.chapter, []).append(call)
    return out


def test_sections_cover_every_chapter_except_ch1(new_calls, old_specs):
    old_chapters = {s.chapters[0] for s in old_specs}
    assert set(new_calls) == old_chapters - {1}
    assert 1 not in new_calls, "Ch1 纵览应由 MoneyBill 撰写，不做成章节工具"


def test_ch8_batches_match_old_path(new_calls, old_specs):
    """Ch8 的分批数与每批标的必须与旧路径一致（batch_size=4 的确定性切分）。"""
    old_ch8 = [s for s in old_specs if s.chapters[0] == 8]
    new_ch8 = new_calls[8]
    assert len(new_ch8) == len(old_ch8) > 1, "冻结快照应产生多批 Ch8"
    for old, new in zip(old_ch8, new_ch8, strict=True):
        assert new.batch_data["batch_index"] == old.batch_data["batch_index"]
        assert new.batch_data["total_batches"] == old.batch_data["total_batches"]
        assert new.batch_data["is_first"] == old.batch_data["is_first"]
        assert new.batch_data["is_last"] == old.batch_data["is_last"]
        assert ([s["symbol"] for s in new.batch_data["stocks"]]
                == [s["symbol"] for s in old.batch_data["stocks"]])


@pytest.mark.parametrize("chapter", sorted(_CHAPTER_TO_SECTION))
def test_system_prompt_is_byte_identical(chapter, new_calls, old_specs):
    old = [s for s in old_specs if s.chapters[0] == chapter]
    new = new_calls[chapter]
    assert len(new) == len(old)
    for o, n in zip(old, new, strict=True):
        assert n.system_prompt == o.system_prompt, f"Ch{chapter} system_prompt 漂移"


@pytest.mark.parametrize("chapter", sorted(_CHAPTER_TO_SECTION))
def test_user_prompt_is_byte_identical(chapter, new_calls, old_specs):
    """旧路径的 user_prompt 是 build_multi 预构建的那份（尚未前置前章结论摘要）。"""
    old = [s for s in old_specs if s.chapters[0] == chapter]
    new = new_calls[chapter]
    for o, n in zip(old, new, strict=True):
        assert n.user_prompt == o.user_prompt, f"Ch{chapter} user_prompt 漂移"


@pytest.mark.parametrize("chapter", sorted(_CHAPTER_TO_SECTION))
def test_temperature_and_label_preserved(chapter, new_calls, old_specs):
    old = [s for s in old_specs if s.chapters[0] == chapter]
    new = new_calls[chapter]
    for o, n in zip(old, new, strict=True):
        assert n.temperature == o.temperature, f"Ch{chapter} temperature 漂移"
        assert n.label == o.label, f"Ch{chapter} label 漂移"


# ── 三种报告周期 / 周末变体都要对齐 ────────────────────────────────────────

@pytest.mark.parametrize("report_type", ["daily", "weekly", "monthly"])
@pytest.mark.parametrize("is_weekend", [False, True])
def test_prompt_parity_across_report_type_and_weekend(builder, report_data_weekly,
                                                      report_type, is_weekend):
    """weekend / daily / 普通 三分支的选择逻辑在新旧两处必须给出同一个 _sys_chN。"""
    data = dict(report_data_weekly, is_weekend=is_weekend)
    old_specs = builder.build_multi(data, report_type)
    new_by_ch: dict[int, list] = {}
    for section in SECTION_CHAPTERS:
        for call in builder.build_section_calls(section, data, report_type):
            new_by_ch.setdefault(call.chapter, []).append(call)

    for spec in old_specs:
        ch = spec.chapters[0]
        if ch == 1:
            continue
        idx = (spec.batch_data or {}).get("batch_index", 0)
        got = new_by_ch[ch][idx]
        assert got.system_prompt == spec.system_prompt, f"{report_type}/weekend={is_weekend} Ch{ch}"
        assert got.user_prompt == spec.user_prompt, f"{report_type}/weekend={is_weekend} Ch{ch}"


def test_no_buy_recommendations_degrades_to_single_watch_call(builder, report_data_weekly):
    """无买入推荐时退化为单次「观望」调用——与旧路径同样的降级。"""
    data = dict(report_data_weekly, top_stocks={"buy_recommendations": [], "sell_warnings": []})
    old_ch8 = [s for s in builder.build_multi(data, "weekly") if s.chapters[0] == 8]
    new_ch8 = builder.build_section_calls("picks", data, "weekly")
    assert len(new_ch8) == len(old_ch8) == 1
    assert new_ch8[0].label == old_ch8[0].label == "正在生成第8章（今日无高质量买入信号）..."
    assert new_ch8[0].system_prompt == old_ch8[0].system_prompt
    assert new_ch8[0].user_prompt == old_ch8[0].user_prompt


def test_unknown_section_rejected(builder, report_data_weekly):
    with pytest.raises(ValueError, match="未知报告章节"):
        builder.build_section_calls("nope", report_data_weekly, "weekly")


# ── Ch8 补充调用的 prompt 也搬得逐字不差 ────────────────────────────────────

def test_ch8_supplement_prompt_matches_old_inline_construction(builder, report_data_weekly):
    """build_ch8_supplement_prompt 是从 generator._retry_missing_stocks 里抠出来的。"""
    recs = report_data_weekly["top_stocks"]["buy_recommendations"][:2]
    prompt = ReportPromptBuilder.build_ch8_supplement_prompt(recs)
    assert "你在上面的分析中遗漏了以下标的" in prompt
    assert "⚠️ 必须分析的标的：" in prompt
    for r in recs:
        assert r["symbol"] in prompt
        assert f"综合评分: {r.get('composite_score', 'N/A')}/100" in prompt

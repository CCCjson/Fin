"""防复发门禁：prompt 改了却忘了 bump 版本号 / 新写入点忘了传 prompt_version。

**两条门禁各管一半，缺一不可：**
- `test_prompt_sources_pinned` —— 钉源文件 hash：**改了 prompt 忘了 bump** 会红
- `test_all_write_sites_pass_prompt_version` —— 源码 grep：**新加写入点忘了传** 会红

为什么是「常量 bump + 测试期 hash」而不是运行时算 hash（2026-07-17 拍板）：
- report 的 prompt 是**逐股动态渲染**的（f-string 二次拼装 + _persona() 等函数生成
  片段）→ 对渲染后文本取 hash = 每只股票一个 hash = 不是版本号是随机数，
  `GROUP BY prompt_version` 会碎成 N 组，归因直接废掉
- 模块级算 hash 在 **import 期**读文件，任何缺文件/打包部署都会让服务起不来
- `a3f9c1e2` 不可读，`report-v1` 能让人直接对上「哦，就是加了新闻规则那次」
"""
import hashlib
import pathlib

import pytest

pytestmark = pytest.mark.baseline

_BACKEND = pathlib.Path(__file__).resolve().parents[2]


def _sha16(rel: str) -> str:
    return hashlib.sha256((_BACKEND / rel).read_bytes()).hexdigest()[:16]


# 文件 → (版本常量所在模块里的期望值, 该文件当前内容的 hash)
#
# ⚠️ **这个测试红了怎么办 —— 先分清是哪种情况：**
#   1. 你改了 prompt 的**语义**（加规则/改人设/调口径）
#      → bump 对应的 PROMPT_VERSION 常量（v1 → v2），**再**更新下面的 hash
#   2. 你只是改了**注释/排版/typo**，没动 prompt 语义
#      → **别 bump 版本号**（版本号只该跟着语义变），直接更新下面的 hash 即可
# 报错信息里也写了这段，不用回来翻。
_PINNED = {
    "advisor_engine/prompt_builder.py": "advisor-v1",
    "cockpit_engine/prompt_builder.py": "cockpit-v1",
    "report_engine/prompt_builder.py": "report-v1",
    "cockpit_engine/scorer.py": "rule:cockpit-v2",
    "agents/skills/monitor.md": "moneybill-v2",
}

_HASHES = {
    "advisor_engine/prompt_builder.py": "c027ca6d1341159b",
    "cockpit_engine/prompt_builder.py": "c263d993c5879a7e",
    "report_engine/prompt_builder.py": "7e39b2d330db4ac9",
    "cockpit_engine/scorer.py": "d8e8c86955bb3a7d",
    "agents/skills/monitor.md": "3866170a014c1258",
}


@pytest.mark.parametrize("rel", sorted(_PINNED))
def test_prompt_sources_pinned(rel):
    """源文件内容变了 → 红，逼你决定要不要 bump 版本号。"""
    actual = _sha16(rel)
    assert actual == _HASHES[rel], (
        f"\n{rel} 的内容变了（当前 hash={actual}，钉的是 {_HASHES[rel]}）。\n"
        f"  · 改了 prompt 的**语义**？→ bump 版本常量（现在是 {_PINNED[rel]!r}），"
        f"再把 _HASHES 里这行更新成 {actual!r}\n"
        f"  · 只改了注释/排版/typo？→ **别 bump 版本号**，直接把 _HASHES 更新成 "
        f"{actual!r} 即可\n"
        f"（为什么要这么麻烦：prompt_version 是 DecisionLog 归因的唯一依据，"
        f"漏 bump = 两版 prompt 的胜率被混在一起算，而且你永远不会发现。）"
    )


def test_version_constants_match_pinned_values():
    """常量的实际取值要和上面 _PINNED 登记的一致（防止改了常量没更新本表）。"""
    # 三个模块导出同名的 PROMPT_VERSION，只能起别名区分（保持全大写以示常量）
    from advisor_engine.prompt_builder import PROMPT_VERSION as ADVISOR_V
    from agents.skills_loader import MONITOR_PROMPT_VERSION
    from cockpit_engine.prompt_builder import PROMPT_VERSION as COCKPIT_V
    from cockpit_engine.scorer import SCORER_VERSION
    from report_engine.prompt_builder import PROMPT_VERSION as REPORT_V

    assert ADVISOR_V == _PINNED["advisor_engine/prompt_builder.py"]
    assert COCKPIT_V == _PINNED["cockpit_engine/prompt_builder.py"]
    assert REPORT_V == _PINNED["report_engine/prompt_builder.py"]
    assert SCORER_VERSION == _PINNED["cockpit_engine/scorer.py"]
    assert MONITOR_PROMPT_VERSION == _PINNED["agents/skills/monitor.md"]


def test_version_values_fit_the_column():
    """DecisionLog.prompt_version 是 String(30) —— 别写超了被静默截断。"""
    for rel, v in _PINNED.items():
        assert len(v) <= 30, f"{rel} 的版本号 {v!r} 超过 30 字符"


# 五个写入点：file → 该文件里必须出现的调用片段。
# 这条门禁兜的是「**新加写入点忘了传 prompt_version**」——它兜不住「改了 prompt
# 忘了 bump」（那个归上面的 hash 门禁）。范式抄 test_decision_confidence_scale.py。
_WRITE_SITES = [
    "advisor_engine/service.py",
    "report_engine/picks_log.py",
    "agents/confirm_gate.py",
    "recommend_engine/engine.py",
    "agents/tools/analysis_tools.py",   # cockpit 留痕（刻意接在工具层，不在 aggregator）
]


@pytest.mark.parametrize("rel", _WRITE_SITES)
def test_all_write_sites_pass_prompt_version(rel):
    src = (_BACKEND / rel).read_text(encoding="utf-8")
    assert "record_decision(" in src, f"{rel} 不再是 record_decision 的写入点了？请更新本表"
    assert "prompt_version=" in src, (
        f"{rel} 调了 record_decision 却没传 prompt_version —— "
        f"这条决策落库后无法归因到具体 prompt/口径版本。"
    )


def test_cockpit_provenance_is_not_in_aggregator():
    """cockpit 留痕**不许**写回 aggregator.aggregate()。

    aggregate 有两个调用方，另一个是 recommend_engine/scoring.py 的 _aggregate_many()
    —— 批量选股循环，每轮几十只。写在 aggregate 里 = 每跑一次选股就涌几十条
    「Jason 从没看见过的中间打分」进 DecisionLog，且与 moneybill_recommend 重复
    计数同一个建议 → **胜率分母被中间产物泡掉**。
    """
    src = (_BACKEND / "cockpit_engine/aggregator.py").read_text(encoding="utf-8")
    assert "record_decision" not in src, (
        "cockpit 的决策留痕必须留在 agents/tools/analysis_tools.py（工具层），"
        "不能下沉到 aggregator —— 批量选股会灌爆 DecisionLog。"
    )

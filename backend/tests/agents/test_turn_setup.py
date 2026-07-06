"""
skill frontmatter 解析 + prepare_turn 把 enabled_tools 并入 allowed_tools 的单测。

覆盖：
- parse_skill_frontmatter 对「有/无/格式错误」frontmatter 的行为
- load_monitor_system_prompt 不泄漏 frontmatter 原文
- prepare_turn 会把 skill 声明的工具组并入 session.allowed_tools（真跑 initial_allowed+expand）
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

os.environ.setdefault("AGENT_TOOL_GROUPS", "off")
os.environ.setdefault("AGENT_TRACE", "off")

import pytest  # noqa: E402

from agents import skills_loader, tool_groups, turn_setup  # noqa: E402
from agents.context import AgentSession  # noqa: E402


# ---------- frontmatter 解析 ----------

def test_parse_frontmatter_present():
    text = "---\nenabled_tools:\n  - signals\n  - screener\n---\n# 正文\nhello"
    meta, body = skills_loader._split_frontmatter(text)
    assert meta == {"enabled_tools": ["signals", "screener"]}
    assert body.startswith("# 正文")


def test_parse_frontmatter_inline_list():
    text = "---\nenabled_tools: [core, news]\n---\nbody"
    meta = skills_loader._parse_frontmatter_block("enabled_tools: [core, news]")
    # 内联标量，parse_skill_frontmatter 再规整成 list
    assert meta["enabled_tools"] == "[core, news]"
    _, body = skills_loader._split_frontmatter(text)
    assert body == "body"


def test_parse_frontmatter_absent():
    text = "# 直接正文\n无 frontmatter"
    meta, body = skills_loader._split_frontmatter(text)
    assert meta == {}
    assert body == text  # 原文不动


def test_parse_skill_frontmatter_malformed(monkeypatch):
    # 只有起始 --- 没有闭合，或纯垃圾 → 优雅降级 {}
    monkeypatch.setattr(skills_loader, "_read",
                        lambda name: "---\nenabled_tools:\n  - signals\n(没有闭合)")
    assert skills_loader.parse_skill_frontmatter("x.md") == {}

    monkeypatch.setattr(skills_loader, "_read", lambda name: "随便一段没有任何标记的文本")
    assert skills_loader.parse_skill_frontmatter("x.md") == {}


def test_parse_skill_frontmatter_real_monitor():
    meta = skills_loader.parse_skill_frontmatter("monitor.md")
    assert set(meta.get("enabled_tools", [])) >= {"signals", "screener", "news"}


def test_monitor_prompt_strips_frontmatter():
    sp = skills_loader.load_monitor_system_prompt()
    assert "enabled_tools" not in sp[:200]
    assert sp.lstrip().startswith("# 你是 MoneyBill")


# ---------- prepare_turn 并入工具组 ----------

@pytest.fixture
def controlled_groups(monkeypatch):
    """用受控的组定义替换真实 REGISTRY 分组，避免其它测试注册的假工具污染。"""
    core = frozenset({"get_realtime_quote"})
    groups = {
        "signals": frozenset({"get_today_signals"}),
        "screener": frozenset({"screen_stocks"}),
        "news": frozenset({"get_news_score"}),
        "backtest_ml": frozenset({"run_backtest"}),
    }
    monkeypatch.setattr(tool_groups, "_registry_groups", lambda: (core, groups))
    monkeypatch.setattr(tool_groups, "grouping_enabled", lambda: True)
    return core, groups


def test_prepare_turn_merges_skill_groups(controlled_groups):
    sess = AgentSession(session_id="t_skill")
    turn_setup.prepare_turn(sess, "今天买什么", page_context=None)
    allowed = sess.allowed_tools
    # 核心 + 元工具 + skill 声明的 signals/screener/news 组的工具都在
    assert {"get_realtime_quote", tool_groups.META_TOOL,
            "get_today_signals", "screen_stocks", "get_news_score"} <= allowed
    # 未声明的组不应被拉进来
    assert "run_backtest" not in allowed


def test_prepare_turn_no_frontmatter_unchanged(controlled_groups, monkeypatch):
    # skill 无 enabled_tools 时，只剩「核心 + 元工具 + 页面预载」，行为与之前一致
    monkeypatch.setattr(skills_loader, "parse_skill_frontmatter", lambda name: {})
    sess = AgentSession(session_id="t_plain")
    turn_setup.prepare_turn(sess, "你好", page_context=None)
    assert sess.allowed_tools == {"get_realtime_quote", tool_groups.META_TOOL}

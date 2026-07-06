"""
工具注册表全量契约测试（Phase 1 安全网 + Phase 2 迁移回归网）。

`import agents` 触发 agents/__init__.py 的完整注册副作用（tools + subagents +
tool_groups），让 REGISTRY 装满生产环境的真实工具集——不这样做的话 REGISTRY
在独立跑测试文件时几乎是空的（其它测试文件只按需 import 到自己关心的那个
tools 模块），契约测试就测不到真实工具。
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

os.environ.setdefault("AGENT_TOOL_GROUPS", "off")
os.environ.setdefault("AGENT_TRACE", "off")

import pytest  # noqa: E402

import agents  # noqa: F401,E402  — 触发全量工具/子代理注册
from agents.executor import run_tool  # noqa: E402
from agents.registry import REGISTRY  # noqa: E402
from agents.tool_groups import META_TOOL  # noqa: E402


def _all_tools():
    """REGISTRY 是跨测试文件共享的全局单例——其它测试文件会往里注册category="test"
    的一次性假工具（且大多不声明 group，本就不打算参与真实契约）。只审计真实
    生产工具，避免测试收集顺序（谁先 import 谁先注册）污染这份契约清单。"""
    return [td for td in REGISTRY.all() if td.category != "test"]


def _id(td):
    return td.name


# 已知历史债务：knowledge_engine/tools.py 注册的工具还没迁到 args_model
# （Phase 4 只挪注册位置，不在本次范围内迁移 schema）。这是一份收缩型白名单——
# 只能变短不能变长：新工具落地时若也漏了 args_model，这里的断言会红。
_LEGACY_NO_ARGS_MODEL = {
    "search_knowledge", "web_search", "sec_search",
    "read_url", "login_site", "scrape", "list_alpha_ideas",
}


@pytest.mark.parametrize("td", _all_tools(), ids=_id)
def test_every_tool_has_a_group(td):
    """load_toolgroup 自己不需要归组（元工具），其余全部工具必须声明 group
    （tool_groups._registry_groups 在 import 期已经强制校验过这点，这里是双保险，
    也让"为什么 group 很重要"在测试里留一份可读证据）。"""
    if td.name == META_TOOL:
        pytest.skip("元工具自己不归组")
    assert td.group, f"{td.name} 未声明 group"


@pytest.mark.parametrize("td", _all_tools(), ids=_id)
def test_domain_tools_have_args_model_unless_known_legacy(td):
    """普通领域工具应该用 args_model 声明参数——单一数据源同时承担运行时校验与
    schema 生成。已知例外见 _LEGACY_NO_ARGS_MODEL。

    Phase 2 后 subagent 也已迁移到 args_model（tool_dispatch.dispatch 校验），
    与域工具走同一条断言；只有元工具 load_toolgroup（enum 需动态注入组名，
    手写 parameters 有正当理由）继续例外。"""
    if td.name == META_TOOL:
        pytest.skip("元工具的 enum 需动态注入组名，手写 parameters 有正当理由")
    if td.name in _LEGACY_NO_ARGS_MODEL:
        assert td.args_model is None, (
            f"{td.name} 已经有 args_model 了，请把它从 _LEGACY_NO_ARGS_MODEL 里移除")
        return
    assert td.args_model is not None, (
        f"{td.name} 缺少 args_model——新工具必须用 pydantic 模型声明参数"
        "（若确有历史原因做不到，请显式加入 _LEGACY_NO_ARGS_MODEL 并说明理由）")


@pytest.mark.parametrize("td", _all_tools(), ids=_id)
def test_requires_confirmation_tools_have_preview_fn(td):
    """危险写操作的确认门必须能生成预览——没有预览的确认弹窗对用户没有意义。"""
    if td.requires_confirmation:
        assert td.preview_fn is not None, (
            f"{td.name} 要求人工确认却没有 preview_fn，用户点确认时看不到预览详情")


@pytest.mark.parametrize("td", [td for td in _all_tools() if not td.is_subagent], ids=_id)
def test_tool_exception_is_caught_and_returns_ok_false(monkeypatch, td):
    """任意工具函数内部抛出未预期异常时，run_tool 必须兜底成 ok:False + error_code，
    绝不能让异常穿透到 orchestrator（那会直接打断整条聊天流）。

    跳过 subagent：它们注册的 fn 是占位 _noop，不走 run_tool（orchestrator 特判
    直接分流到 SubagentRunner），在这里 mock 它没有意义。

    传空 args：临时把 args_model 关掉（设为 None），跳过参数校验直接进 fn——
    否则大量必填字段的工具会在校验阶段就短路返回 validation_error，
    boom() 根本不会被调用，测的就不是"fn 异常"这条路径了。"""
    def boom(**kwargs):
        raise RuntimeError("契约测试注入的异常")

    monkeypatch.setattr(td, "fn", boom)
    if td.args_model is not None:
        monkeypatch.setattr(td, "args_model", None)
    result = run_tool(td.name, {})
    assert result["ok"] is False, f"{td.name} 异常后 run_tool 应返回 ok:False"
    # 迁移到 ToolEnvelope 的工具会带 error_code；未迁移的裸 dict 兜底路径
    # （executor.py 的 except Exception 分支）也会带，二者都应该有
    assert result.get("error_code") == "internal_error"

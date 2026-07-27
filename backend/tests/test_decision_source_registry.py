"""门禁：`DecisionLog.source` 的真源表必须与代码里的三处用法保持一致（S0 §2.2）。

# 为什么要有这张门禁

`agents/tools/decision_tools.py` 的 `_SOURCES` Literal 是一串**手写枚举**，靠人记得
同步。实测它漏了 4 个（`crypto` / `crypto_cockpit` / `crypto_earn` + 组合），后果是
**MoneyBill 根本没法按 crypto 查胜率** —— 而且不报错，只是 pydantic 校验直接拒掉参数。

这与 `entry_kind` 那颗炸弹是**同款成因**：靠命名约定 + 人肉同步的分类。补完这次，
下一个新 source 照样会漏。所以钉三条：

1. Literal ⇄ 表键集一致（pydantic 要静态字面量，只能手写，但不能手写错）
2. 全项目 `record_decision(source=...)` 的取值必须都在表里（AST 扫源码）
3. 工具 description 里必须提到表里每个 source（防有人把生成的清单改回硬编码）

⚠️ 抄 `test_decision_entry_kind.py` 的坑一起抄了：**读源码 AST，不读运行期
`REGISTRY`** —— 别的测试会往全局 REGISTRY 里塞假工具，读它会「单跑绿、全套红」。
（第 3 条例外：它查的就是工具自己的 description 字符串，直接从模块常量取。）
"""
import ast
import os
import typing

import pytest

from common.decision_kind import ADVICE, ENTRY_KINDS
from common.decision_source import (
    ADVICE_SOURCES,
    ALL_SOURCES,
    SOURCES,
    advice_alternative,
    describe_for_prompt,
    has_advice,
    kinds_of,
    label_of,
)

_BACKEND = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


# ── 0. 表本身自洽 ─────────────────────────────────────────────────────────

def test_registry_is_self_consistent():
    for name, spec in SOURCES.items():
        assert name == name.strip() and name, f"source 名不能带空白：{name!r}"
        assert spec["label"], f"{name} 缺中文说明"
        assert spec["kinds"], f"{name} 的 kinds 是空集 —— 那它属于哪一类？"
        assert spec["kinds"] <= ENTRY_KINDS, f"{name} 的 kinds 越界：{spec['kinds']}"
        hint = spec.get("advice_hint")
        if hint is not None:
            assert hint in SOURCES, f"{name} 指路指向了不存在的 source：{hint}"
            assert has_advice(hint), f"{name} 指路指向了同样没有建议的 {hint}"
        else:
            # 没有指路的，自己就该有建议 —— 否则 LLM 查空了会得不到任何去向
            assert has_advice(name), f"{name} 既没有 advice 也没给 advice_hint"


def test_advice_sources_are_exactly_the_ones_with_advice():
    assert set(ADVICE_SOURCES) == {s for s in ALL_SOURCES if ADVICE in kinds_of(s)}
    # 至少 cockpit 系在里面 —— 防这个列表哪天被清空还全绿
    assert "cockpit" in ADVICE_SOURCES and "crypto_cockpit" in ADVICE_SOURCES


def test_unknown_source_is_not_guessed():
    """未登记的 source **不猜** —— 空集比猜错好（猜错会把回执塞进胜率分母）。"""
    assert kinds_of("nope") == frozenset()
    assert has_advice("nope") is False
    assert advice_alternative("nope") is None
    assert label_of("nope") == "nope"


# ── 1. Literal ⇄ 表键集 ───────────────────────────────────────────────────

def test_literal_matches_registry():
    from agents.tools import decision_tools

    literal = set(typing.get_args(decision_tools._SOURCES))
    assert literal == set(ALL_SOURCES), (
        f"decision_tools._SOURCES 与 common.decision_source.SOURCES 不一致。"
        f"漏在 Literal 里的：{sorted(set(ALL_SOURCES) - literal)}；"
        f"多出来的：{sorted(literal - set(ALL_SOURCES))}。"
        f"（pydantic 要静态字面量所以只能手写，但不能手写错 —— 漏一个就是"
        f"「MoneyBill 按这个来源查不了」，且不报错只查回 0 条）"
    )


# ── 2. AST 扫全项目的 record_decision(source=...) ─────────────────────────

def _iter_backend_py():
    for root, _dirs, files in os.walk(_BACKEND):
        if any(p in root for p in (os.sep + "tests", os.sep + "__pycache__", os.sep + ".")):
            continue
        for fn in files:
            if fn.endswith(".py"):
                yield os.path.join(root, fn)


def _module_level_str_consts(tree: ast.Module) -> dict:
    """收 `NAME = "字面量"` 形式的模块级常量 —— `report_engine/picks_log.py` 传的是
    `source=PICKS_SOURCE`，不解析它就会被误判成「拿不到取值」。"""
    out = {}
    for node in tree.body:
        if isinstance(node, ast.Assign) and isinstance(node.value, ast.Constant) \
                and isinstance(node.value.value, str):
            for t in node.targets:
                if isinstance(t, ast.Name):
                    out[t.id] = node.value.value
    return out


def _record_decision_aliases(tree: ast.Module) -> set:
    """`record_decision` 在本文件里的所有可调用名字。

    ⚠️ 不收别名就会被 `from decision_log import record_decision as rd` 绕过 ——
    实测：这么写一个 source 全是脏值的写入点，门禁**十条全绿**。
    """
    names = {"record_decision"}
    for node in ast.walk(tree):
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            for a in node.names:
                if a.name == "record_decision" and a.asname:
                    names.add(a.asname)
    return names


def _collect_source_args():
    """→ [(相对路径, 行号, 取值 或 None)]，None = 不是能静态求值的字面量。"""
    found = []
    for path in _iter_backend_py():
        rel = os.path.relpath(path, _BACKEND)
        if rel == "decision_log.py":
            continue                                  # 定义处，不是写入点
        src = open(path, encoding="utf-8").read()
        # 前缀过滤不带 `(`：`import record_decision as rd` 这行里没有括号，
        # 带括号过滤会在读 AST 之前就把整个文件跳掉。
        if "record_decision" not in src:
            continue
        tree = ast.parse(src)
        aliases = _record_decision_aliases(tree)
        consts = _module_level_str_consts(tree)
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            fname = getattr(node.func, "id", None) or getattr(node.func, "attr", None)
            if fname not in aliases:
                continue
            for kw in node.keywords:
                if kw.arg != "source":
                    continue
                v = kw.value
                if isinstance(v, ast.Constant) and isinstance(v.value, str):
                    found.append((rel, node.lineno, v.value))
                elif isinstance(v, ast.Name) and v.id in consts:
                    found.append((rel, node.lineno, consts[v.id]))
                else:
                    found.append((rel, node.lineno, None))
    return found


def test_every_write_site_source_is_registered():
    args = _collect_source_args()
    assert args, "一个 record_decision(source=) 都没扫到？AST 扫描本身坏了"

    unknown = [(f, ln, v) for f, ln, v in args if v is not None and v not in SOURCES]
    assert not unknown, (
        f"这些写入点用了没登记的 source：{unknown}。"
        f"请先去 common/decision_source.py 登记（写清 label 和它属于哪个 entry_kind），"
        f"再补 decision_tools._SOURCES —— 不然 MoneyBill 按它查不到东西，且不报错。"
    )

    dynamic = [(f, ln) for f, ln, v in args if v is None]
    assert not dynamic, (
        f"这些写入点的 source 不是静态字面量/模块级常量，门禁看不见：{dynamic}。"
        f"source 是要被枚举的分类字段，**别动态拼**。"
    )


def test_registry_has_no_dead_entries():
    """反方向：表里登记了但全项目没人写 —— 说明写入点没了，该清理。"""
    written = {v for _f, _ln, v in _collect_source_args() if v}
    # confirm_gate 走 `source="moneybill"` 字面量，crypto 三个也都是字面量，
    # 所以这里应当能覆盖全表。真有例外要在下面显式豁免并写清理由。
    dead = set(ALL_SOURCES) - written
    assert not dead, (
        f"表里这些 source 已经没有写入点了：{sorted(dead)}。"
        f"要么是写入点被删了（该从表里摘掉），要么是新写入点用了变量（门禁看不见）。"
    )


def test_legacy_backfill_sql_handles_every_non_advice_source():
    """`init_db` 里那条 entry_kind 存量归类 SQL 是**第四张手写清单**。

    它 `ELSE 'advice'` 兜底 —— 将来加一个 execution 类的新 source 却忘了写进
    那个 CASE，存量行会被回填成 `advice`，**直接进胜率分母**。影响有界（一次性 +
    `WHERE entry_kind IS NULL` 幂等），但正是本卡在治的那类病，所以照样上门禁。
    """
    from common.decision_kind import ADVICE as _ADV

    path = os.path.join(_BACKEND, "data_engine", "storage", "database.py")
    src = open(path, encoding="utf-8").read()
    non_advice = [s for s in ALL_SOURCES if SOURCES[s]["kinds"] != frozenset({_ADV})]
    missing = [s for s in non_advice if f"'{s}'" not in src]
    assert not missing, (
        f"这些非 advice 的 source 没在 `init_db` 的 entry_kind 归类 SQL 里出现："
        f"{missing} —— 它们的存量行会被 `ELSE 'advice'` 兜进胜率分母。"
    )


# ── 3. 工具 description 覆盖全表 ──────────────────────────────────────────

def test_tool_description_mentions_every_source():
    """description 里的来源清单由 `describe_for_prompt()` 生成 —— 这条防有人改回硬编码。

    ⚠️ 这不是空气测试：LLM 只看 description 决定传什么 source，清单漏一个
    = 那个来源对 LLM 事实上不存在（哪怕 Literal 里有）。
    """
    import inspect

    from agents.tools import decision_tools

    src = inspect.getsource(decision_tools)
    # 取 @tool(...) 里的 description 实参不方便，直接查生成串本身在不在源码里，
    # 再查生成串确实覆盖全表 —— 两步合起来等价于「description 覆盖全表」。
    assert "describe_for_prompt()" in src, (
        "工具 description 不再由 common.decision_source 生成了？"
        "手抄清单正是本卡要修的病。"
    )
    listed = describe_for_prompt()
    for s in ALL_SOURCES:
        assert f"{s}=" in listed, f"来源清单漏了 {s}"
        assert label_of(s) in listed, f"{s} 的中文说明没进清单"


@pytest.mark.parametrize("source", ["crypto", "crypto_earn", "moneybill"])
def test_non_advice_sources_point_somewhere(source):
    """§2.3：这几个 source 里没有建议，必须能指路，否则 LLM 只会说「没有记录」。"""
    assert not has_advice(source)
    assert advice_alternative(source) in SOURCES

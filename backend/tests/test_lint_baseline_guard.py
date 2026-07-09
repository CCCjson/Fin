"""守住「ruff 豁免表只减不增、mypy 强检名单只增不减」这条纪律。

存量豁免表是机器生成的一次性快照（13.0）。往后每迁移一个域，就该删掉它在
per-file-ignores 里的条目。人靠自觉守不住，所以把上限钉在这里：
条目数只许变小。当你合法地缩小了豁免面，把下面的数字一起改小即可。
"""
import pathlib
import re

# 13.0 生成基线时的规模。**只许调小，不许调大。**
BASELINE_FILE_COUNT = 267
BASELINE_CODE_COUNT = 809

# mypy 强检名单的初始成员数。**只许调大，不许调小。**
BASELINE_STRICT_MODULE_COUNT = 1

_PYPROJECT = pathlib.Path(__file__).resolve().parents[1] / "pyproject.toml"


def _parse_per_file_ignores(text: str) -> dict[str, list[str]]:
    """从 pyproject 里抠出 per-file-ignores 段（3.10 无 tomllib，手工解析这一段）。"""
    start = text.index("[tool.ruff.lint.per-file-ignores]")
    rest = text[start:].splitlines()[1:]
    entries: dict[str, list[str]] = {}
    for line in rest:
        line = line.strip()
        if line.startswith("["):        # 下一个 table，结束
            break
        if not line or line.startswith("#"):
            continue
        match = re.match(r'^"([^"]+)"\s*=\s*\[(.*)\]$', line)
        assert match, f"无法解析 per-file-ignores 条目: {line!r}"
        codes = re.findall(r'"([^"]+)"', match.group(2))
        entries[match.group(1)] = codes
    return entries


def _strict_modules(text: str) -> list[str]:
    """强检名单 = 最后一个 [[tool.mypy.overrides]] 里带 disallow_untyped_defs 的那个。"""
    blocks = text.split("[[tool.mypy.overrides]]")[1:]
    for block in blocks:
        if "disallow_untyped_defs = true" in block:
            module_list = re.search(r"module\s*=\s*\[(.*?)\]", block, re.S)
            assert module_list, "强检名单缺 module 列表"
            return re.findall(r'"([^"]+)"', module_list.group(1))
    raise AssertionError("找不到 mypy 强检名单（disallow_untyped_defs = true 的 overrides）")


def test_ruff_ignore_baseline_only_shrinks():
    entries = _parse_per_file_ignores(_PYPROJECT.read_text())
    file_count = len(entries)
    code_count = sum(len(codes) for codes in entries.values())

    assert file_count <= BASELINE_FILE_COUNT, (
        f"ruff 豁免表变大了（{file_count} > {BASELINE_FILE_COUNT}）。"
        "豁免只减不增：新代码不许进表，存量迁移后应删条目。"
    )
    assert code_count <= BASELINE_CODE_COUNT, (
        f"ruff 豁免的规则码数变多了（{code_count} > {BASELINE_CODE_COUNT}）。"
    )


def test_real_bug_codes_never_whitelisted():
    """F821(未定义名)/F811(重复定义)/E722(裸except)/E9(语法错) 是真 bug，永不豁免。"""
    entries = _parse_per_file_ignores(_PYPROJECT.read_text())
    forbidden = {"F821", "F811", "E722", "E712", "E902", "E999"}
    offenders = {
        path: sorted(forbidden & set(codes))
        for path, codes in entries.items()
        if forbidden & set(codes)
    }
    assert not offenders, f"真 bug 类规则码被豁免了: {offenders}"


def test_mypy_strict_list_only_grows():
    modules = _strict_modules(_PYPROJECT.read_text())
    assert len(modules) >= BASELINE_STRICT_MODULE_COUNT, (
        f"mypy 强检名单缩小了（{len(modules)} < {BASELINE_STRICT_MODULE_COUNT}）。"
        "名单只增不减：迁完一个域就把它加进来。"
    )
    assert "common.market" in modules, "common.market 是强检名单的首个成员，不该被移出"

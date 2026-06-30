"""
skills.md 加载器 —— 把 Markdown 操作手册注入 system prompt。

Phase 1：纯文档注入（全工具可见）。
Phase 5：解析 monitor.md 顶部声明的 enabled_tools 做白名单裁剪。
"""
from pathlib import Path
from functools import lru_cache

_SKILLS_DIR = Path(__file__).parent / "skills"


@lru_cache(maxsize=None)
def _read(name: str) -> str:
    path = _SKILLS_DIR / name
    if not path.exists():
        return ""
    return path.read_text(encoding="utf-8")


def load_monitor_system_prompt() -> str:
    """MoneyBill 主编排 agent 的 system prompt。"""
    return _read("monitor.md")


def load_subagent_prompt(name: str) -> str:
    """某 subagent 的领域 system prompt（如 'alpha_lab' → alpha_lab.md）。"""
    return _read(f"{name}.md")

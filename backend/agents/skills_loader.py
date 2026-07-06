"""
skills.md 加载器 —— 把 Markdown 操作手册注入 system prompt。

Phase 1：纯文档注入（全工具可见）。
Phase 5：解析 skill md 顶部声明的 frontmatter（enabled_tools）做工具组白名单，
让 skill 期望的工具组在会话一开始就并入 allowed_tools（见 turn_setup.prepare_turn），
而不是完全依赖模型自己现场调 load_toolgroup。

frontmatter 格式（YAML 风格，但只做极轻量的逐行解析，不引入 PyYAML）：

    ---
    enabled_tools:
      - signals
      - screener
    ---
    # 正文……

解析失败 / 无 frontmatter 一律优雅降级（返回 {} / 原文），
绝不因一个 skill md 格式不对拖垮主流程。
"""
import re
from pathlib import Path
from functools import lru_cache

_SKILLS_DIR = Path(__file__).parent / "skills"

# 顶部 frontmatter 块：文件必须以 --- 开头，到下一行 --- 结束
_FRONTMATTER_RE = re.compile(r"\A---[ \t]*\n(.*?)\n---[ \t]*\n?", re.DOTALL)


@lru_cache(maxsize=None)
def _read(name: str) -> str:
    path = _SKILLS_DIR / name
    if not path.exists():
        return ""
    return path.read_text(encoding="utf-8")


def _split_frontmatter(text: str) -> tuple[dict, str]:
    """把文本拆成 (frontmatter 元数据, 正文)。无 frontmatter 时返回 ({}, 原文)。"""
    m = _FRONTMATTER_RE.match(text)
    if not m:
        return {}, text
    return _parse_frontmatter_block(m.group(1)), text[m.end():]


def _parse_frontmatter_block(block: str) -> dict:
    """逐行解析 frontmatter 块：支持 `key: value` 与 `key:` 后跟 `- item` 列表。"""
    meta: dict = {}
    current_key = None
    for raw in block.splitlines():
        line = raw.rstrip()
        if not line.strip():
            continue
        item = re.match(r"[ \t]*-[ \t]+(.+)$", line)
        if item and current_key and isinstance(meta.get(current_key), list):
            meta[current_key].append(item.group(1).strip())
            continue
        kv = re.match(r"([A-Za-z_][\w-]*)[ \t]*:[ \t]*(.*)$", line)
        if kv:
            key, val = kv.group(1), kv.group(2).strip()
            current_key = key
            if val:
                meta[key] = val          # 内联标量（可能是 [a, b] 形式）
            else:
                meta[key] = []           # 后续用 `- item` 填充的列表
    return meta


def parse_skill_frontmatter(name: str) -> dict:
    """解析某 skill md 顶部的 frontmatter，目前只关心 enabled_tools。

    返回如 {"enabled_tools": ["core", "signals"]}；无 frontmatter 或
    未声明 enabled_tools 或解析出错，一律返回 {}（绝不抛异常拖垮主流程）。
    """
    try:
        meta, _ = _split_frontmatter(_read(name))
        et = meta.get("enabled_tools")
        if et is None:
            return {}
        if isinstance(et, str):
            # 内联写法 `enabled_tools: [a, b]` 或 `enabled_tools: a, b`
            et = [x.strip() for x in et.strip("[]").split(",") if x.strip()]
        if not isinstance(et, list) or not et:
            return {}
        return {"enabled_tools": [str(x) for x in et]}
    except Exception:  # noqa: BLE001 — frontmatter 解析绝不拖垮启动
        return {}


def load_monitor_system_prompt() -> str:
    """MoneyBill 主编排 agent 的 system prompt（去掉顶部 frontmatter，只留正文）。"""
    text = _read("monitor.md")
    try:
        _, body = _split_frontmatter(text)
        return body
    except Exception:  # noqa: BLE001 — 降级为原文，绝不因解析失败起不来
        return text


def load_subagent_prompt(name: str) -> str:
    """某 subagent 的领域 system prompt（如 'alpha_lab' → alpha_lab.md）。"""
    text = _read(f"{name}.md")
    try:
        _, body = _split_frontmatter(text)
        return body
    except Exception:  # noqa: BLE001
        return text

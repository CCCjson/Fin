"""采集深任务的真实输出样本，作为重构前后的人工 diff 基准。

为什么不做成 pytest：LLM 出稿不确定，没法写断言。13.1 的门禁只锁**结构与契约**
（见 tests/baseline/），**内容质量**靠这里落盘的样本，迁移后再跑一次、人工对比。

用法（会真打 LLM，花钱；需后端依赖就绪）：
    conda run -n quant python tests/baseline/capture_samples.py            # 全部七个
    conda run -n quant python tests/baseline/capture_samples.py --only report_market
    conda run -n quant python tests/baseline/capture_samples.py --tag after_graph_migration

产物落到 scripts/data/baseline_samples/<tag>_<shorthash>/，该目录是外置盘符号链接，
不在源码树里。每个任务一个 .md（正文）+ 一个 .json（事件统计与耗时）。

迁移后对比：
    # 逐章 diff：把旧整份报告按 '## N.' 切段，与新章节样本逐段对照
    #   python tests/baseline/split_report_chapters.py <before>/report.md
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
from collections import Counter
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any

_BACKEND = Path(__file__).resolve().parents[2]
if str(_BACKEND) not in sys.path:
    sys.path.insert(0, str(_BACKEND))

OUT_ROOT = _BACKEND / "scripts" / "data" / "baseline_samples"


def _git_short_hash() -> str:
    try:
        out = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],
            cwd=_BACKEND, capture_output=True, text=True, timeout=5, check=True,
        )
        return out.stdout.strip()
    except (subprocess.SubprocessError, OSError):
        return "nogit"


def _drain(stream: Iterator[str]) -> tuple[str, Counter, dict[str, Any]]:
    """把 subagent 的 NDJSON 流排空，攒出正文、事件计数、最终 envelope。"""
    body: list[str] = []
    events: Counter = Counter()
    envelope: dict[str, Any] = {}

    for line in stream:
        line = (line or "").strip()
        if not line:
            continue
        try:
            ev = json.loads(line)
        except json.JSONDecodeError:
            continue
        name = ev.get("event", "?")
        events[name] += 1
        if name == "chunk":
            body.append(ev.get("content", ""))
        elif name == "subagent_done":
            envelope = ev.get("result", {})

    return "".join(body), events, envelope


# ── 三个深任务的入口。args 挑的是「有代表性且不太贵」的参数。────────────────

def _run_section(section: str) -> Callable[[], Iterator[str]]:
    """13.2 把全量报告拆成五个章节工具，每章各出一个样本，好逐章 diff。"""
    def _run() -> Iterator[str]:
        from agents.subagents import get_runner
        return get_runner(f"report_{section}").run({"report_type": "weekly"})
    return _run


def _run_deep_stock() -> Iterator[str]:
    from agents.subagents.deep_stock import DeepStockSubagent
    return DeepStockSubagent().run({"symbol": "600519.SH"})


def _run_alpha() -> Iterator[str]:
    from agents.subagents.alpha_lab import AlphaLabSubagent
    # 只跑 2 轮迭代：样本是用来比「形状与质量」的，不是比谁挖得深
    return AlphaLabSubagent().run({"symbols": ["600519.SH"], "max_iterations": 2})


TASKS: dict[str, Callable[[], Iterator[str]]] = {
    **{f"report_{sec}": _run_section(sec)
       for sec in ("market", "news", "positions", "strategy", "picks")},
    "deep_stock": _run_deep_stock,
    "alpha": _run_alpha,
}


def capture(name: str, out_dir: Path) -> dict[str, Any]:
    print(f"\n▶ {name} …", flush=True)
    started = time.monotonic()
    try:
        body, events, envelope = _drain(TASKS[name]())
        error = None
    except Exception as exc:  # 采集脚本：一个任务炸了不该带走其余两个
        body, events, envelope = "", Counter(), {}
        error = f"{type(exc).__name__}: {exc}"
        print(f"  ✗ {error}", flush=True)

    elapsed = round(time.monotonic() - started, 1)
    # alpha_lab 不吐 markdown chunk，它的产出全在 envelope 的 message/widgets 里；
    # 落盘时把 envelope 的正文也拼进 .md，否则该任务的样本是空的、没法 diff。
    document = body or envelope.get("message", "")
    (out_dir / f"{name}.md").write_text(document or "(无正文)")

    meta = {
        "task": name,
        "elapsed_s": elapsed,
        "body_chars": len(body),
        "document_chars": len(document),
        "events": dict(events),
        "envelope": envelope,       # 完整存档：迁移后逐字段 diff 的依据
        "error": error,
    }
    (out_dir / f"{name}.json").write_text(json.dumps(meta, ensure_ascii=False, indent=1))

    if not error:
        print(f"  ✓ {len(document)} 字 / {elapsed}s / ok={envelope.get('ok')}", flush=True)
    return meta


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--tag", default="before_step13", help="样本目录前缀")
    parser.add_argument("--only", choices=sorted(TASKS), help="只跑某一个任务")
    args = parser.parse_args()

    names = [args.only] if args.only else list(TASKS)
    out_dir = OUT_ROOT / f"{args.tag}_{_git_short_hash()}"
    out_dir.mkdir(parents=True, exist_ok=True)

    print(f"样本目录: {out_dir}")
    metas = [capture(name, out_dir) for name in names]

    (out_dir / "summary.json").write_text(json.dumps({
        "tag": args.tag,
        "git": _git_short_hash(),
        "tasks": metas,
    }, ensure_ascii=False, indent=1))

    failed = [m["task"] for m in metas if m["error"]]
    print(f"\n完成。{len(metas) - len(failed)}/{len(metas)} 成功 → {out_dir}")
    if failed:
        print(f"失败: {', '.join(failed)}")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())

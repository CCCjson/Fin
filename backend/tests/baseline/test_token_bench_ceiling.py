"""13.1 基线：每轮对话的 token 地板不许被重构抬回去。

token 优化（工具分组 load_toolgroup + 历史压缩 + prompt 瘦身）把每轮地板从
9.7K 压到 4.4K。第 13 步重构（尤其是把深任务搬上 LangGraph）很容易在不知不觉中
把 system prompt / 工具 schema 重新灌回每一轮。这条守着那个成果。

用法（需后端跑在 :8000）：
    conda run -n quant python scripts/token_bench.py --tag <你的标签>
    conda run -n quant pytest -m integration tests/baseline/test_token_bench_ceiling.py

标 integration：只读 jsonl，不自己打 LLM（那太慢也费钱）。没有基线记录 → skip。
"""
import json
import pathlib

import pytest

pytestmark = [pytest.mark.baseline, pytest.mark.integration]

RESULTS = pathlib.Path(__file__).resolve().parents[2] / "scripts" / "data" / "token_bench_results.jsonl"

BASELINE_TAG = "before_step13"
# 允许 5% 的自然波动（模型返回长度、检索命中数会小幅影响 prompt 累积）
TOLERANCE = 1.05


def _load_runs() -> list[dict]:
    if not RESULTS.exists():
        pytest.skip(f"没有 token_bench 记录: {RESULTS}")
    runs = [json.loads(line) for line in RESULTS.read_text().splitlines() if line.strip()]
    if not runs:
        pytest.skip("token_bench 记录为空")
    return runs


def _by_tag(runs: list[dict], tag: str) -> dict | None:
    matches = [r for r in runs if r.get("tag") == tag]
    return matches[-1] if matches else None


def test_baseline_run_exists():
    """重构开工前必须录一条基线，否则后面没有对比基准。"""
    baseline = _by_tag(_load_runs(), BASELINE_TAG)
    assert baseline is not None, (
        f"缺少 tag={BASELINE_TAG} 的基线记录。先跑："
        f"conda run -n quant python scripts/token_bench.py --tag {BASELINE_TAG}"
    )
    assert baseline["total_prompt_tokens"] > 0


def test_latest_run_does_not_exceed_baseline_ceiling():
    """最近一次跑分不许超过基线 ×1.05。超了就是重构把 token 灌回去了。"""
    runs = _load_runs()
    baseline = _by_tag(runs, BASELINE_TAG)
    if baseline is None:
        pytest.skip(f"没有 {BASELINE_TAG} 基线")

    latest = runs[-1]
    if latest.get("tag") == BASELINE_TAG:
        pytest.skip("最近一次就是基线本身，无可对比")

    ceiling = baseline["total_prompt_tokens"] * TOLERANCE
    assert latest["total_prompt_tokens"] <= ceiling, (
        f"token 地板被抬高了：{latest['tag']} 用了 {latest['total_prompt_tokens']}，"
        f"基线 {baseline['total_prompt_tokens']} × {TOLERANCE} = {ceiling:.0f}"
    )


def test_per_scenario_ceiling():
    """逐场景对比 —— 总量可能被某个场景的下降掩盖住另一个的暴涨。"""
    runs = _load_runs()
    baseline = _by_tag(runs, BASELINE_TAG)
    latest = runs[-1]
    if baseline is None or latest.get("tag") == BASELINE_TAG:
        pytest.skip("无可对比的运行")

    base_by_scenario = {r["scenario"]: r for r in baseline["results"]}
    regressions = []
    for row in latest["results"]:
        base = base_by_scenario.get(row["scenario"])
        if not base:
            continue
        if row["prompt_tokens"] > base["prompt_tokens"] * TOLERANCE:
            regressions.append(
                f"{row['scenario']}: {row['prompt_tokens']} > {base['prompt_tokens']}×{TOLERANCE}"
            )
    assert not regressions, "这些场景的 token 涨了:\n" + "\n".join(regressions)


def test_baseline_scenarios_cover_the_main_paths():
    """基线得覆盖主要链路，否则守不住什么。"""
    baseline = _by_tag(_load_runs(), BASELINE_TAG)
    if baseline is None:
        pytest.skip(f"没有 {BASELINE_TAG} 基线")

    scenarios = {r["scenario"] for r in baseline["results"]}
    assert {"recommend", "market_pulse", "positions", "backtest", "knowledge"} <= scenarios

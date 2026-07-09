"""
MoneyBill token 基准测量 —— 回放固定场景，统计每场景 prompt/completion tokens。

用法（后端需已在 8000 端口运行）：
    conda run -n quant python backend/scripts/token_bench.py
    conda run -n quant python backend/scripts/token_bench.py --tag after_step3

结果追加写入 backend/scripts/data/token_bench_results.jsonl，方便前后对比。
"""
import argparse
import json
import os
import time
from datetime import datetime
from pathlib import Path

import requests
from dotenv import load_dotenv

load_dotenv(Path(__file__).parent.parent / ".env")

BASE = "http://127.0.0.1:8000"
OUT = Path(__file__).parent / "data" / "token_bench_results.jsonl"


def _auth_headers() -> dict:
    """全局 JWT 鉴权落地后（Phase 3），/agent/chat 需要 Bearer token。
    用 .env 里的 AUTH_USERNAME/AUTH_PASSWORD 登录换一个。"""
    user = os.getenv("AUTH_USERNAME", "")
    pwd = os.getenv("AUTH_PASSWORD", "")
    resp = requests.post(f"{BASE}/auth/login", json={"username": user, "password": pwd}, timeout=10)
    resp.raise_for_status()
    token = resp.json()["token"]
    return {"Authorization": f"Bearer {token}"}

# 固定 5 场景（覆盖推荐/大盘/持仓/回测/知识问答），各自独立新会话
SCENARIOS = [
    ("recommend", "今天有什么值得买的股票吗？"),
    ("market_pulse", "现在盘面怎么样，情绪如何？"),
    ("positions", "看下我现在的持仓和盈亏"),
    ("backtest", "帮我用双均线策略回测一下贵州茅台近一年的表现"),
    ("knowledge", "动量因子在A股有效吗？知识库里有什么研究？"),
]

# 模拟前端页面上下文（约 1KB 可见文本，接近真实使用）
PAGE_CONTEXT = {
    "page": "仪表盘",
    "path": "/",
    "entities": {},
    "visible_text": "上证指数 3,450.12 +0.35%  深证成指 11,200.55 -0.12%  " * 20,
}


def run_scenario(name: str, message: str, headers: dict) -> dict:
    """跑一个场景（新会话），汇总该回合所有轮次的 usage。"""
    prompt_total = 0
    completion_total = 0
    rounds = 0
    t0 = time.time()
    resp = requests.post(
        f"{BASE}/agent/chat",
        json={"message": message, "page_context": PAGE_CONTEXT},
        headers=headers,
        stream=True, timeout=300,
    )
    resp.raise_for_status()
    for line in resp.iter_lines(decode_unicode=True):
        if not line:
            continue
        try:
            ev = json.loads(line)
        except json.JSONDecodeError:
            continue
        if ev.get("event") == "usage":
            turn = ev.get("turn") or {}
            # usage 事件每轮都发且是回合累计值，取最后一次即回合总量
            prompt_total = turn.get("prompt_tokens", 0)
            completion_total = turn.get("completion_tokens", 0)
            rounds += 1
        elif ev.get("event") == "await_confirm":
            break  # 下单确认场景不继续，已有用量足够对比
    return {
        "scenario": name,
        "prompt_tokens": prompt_total,
        "completion_tokens": completion_total,
        "llm_rounds": rounds,
        "elapsed_s": round(time.time() - t0, 1),
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--tag", default="baseline", help="本次测量标签，如 after_step3")
    args = parser.parse_args()

    headers = _auth_headers()
    results = []
    for name, msg in SCENARIOS:
        print(f"▶ {name}: {msg}")
        try:
            r = run_scenario(name, msg, headers)
        except Exception as e:  # noqa: BLE001
            r = {"scenario": name, "error": str(e)}
        print(f"  {r}")
        results.append(r)

    total_prompt = sum(r.get("prompt_tokens", 0) for r in results)
    record = {
        "tag": args.tag,
        "ts": datetime.now().isoformat(timespec="seconds"),
        "total_prompt_tokens": total_prompt,
        "results": results,
    }
    OUT.parent.mkdir(parents=True, exist_ok=True)
    with OUT.open("a", encoding="utf-8") as f:
        f.write(json.dumps(record, ensure_ascii=False) + "\n")
    print(f"\n合计 prompt tokens: {total_prompt}（tag={args.tag}，已写入 {OUT}）")


if __name__ == "__main__":
    main()

"""
Token 用量 & 成本追踪 —— 累计 OpenAI 接口返回的 usage，按模型单价折算美元。

单价可用环境变量覆盖（每百万 token 美元）：
    AGENT_PRICE__<model>=<input_per_1M>,<output_per_1M>
例：AGENT_PRICE__gpt-5.5=1.25,10   AGENT_PRICE__gpt-5.4-mini=0.25,2
未配置则用 DEFAULT_PRICES；都没有则按 fallback 估算。
⚠️ 默认单价是占位值，请按你实际的计费标准设置环境变量后才准确。
"""
import os
import json
import threading
from datetime import datetime
from pathlib import Path

# 当天用量持久化文件（跨重启累计；按本地日期分桶）
_DAILY_FILE = Path(__file__).parent.parent / "data" / "usage_daily.json"


def _today_key() -> str:
    return datetime.now().strftime("%Y-%m-%d")


# (input_per_1M_usd, output_per_1M_usd) —— 实际官方单价（可用 env 覆盖）
DEFAULT_PRICES: dict[str, tuple[float, float]] = {
    "gpt-5.5": (5.00, 30.00),
    "gpt-5.4-mini": (0.75, 4.50),
}
_FALLBACK = (5.00, 30.00)


def get_price(model: str) -> tuple[float, float]:
    env = os.getenv(f"AGENT_PRICE__{model}")
    if env:
        try:
            i, o = env.split(",")
            return float(i), float(o)
        except ValueError:
            pass
    return DEFAULT_PRICES.get(model, _FALLBACK)


def cost_usd(model: str, prompt_tokens: int, completion_tokens: int) -> float:
    pin, pout = get_price(model)
    return prompt_tokens / 1_000_000 * pin + completion_tokens / 1_000_000 * pout


class UsageTracker:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self.prompt_tokens = 0
        self.completion_tokens = 0
        self.cost = 0.0
        self.calls = 0
        self.by_model: dict[str, dict] = {}
        # 当天用量（按日期分桶，跨进程重启累计）：{date: {...}}
        self.daily: dict[str, dict] = self._load_daily()

    @staticmethod
    def _load_daily() -> dict:
        try:
            if _DAILY_FILE.exists():
                return json.loads(_DAILY_FILE.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            pass
        return {}

    def _persist_daily(self) -> None:
        """把当天用量落盘（调用方需已持锁）。失败不影响主流程。"""
        try:
            _DAILY_FILE.parent.mkdir(parents=True, exist_ok=True)
            _DAILY_FILE.write_text(
                json.dumps(self.daily, ensure_ascii=False), encoding="utf-8")
        except OSError:
            pass

    def _bump_daily(self, prompt_tokens: int, completion_tokens: int, cost: float) -> None:
        """累加到今天的桶并落盘（调用方需已持锁）。"""
        key = _today_key()
        d = self.daily.setdefault(
            key, {"prompt_tokens": 0, "completion_tokens": 0, "cost": 0.0, "calls": 0})
        d["prompt_tokens"] += prompt_tokens
        d["completion_tokens"] += completion_tokens
        d["cost"] += cost
        d["calls"] += 1
        self._persist_daily()

    def record(self, model: str, prompt_tokens: int, completion_tokens: int) -> float:
        c = cost_usd(model, prompt_tokens, completion_tokens)
        with self._lock:
            self.prompt_tokens += prompt_tokens
            self.completion_tokens += completion_tokens
            self.cost += c
            self.calls += 1
            m = self.by_model.setdefault(
                model, {"prompt_tokens": 0, "completion_tokens": 0, "cost": 0.0, "calls": 0})
            m["prompt_tokens"] += prompt_tokens
            m["completion_tokens"] += completion_tokens
            m["cost"] += c
            m["calls"] += 1
            self._bump_daily(prompt_tokens, completion_tokens, c)
        return c

    def record_total(self, model: str, total_tokens: int) -> float:
        """只知道总 token（subagent 内部引擎只暴露 total）时，按 50/50 估算 prompt/completion。"""
        if not total_tokens:
            return 0.0
        half = total_tokens // 2
        return self.record(model, half, total_tokens - half)

    def record_cost(self, label: str, cost: float, total_tokens: int = 0) -> None:
        """已知美元成本（如 alpha_lab 自带 total_cost_usd）时直接累加。"""
        with self._lock:
            self.cost += cost
            self.calls += 1
            self.prompt_tokens += total_tokens
            m = self.by_model.setdefault(
                label, {"prompt_tokens": 0, "completion_tokens": 0, "cost": 0.0, "calls": 0})
            m["cost"] += cost
            m["calls"] += 1
            m["prompt_tokens"] += total_tokens
            self._bump_daily(total_tokens, 0, cost)

    def snapshot(self) -> dict:
        with self._lock:
            day = self.daily.get(_today_key(), {})
            d_prompt = day.get("prompt_tokens", 0)
            d_completion = day.get("completion_tokens", 0)
            return {
                "prompt_tokens": self.prompt_tokens,
                "completion_tokens": self.completion_tokens,
                "total_tokens": self.prompt_tokens + self.completion_tokens,
                "cost_usd": round(self.cost, 6),
                "calls": self.calls,
                "by_model": {
                    k: {**v, "cost": round(v["cost"], 6)}
                    for k, v in self.by_model.items()
                },
                "today": {
                    "date": _today_key(),
                    "prompt_tokens": d_prompt,
                    "completion_tokens": d_completion,
                    "total_tokens": d_prompt + d_completion,
                    "cost_usd": round(day.get("cost", 0.0), 6),
                    "calls": day.get("calls", 0),
                },
            }


USAGE = UsageTracker()

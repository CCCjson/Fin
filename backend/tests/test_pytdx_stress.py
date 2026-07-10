"""
pytdx 盘中扫描暴力压测 — 180 轮 × 10 并发

测试项：
1. 连接稳定性（多线程并发连接 + 断连自动重连）
2. 数据一致性（每轮都能拿到 20 只数据？）
3. 单轮耗时 & 总耗时
4. 内存占用 & 泄漏检测
5. 异常统计

用法：
  cd ~/Desktop/Fin/backend
  conda activate quant
  python tests/test_pytdx_stress.py
"""

import pytest

pytestmark = pytest.mark.network
import sys
import os

_backend_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if _backend_dir not in sys.path:
    sys.path.insert(0, _backend_dir)

import time
import traceback
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed

import psutil
from loguru import logger

# ── 配置 ──────────────────────────────────────────
WATCHLIST = [
    "600519.SH",  # 贵州茅台
    "000858.SZ",  # 五粮液
    "601318.SH",  # 中国平安
    "000333.SZ",  # 美的集团
    "600036.SH",  # 招商银行
    "000001.SZ",  # 平安银行
    "600276.SH",  # 恒瑞医药
    "002594.SZ",  # 比亚迪
    "601899.SH",  # 紫金矿业
    "600900.SH",  # 长江电力
    "000725.SZ",  # 京东方A
    "601012.SH",  # 隆基绿能
    "002475.SZ",  # 立讯精密
    "600030.SH",  # 中信证券
    "000002.SZ",  # 万科A
    "601166.SH",  # 兴业银行
    "002415.SZ",  # 海康威视
    "600887.SH",  # 伊利股份
    "000568.SZ",  # 泸州老窖
    "601688.SH",  # 华泰证券
]

TOTAL_ROUNDS = 180
WORKERS = 10  # 并发线程数
# ──────────────────────────────────────────────────

_lock = threading.Lock()


def get_mem_mb():
    return psutil.Process(os.getpid()).memory_info().rss / 1024 / 1024


def run_one_round(round_id: int) -> dict:
    """单轮扫描（每个线程独立创建 SignalGenerator + PytdxFetcher）"""
    from strategy.signal_generator import SignalGenerator

    generator = SignalGenerator()
    t0 = time.time()

    try:
        result = generator.scan_intraday(
            symbols=WATCHLIST,
            bar_count=240,
            period=1,
            save_to_db=False,
        )
        elapsed = time.time() - t0

        details = result.get("results", {})
        got_data = sum(1 for v in details.values() if not v.get("error"))
        errors = [s for s, v in details.items() if v.get("error")]

        return {
            "round": round_id,
            "ok": True,
            "elapsed": elapsed,
            "total_signals": result.get("total_signals", 0),
            "buy": result.get("buy_signals", 0),
            "sell": result.get("sell_signals", 0),
            "got_data": got_data,
            "symbol_errors": errors,
        }

    except Exception as e:
        elapsed = time.time() - t0
        return {
            "round": round_id,
            "ok": False,
            "elapsed": elapsed,
            "error": f"{type(e).__name__}: {e}",
        }


def run_stress_test():
    mem_start = get_mem_mb()

    # 先预热，把 40s 测速在压测前一次性搞完
    from acquisition.markets.pytdx_fetcher import warmup_pytdx
    warmup_pytdx()

    logger.info(
        f"=== 暴力压测开始: {TOTAL_ROUNDS} 轮, {WORKERS} 并发, "
        f"{len(WATCHLIST)} 只股票 ==="
    )
    logger.info(f"起始内存: {mem_start:.1f} MB")

    success = 0
    fail = 0
    total_signals = 0
    total_buy = 0
    total_sell = 0
    round_times = []
    data_counts = []
    errors = []

    test_start = time.time()
    completed = 0

    with ThreadPoolExecutor(max_workers=WORKERS) as pool:
        futures = {
            pool.submit(run_one_round, i): i
            for i in range(1, TOTAL_ROUNDS + 1)
        }

        for future in as_completed(futures):
            r = future.result()
            completed += 1
            round_times.append(r["elapsed"])

            if r["ok"]:
                success += 1
                total_signals += r["total_signals"]
                total_buy += r["buy"]
                total_sell += r["sell"]
                data_counts.append(r["got_data"])

                mem_now = get_mem_mb()
                logger.info(
                    f"[{completed}/{TOTAL_ROUNDS}] R{r['round']:>3} OK | "
                    f"{r['elapsed']:.1f}s | "
                    f"信号 {r['total_signals']}(B{r['buy']}/S{r['sell']}) | "
                    f"数据 {r['got_data']}/{len(WATCHLIST)} | "
                    f"内存 {mem_now:.0f}MB"
                )
                if r["symbol_errors"]:
                    logger.warning(f"  出错股票: {r['symbol_errors']}")
            else:
                fail += 1
                errors.append(f"R{r['round']}: {r.get('error', '?')}")
                logger.error(
                    f"[{completed}/{TOTAL_ROUNDS}] R{r['round']:>3} FAIL | "
                    f"{r['elapsed']:.1f}s | {r.get('error')}"
                )

    total_elapsed = time.time() - test_start
    mem_end = get_mem_mb()

    avg_t = sum(round_times) / len(round_times) if round_times else 0
    min_t = min(round_times) if round_times else 0
    max_t = max(round_times) if round_times else 0
    p50 = sorted(round_times)[len(round_times) // 2] if round_times else 0
    p95 = sorted(round_times)[int(len(round_times) * 0.95)] if round_times else 0
    avg_data = sum(data_counts) / len(data_counts) if data_counts else 0

    report = f"""
╔════════════════════════════════════════════════════════════╗
║              pytdx 盘中扫描暴力压测报告                   ║
╠════════════════════════════════════════════════════════════╣
║  总轮数: {TOTAL_ROUNDS:>5}   并发: {WORKERS:>3}   股票: {len(WATCHLIST):>3} 只              ║
║  成功: {success:>5}   失败: {fail:>5}   成功率: {success/TOTAL_ROUNDS*100:.1f}%              ║
╠────────────────────────────────────────────────────────────╣
║  总耗时: {total_elapsed:.1f}s  ({total_elapsed/60:.1f} 分钟)                          ║
║  单轮耗时:  avg {avg_t:.1f}s | min {min_t:.1f}s | p50 {p50:.1f}s | p95 {p95:.1f}s | max {max_t:.1f}s  ║
╠────────────────────────────────────────────────────────────╣
║  累计信号: {total_signals:>6}  (买入 {total_buy}, 卖出 {total_sell})                  ║
║  平均数据获取: {avg_data:.1f}/{len(WATCHLIST)} 只/轮                               ║
╠────────────────────────────────────────────────────────────╣
║  内存: {mem_start:.0f}MB → {mem_end:.0f}MB  (Δ{mem_end - mem_start:+.0f}MB)                          ║
╚════════════════════════════════════════════════════════════╝
"""
    if errors:
        report += f"\n异常汇总 ({len(errors)} 条, 显示前 10):\n"
        for e in errors[:10]:
            report += f"  - {e}\n"

    print(report)
    logger.info(report)


if __name__ == "__main__":
    run_stress_test()

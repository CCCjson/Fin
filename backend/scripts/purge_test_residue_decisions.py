"""一次性清理：把测试写进生产库的 41 行假成交从 `decision_logs` 里删掉（P0-4）。

# 这些行是什么

    BTCUSDT.BN | BUY | entry_price=100.0 | 币安现货成交 order=BTCUSDT.BN:999

`BTCUSDT.BN:999` 这个 order_id **只存在于** `tests/test_crypto_strategy_engine.py`
的 `_FakeOrder`，配 100.0 的成交价、0.5 的数量 —— 是 `test_confirm_executes_and_records`
那条测试跑出来的。真实币安订单号是纯数字（如 `64822196610`），撞不上这个指纹。

泄漏机制与结构性堵法见 `tests/conftest.py` 文首（已修：测试期 engine 换成临时库，
跑完全套后生产库行数不变，实测 99 → 99）。

# 为什么不是「标一下就算了」

P0-4 的 `entry_kind='execution'` 已经让它们不再进评估管道（炸弹拆了）。但它们仍是
**假数据**：占全表 41%，会让「crypto 是 decision_logs 最大来源（54%）」这类结论
一直失真 —— 那个数字本身就是污染的产物，真实占比是 22%。Jason 2026-07-27 拍板：删，
不备份。

# 幂等

按指纹删，删完再跑就是 0 行。
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sqlalchemy import text  # noqa: E402

from data_engine.storage.database import engine  # noqa: E402

_FINGERPRINT = "%BTCUSDT.BN:999%"


def main() -> int:
    with engine.begin() as conn:
        total = conn.execute(text("SELECT count(*) FROM decision_logs")).scalar()
        hits = conn.execute(
            text("SELECT count(*) FROM decision_logs WHERE output_text LIKE :fp"),
            {"fp": _FINGERPRINT},
        ).scalar()
        # 保险：真实订单一律是纯数字 order_id，指纹只该命中 source='crypto' 的回执。
        strays = conn.execute(
            text("SELECT count(*) FROM decision_logs "
                 "WHERE output_text LIKE :fp AND source <> 'crypto'"),
            {"fp": _FINGERPRINT},
        ).scalar()
        if strays:
            print(f"✗ 指纹命中了 {strays} 行非 crypto 的记录，疑似误伤，已中止")
            return 1
        deleted = conn.execute(
            text("DELETE FROM decision_logs WHERE output_text LIKE :fp"),
            {"fp": _FINGERPRINT},
        ).rowcount
        left = conn.execute(text("SELECT count(*) FROM decision_logs")).scalar()

    print(f"decision_logs: {total} 行 → 指纹命中 {hits} → 已删 {deleted} → 剩 {left}")
    for row in engine.connect().execute(text(
            "SELECT entry_kind, count(*) FROM decision_logs GROUP BY entry_kind")):
        print(f"  {row[0] or '(NULL)'}: {row[1]}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

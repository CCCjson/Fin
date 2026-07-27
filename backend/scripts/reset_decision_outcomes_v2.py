"""一次性重刷：清空 `decision_logs` 的后验评估列，让它们全部按 **v2 口径**重评（P0-4 批次2）。

干两件事，都是 2026-07-27 勘察批次2 时发现的（卡片上原本没写）：

# 1. 非 advice 行挂着批次1 之前留下的评估残留

批次1 把 `entry_kind` 三分之后，订单回执和非交易操作就退出了回填候选集。但批次1
**之前**它们已经被评过一轮，戳还留在库里：

    execution / unable / insufficient_bars   6 行
    execution / unable / no_entry_price      1 行   （都带 engine_version=v1）

这些行**永远不会再被回填扫到** → 戳会永久挂着骗人：谁翻表都会以为订单回执也在被
评估。它们是残留，不是事实。

# 2. bump v2 之后会出现版本混杂，而现在是唯一一次「重刷零成本」的窗口

`ENGINE_VERSION` 从 v1 bump 到 v2（新增离谱入场价守卫）之后：

  - pending 与可重试 unable 的行，下次回填自动重评、自动盖上 v2；
  - 但**不可重试**的 unable（`action_not_directional` / `no_action` / `no_entry_price`）
    再也不会被扫到 → 永远停在 v1，`get_decision_stats` 的 `engine_version` 从此
    长期返 `["decision-outcome-v1", "decision-outcome-v2"]`。

关键事实：**此刻 `completed` = 0** —— 没有任何一条胜负结论被版本戳背书过。现在重刷
代价为零；等攒出 completed 行再想统一，就得连「已经进过 P0-3 校准的历史结果」一起洗。

# 做法

把 `decision_log._OUTCOME_WRITE_FIELDS`（回填唯一允许写的那 12 列）在**全表**打回
NULL，然后跑一次 `backfill_outcomes()`：

  - advice 行 → `outcome_status IS NULL` 落进候选集，按 v2 重评、重新盖戳；
  - execution / ops 行 → 永不进候选集，就此保持干净的 NULL。

**复用 `_OUTCOME_WRITE_FIELDS` 而不是手抄一遍列名**：以后 DecisionLog 加了新的
outcome 列，这个脚本自动跟上，不会漏。

# 安全闸

若库里已存在 `completed` 行则**中止**（要 `--force` 才继续）。理由：completed 行的
bars 若已被清理/不可得，重评会把一条真实胜负结论降级成 `unable/no_quotes` —— 那是
真的丢数据。今天 completed=0，这道闸只是防将来有人手滑重跑。

# 幂等

清空 + 重评。重复跑得到同样的结果（同一批 bar 评出同一批结论），只是白跑一趟。
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sqlalchemy import text  # noqa: E402

from data_engine.storage.database import engine  # noqa: E402
from decision_log import _OUTCOME_WRITE_FIELDS, backfill_outcomes  # noqa: E402


def _dump(conn, title: str) -> None:
    print(f"\n--- {title} ---")
    rows = conn.execute(text(
        "SELECT entry_kind, outcome_status, engine_version, count(*) "
        "FROM decision_logs GROUP BY 1, 2, 3 ORDER BY 1, 2, 3"
    )).fetchall()
    for kind, status, ver, n in rows:
        print(f"  {kind or '(NULL)':<10} {status or '(未评)':<10} {ver or '(无戳)':<22} {n}")


def main() -> int:
    force = "--force" in sys.argv

    with engine.begin() as conn:
        _dump(conn, "重刷前")

        completed = conn.execute(text(
            "SELECT count(*) FROM decision_logs WHERE outcome_status = 'completed'"
        )).scalar() or 0
        if completed and not force:
            print(
                f"\n✗ 库里已有 {completed} 行 completed —— 重刷会把真实胜负结论推倒重算，"
                f"而它们的 bars 未必还在（重评可能降级成 unable/no_quotes = 真丢数据）。\n"
                f"  想清楚了再加 --force。"
            )
            return 1

        # 白名单来自 decision_log._OUTCOME_WRITE_FIELDS —— 与回填唯一允许写的列同源，
        # 保证「清掉的」正好等于「回填会重写的」，一列不多一列不少。
        cols = sorted(_OUTCOME_WRITE_FIELDS)
        assignments = ", ".join(f"{c} = NULL" for c in cols)
        cleared = conn.execute(text(f"UPDATE decision_logs SET {assignments}")).rowcount
        print(f"\n✓ 已清空 {cleared} 行的后验评估列（{len(cols)} 列）")

    stats = backfill_outcomes()
    print(
        f"\n✓ 按 v2 口径重评完成: 共 {stats['total']} 条 → "
        f"已评 {stats['completed']} / 待评 {stats['pending']} / "
        f"没法评 {stats['unable']} / 出错 {stats['errors']}"
    )

    with engine.connect() as conn:
        _dump(conn, "重刷后")
        strays = conn.execute(text(
            "SELECT count(*) FROM decision_logs "
            "WHERE entry_kind IN ('execution', 'ops') AND outcome_status IS NOT NULL"
        )).scalar() or 0
        if strays:
            print(f"\n✗ 仍有 {strays} 行非 advice 带着评估戳 —— 回填候选集漏了 entry_kind 过滤？")
            return 1
        print("\n✓ 非 advice 行的评估残留已清零")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

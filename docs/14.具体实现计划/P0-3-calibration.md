---
id: P0-3
title: 置信度校准反哺
size: 小
depends: P0-1（没有后验结果就无从校准）
paths_verified: 2026-07-17
---

# P0-3 置信度校准反哺

> ## ✅ 已完工（2026-07-20，commit 见 P0 闭环收官）—— 下方是原始计划，实施偏离见本框
>
> **三条验收标准达成**（真库冒烟：cockpit 当前 0 条已评 → 校准惰性 factor=1.0、老行为不变；
> 单测覆盖 ≥30 样本时按命中率打折）。代码承载物：
> - 内核 `decision_log.compute_calibration(source, window=50, horizon)` + `get_calibration_factor`（带 TTL 缓存）
> - `cockpit_engine/scorer.py` 加 `calibration_factor` 参数（纯函数，因子由调用方传入），SCORER_VERSION v2→**v3**
> - `cockpit_engine/aggregator.py` 取因子并传入 score_cockpit
> - `agents/tools/analysis_tools.py` 留痕 `calibration_factor`；`agents/tools/decision_tools.py` 的 `get_decision_history` 暴露 `calibration`（分桶表 + 因子）
> - 门禁：`tests/test_decision_calibration.py` + `tests/agents/test_hard_clamp.py`（校准×硬钳顺序）
>
> ### 实施时对本卡的七处偏离（都已确认，别改回去）
>
> 1. **校准点 = cockpit scorer，不是 orchestrator（本卡落点表写错了）**。勘察实锤：MoneyBill
>    对话输出的「置信度：高」是**自由文本、无法解析回数值**（quality_guard.py:19 有同样自白）。
>    真正被产出/钳制/留痕的结构化 confidence 是 **cockpit composite**（scorer.py）。所以校准落在
>    scorer（aggregator 传因子进去），不在 orchestrator 收尾 / policy_checks。
> 2. **只下调不上抬**：`factor = min(1.0, max(0.5, 0.5 + 命中率))`，封在 1.0。蓝本
>    `aggregator.py` 的 `0.5 + win_rate` 可 >1（那是给「技能权重」用的）；**置信度校准只许打折**
>    —— 上抬 = 把一段走运连胜当成本事，危险。命中率 >=50% → 1.0 不动；<50% → 按比例打折，floor 0.5。
> 3. **校准在硬钳之前**：`raw → calibrate → clamp → record`。硬钳（数据降级封顶 60）仍是最后安全网，
>    校准怎么算都绕不过它。门禁 `test_calibration_then_clamp_both_apply` 钉住顺序。
> 4. **`raw_composite` 语义扩了**：现在是「校准前 + 钳前」的原始模型分（P0-2 时只是钳前）。审计
>    「历史打了几折」看 `output_summary.calibration_factor` + `adjustments` 里的 `confidence_calibrated_by_history`。
> 5. **`window=50` 是新语义**：`get_decision_stats` 刻意不接 limit（胜率是全量事实），compute_calibration
>    自己按 `created_at desc` 截最近 50 条**已评**决策。命中口径复用 outcome_eval（win/loss/neutral，
>    分母排除 unable/pending，空桶返 None 不返 0.0）。
> 6. **0-100 量纲桶**（不是蓝本 validator 的 0-1 桶，量纲雷在 decision_log.py:464 已警告）。
> 7. **get_calibration_factor 带 600s TTL 进程缓存**：批量选股每只都算 cockpit 打分，不能每只查库；
>    校准只在每日 backfill 后变。fail-open：算不出来返 1.0，绝不弄坏打分。
>
> ### 与蓝本 CalibrationResult 的差异
> 蓝本 `daily_stock_analysis/memory.py` 有 dataclass；本项目返 dict（与 validator/_row_to_metrics 一致，
> 不为一个结构单开 dataclass）。样本门槛 `_MIN_CALIBRATION_SAMPLES=30`、窗口 50 都照抄。

> **一句话**：MoneyBill 每次都说「置信度：高」，**但它凭什么说高？凭 LLM 的感觉**。模型说 90% 有把握的事实际可能只有 55% 对。这一项拿 P0-1 的历史战绩反过来打它的脸：「你说高的那 80 条只对了 45 条 = 56%，那以后你说高我给你打个折。」

## 验收标准

1. `calibration_factor` 由**真实历史命中率**算出，反向调 MoneyBill 输出的 confidence。
2. **`raw_confidence` 一并留痕**（校准前后都留），便于审计。
3. `get_decision_history` 能让 MoneyBill 回答「我说高置信度时实际准多少」。

## 硬要求：≥30 样本才生效

只有 3 条历史、恰好错 2 条 → 准确率 33% → 就把置信度砍到三分之一？**那是被噪声带着走。**

**样本不足时 `calibration_factor` 恒为 1.0（不动它）。** 滚动窗口 50。

## 落点

| 类型 | 文件 | 要做什么 |
|---|---|---|
| 抄的模式 | `backend/prediction_engine/validator.py` | **置信度校准分桶自家已有**（给 ML 预测做的），把同一套标准推到 LLM 建议上——**这是「抄自己」不是造轮子** |
| 新增 | `backend/decision_log.py` | `compute_calibration(source, window=50)` → 按 source/confidence 分桶算实际命中率 |
| 修改 | `backend/agents/orchestrator.py` 收尾处 **或** `backend/agents/policy_checks.py` | 输出前用 factor 调整 confidence，`raw_confidence` 一并留痕 |
| 修改 | `backend/agents/tools/decision_tools.py` | `get_decision_history` 暴露校准数据 |

## 外部蓝本

| 文件 | 抄什么 |
|---|---|
| `daily_stock_analysis/src/agent/memory.py` | `CalibrationResult{total_samples, historical_accuracy, calibrated, calibration_factor}` + `_MIN_CALIBRATION_SAMPLES = 30` + `_ROLLING_WINDOW = 50`。**保留 `raw_confidence` 便于审计** |
| `daily_stock_analysis/src/agent/skills/aggregator.py` | 变体：**回测胜率 → 技能权重**（`factor = 0.5 + win_rate`，同样 ≥30 样本才生效）。若将来给 MoneyBill 做多策略加权可参考 |

## 与 P0-1「教训反哺半环」的关系

**互补，不重复。** TradingAgents 那条是**定性**的（LLM 提炼 2-4 句教训注入下次 prompt）；本项是**定量**的（数值系数调置信度）。**先做本项**——确定性强、可测。

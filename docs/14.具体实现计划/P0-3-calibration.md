---
id: P0-3
title: 置信度校准反哺
size: 小
depends: P0-1（没有后验结果就无从校准）
paths_verified: 2026-07-17
---

# P0-3 置信度校准反哺

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

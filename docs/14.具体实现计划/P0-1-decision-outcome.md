---
id: P0-1
title: 建议后验评估器
size: 小
depends: 无
blocks: P0-3
paths_verified: 2026-07-17
---

# P0-1 建议后验评估器

> **一句话**：MoneyBill 说完「茅台买入，止损 1580，目标 1750」就完事了，**没人回头问一句后来对不对**。这一项给 AI 装上记性。
>
> **别混淆**：C++ 回测测的是**策略**（「金叉买入这规则历史上赚不赚钱」）；本项测的是 **AI 的嘴**（「MoneyBill 上周说买茅台，对了吗」）。两根轴，不冲突、不重复。

## 验收标准

1. `get_decision_history` 能回答「MoneyBill 的推荐历史胜率是多少」，按 source 分组。
2. cockpit 的决策进了 DecisionLog（**07-17 复核：`backend/cockpit_engine/` 全目录零 `record_decision` 调用**）。
3. `prompt_version` 在所有写入点有值。
4. 回填任务挂进 `daily_pipeline_scheduler` 的每日链。

## 必须做对的三条设计（已裁决，不要重新讨论）

1. **`unable` ≠ `miss`**
   100 条建议里 60 对、20 错、**20 条根本没法评**（停牌 / 新股数据不够 / 建议里没写止损）。把「没法评」算成「判错」→ 准确率 60%；真实是 60/80 = **75%**。**差 15 个点全是自己冤枉自己。**
   → `outcome_status` 至少 `completed` / `unable` 两态 + `unable_reason`；`unable` 再分**可重试**（数据暂缺，明天补了重跑）与**不可重试**。

2. **`engine_version` 戳**
   判定口径（窗口天数 / 中性带）以后一定会改。不打版本戳，历史结果会随代码演进**悄悄漂移**，而且你永远不知道。

3. **`first_hit="ambiguous"`**
   同一根日线里止损和止盈都被触及时，**日线数据无法判断先后**。必须显式标 ambiguous 并保守假设先止损，**不许猜**。

## 落点

| 类型 | 文件 | 要做什么 |
|---|---|---|
| 抄的模式 | `backend/analysis_engine/signal_tracker.py` | `SignalTracker.update_all` 的 pending→completed 回填骨架，直接照抄 |
| 抄的模式 | `backend/prediction_engine/validator.py` | `backfill_outcomes` 的到期回填 + 置信度校准分桶，胜率统计照这个做 |
| 修改 | `backend/data_engine/storage/models.py`（`DecisionLog` 类，约 :1126） | 加字段：`outcome_5d` / `outcome_20d` / `hit_stop` / `hit_target` / `outcome_status` + `unable_reason` / `first_hit`（`stop_loss`\|`take_profit`\|`ambiguous`\|`none`）/ `first_hit_days` / `engine_version`（如 `decision-outcome-v1`）。**07-17 复核：这些字段一个都还没有** |
| 修改 | `backend/decision_log.py` | 新增 `backfill_outcomes()` + 按 source 的胜率统计查询；`record_decision` 对关键字段缺失加醒目告警（**现在吞异常太静默**，漏传不报错） |
| 修改 | `backend/cockpit_engine/aggregator.py` | 聚合完成处补 `record_decision(source="cockpit")` |
| 修改 | `backend/advisor_engine/service.py`（:161 附近） | 补传 `prompt_version`、结构化 `reasons`、prompt 原文 |
| 修改 | `backend/recommend_engine/engine.py`（:225 附近）、`backend/agents/confirm_gate.py`（:92 附近） | 补传 `prompt_version` |
| 修改 | `backend/data_engine/daily_pipeline_scheduler.py` | 每日链（现有五步串行）末尾挂 outcome 回填为第⑥步 |
| 修改 | `backend/agents/tools/decision_tools.py` | `get_decision_history` 输出加胜率/归因字段 |
| 测试 | `backend/tests/` | 参考 `test_turn_monitor.py` 的纯规则测试写法 |

**`prompt_version` 取值**：用 prompt_builder 模块里的常量版本号（每次改 prompt 手动 bump），或取文件内容 hash 自动化。二选一，写下来别混用。

## 🔍 探查前置（**不阻塞开工**——本卡的主蓝本已是源码级可信，见下方「外部蓝本」）

> **本卡是唯一「探查全部为选修」的 P0 卡**，因为 `daily_stock_analysis` 那 820 行已经把核心逻辑喂到嘴边了。下面两项是**边角补充，探不探都能写代码**——但都归位到了本卡，因为它们对应的正是本卡的验收标准。

| 档 | 对象 | 对应本卡的 | 带着这个问题去 | 状态 |
|---|---|---|---|---|
| 选修 | **TipRanks** | 验收标准 1（胜率） | 「**每个建议都被记分**」的产品化范式 = 本卡要达到的形态。⚠️ **闭源，只能看产品**：重点看它**怎么呈现**一个分析师的历史战绩（分窗口？分标的？怎么处理「没法评」的那些？）。<br/>**（2026-07-17 归位：原挂在 P1-4，但 P1-4 是失效条件监控；`docs/14` §7 白纸黑字写它对应 P0-1）** | ❌ 未探 |
| 选修 | **MLflow** 的 model/prompt registry | 验收标准 3（`prompt_version`） | prompt 版本管理的成熟范式（版本 + 内容 + 指标关联）。带着问题去：**上方「二选一」那个决定（常量 bump vs 内容 hash）它怎么解的？**<br/>**（2026-07-17 归位：原挂在 P3-2「远期、商业化打磨期再做」那张卡的参考里——等于本卡开工时根本看不到它，而 `prompt_version` 明明是本卡的活）** | ❌ 未探 |

> **同类顺手参考**：`docs/14` §10.6 的 **`decision_scale.py` 单一真源模式**（同一文件同时导出给 LLM 的 prompt 文字和给代码的判定函数，**让 prompt 与代码口径漂移不可能发生**）。这个已在 `00-PLAN.md` Backlog 里，与 `prompt_version` 是同一类病的两种解法，**决定取值方案时一并想**。

## 外部蓝本

| 文件 | 抄什么 |
|---|---|
| `daily_stock_analysis/src/core/backtest_engine.py`（820 行） | **纯逻辑、DB 无关**（`DailyBarLike`/`BacktestResultLike` 两个 Protocol），移植阻力最小。`evaluate_single()` = 建议日/起始价/止损/止盈 + 未来 N 根 bar → 方向对不对、先碰止损还是止盈。`compute_summary()` = 方向准确率/首次命中天数/ambiguous 计数。**⚠️ 它的输入是中文自由文本建议（要关键词+否定检测猜方向），我们不需要这段**——DecisionLog 的 `action` 本来就是结构化的，直接读 |
| `daily_stock_analysis/src/services/decision_signal_outcome_service.py` | `unable≠miss` + `RETRYABLE_UNABLE_REASONS` + 多窗口（1/3/5/10d）落库编排。**它把「策略回测」和「决策复盘」共用同一个评估内核**，保证口径一致——这点一起抄，否则复盘结果会漂 |
| `daily_stock_analysis/src/repositories/decision_signal_repo.py` | `_IMMUTABLE_REFRESH_FIELDS`：refresh 时冻结 `created_at`/`source_report_id`/`trace_id`/`action`/`horizon`。**原始决策不可篡改，否则复盘就是自欺欺人** |

> **可选二级参考**：`ai-hedge-fund`（virattt）把 **agent 历史决策当策略回测**跑，评估「这个 agent 组合到底赚不赚钱」。**不设为本卡闸门**——上表三个蓝本已经把本卡喂饱了，它的主战场在 **P1-1**（那张卡才是真空白）。若探 P1-1 时顺手看到，回填到这里。

## 可选后半环：教训反哺（做完主体再说）

`TradingAgents-main/tradingagents/agents/utils/memory.py` + `graph/reflection.py` + `graph/trading_graph.py:296-334`：回填真实收益后让 LLM 提炼 **2-4 句教训**（刻意压短防撑 context），注入下次决策 prompt（同 ticker 近 5 条 + 跨 ticker 近 3 条）。

- **回填骨架仍抄自家** SignalTracker/PredictionValidator——我们有本地行情库和定时任务，不必像它那样「下次 run 时现拉 yfinance」。
- **教训生成+注入**参考它。
- 与 P0-3 **互补不重复**：这条是**定性**的（文字教训），P0-3 是**定量**的（数值系数）。**先做 P0-3**（确定性强、可测），教训半环随后。

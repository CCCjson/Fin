---
id: P0-1
title: 建议后验评估器
size: 小
depends: 无
blocks: P0-3
paths_verified: 2026-07-17
status: ✅ 已完工 2026-07-17（commit ee561a1 → f95c08d，六笔）
---

# P0-1 建议后验评估器

> ## ✅ 已完工（2026-07-17）—— 下方是原始计划，实施时的偏离见本框
>
> **四条验收标准全部达成**，代码承载物：
> - 评估内核 `backend/common/outcome_eval.py`（纯逻辑/DB 无关/Protocol 入参/`age_days` 而非 `date.today()`）
> - 回填 + 胜率 `backend/decision_log.py`：`backfill_outcomes()` / `get_decision_stats()` / `_OUTCOME_WRITE_FIELDS` 白名单
> - schema：`DecisionLog` 加 12 列 + `idx_decision_outcome_scan`；`init_db()` 幂等 ALTER
> - 每日链第⑥步；门禁 `tests/baseline/test_prompt_version_pinned.py`
>
> ### 实施时对本卡的五处偏离（都已跟 Jason 逐条确认，别改回去）
>
> 1. **cockpit 留痕接在工具层 `agents/tools/analysis_tools.py`，不在 `aggregator.py`**（本卡原文写的是后者）。因为 `aggregate()` 另一个调用方是 `recommend_engine/scoring.py` 的批量选股循环（4 线程、每轮几十只），写在 aggregator 里会灌爆 DecisionLog 且与 `moneybill_recommend` 重复计数。**已加门禁 `test_cockpit_provenance_is_not_in_aggregator` 防复发**，并实测回归。
> 2. **`completed` 的门槛是「方向可评」（action + entry_price + 够 bar），不是「止损止盈齐全」**。实锤：`recommend_engine/engine.py:142` 构造 buys 时压根没 take_profit 键 —— 照本卡字面判会让主力 source 100% unable。
> 3. **多加了 `return_5d`/`return_20d` 两列**（本卡字段清单只有 label）。归因要 `avg_return`，且以后调中性带能直接重算 label 不必重跑行情。范式同 `SignalTracking`。
> 4. **`no_entry_price` 判不可重试**（外部蓝本判可重试）。它的锚定价是现拉行情、可能暂时缺；我们的 `entry_price` 是被 `_IMMUTABLE_REFRESH_FIELDS` 冻结的存量字段，NULL 就永远是 NULL。
> 5. **`hit_stop`/`hit_target` 首次命中就 break**，不记全窗口（照蓝本）。止损出局后已空仓，后面再碰止盈跟你没关系；记全窗口会让 `hit_target_rate` 虚高。
>
> ### `prompt_version` 取值：**常量 bump + baseline 测试钉源文件 hash**（运行时不算 hash）
> 五个常量就近落在「它所版本化的东西」旁：三个 prompt_builder + `cockpit_engine/scorer.py`（版本化的是权重表）+ `agents/skills_loader.py`（版本化的是 `monitor.md`）。
> 不用运行时 hash 的决定性理由：report 的 prompt 是**逐股动态渲染**的 → 对渲染后文本取 hash = 每只股票一个 hash = 不是版本号是随机数，`GROUP BY prompt_version` 碎成 N 组，归因废掉。
>
> ### 首跑真库（25 条）的发现 —— 全部可解释，且都是 `unable≠miss` 的价值现场
> - **`moneybill` 7 条里 6 条 `no_action`**：`confirm_gate` 把「加/删自选股」这类**非交易确认**也记成 decision。旧口径会把它们算成「判错」稀释胜率，现在诚实地不进分母。
> - **`report_picks` 10 条 `no_quotes`** → **顺藤摸出两个独立的真问题（都不属 P0-1，别当成本卡遗留）**。行情日期分布（07-17 查）：07-06 有 18493 只、07-07~07-08 只剩 ~5190、07-09 残缺 4216 只、**07-10 之后全市场只有 2 只**，缺 6 个交易日。
>   - 对本卡而言这批 `no_quotes` 判**可重试**是对的，日线补回来就能评 —— 也印证了「第⑥步必须排在第①步之后」。手动跑 `DailyUpdater().update_stream()` 实测能补回来。
>   - 🔴 **问题一：A 股每日链从 07-09 起停摆**，因未查（方向：`DAILY_AUTO_UPDATE_ENABLED` / 后端是否常驻 / 代理额度）。
>   - ⚠️ **问题二：港美股根本不在每日链范围内** —— `daily_updater.py` 硬过滤 `market == "a_share"`。所以「港美股 07-07 先断」**不是故障、是设计如此**；「港美股行情靠什么更新」是个未答的问题。**别把这两件事混成一个 bug 查。**
>   - 顺带解释「补 6 天为什么慢」：`update_stream` 只有 `latest == prev_trading_date`（只差一天）才走快速批量路径，差多天一律落进 `history_needed` 慢路径逐只抓 → **断更越久补得越慢**。
>
> ### 遗留（不在本卡范围，想做时再说）
> - **advisor v1 恒为 `unable/no_action`**：不传 action/entry_price，本卡明确不做「解析中文自由文本猜方向」。要让它可评得在流式结束后跑结构化抽取 —— 另开卡。
> - **重复跑选股会重复计数同一条建议**：真库里 601156.SH 07-02 那条被记了 3 次（同一入场价）。这是 `recommend_engine` 既有的记录方式，不是本卡引入的，但会让胜率分母虚高。
> - **`engine_version` 混版本**：bump 后老 `completed` 行不重算。本卡只做「可见」（`get_decision_stats` 返回数据里实际 distinct 的版本集合）。
> - **`record_decision` 的 `finally` 缺 rollback**（`decision_log.py`）：commit 抛异常时连接带脏事务被 close。SQLite+NullPool 下危害有限。
> - **P0-3 的量纲雷**：做置信度分桶校准时**别抄 `validator.py:246` 的桶**（那是 0-1 量纲），`confidence` 这列是 0-100。

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

---

## 🪙 crypto 实况补记（2026-07-27 重设计｜**分类 ④：已完工，但 crypto 实况未验**）

> 本卡写于 2026-07-07~17，**crypto 模块 07-20 才启动**，卡里零 crypto 覆盖。以下是 07-27 查生产库/代码的实况，**是事实补记不是方案变更**。

### ✅ 代码层面 crypto 已通（不用改）

| 检查 | 结果 |
|---|---|
| `backfill_outcomes` 认不认 crypto | ✅ **已逐行按 symbol 推市场**（`decision_log.py:406` `infer_market_from_symbol` + `market_today` + `market_day_of`），注释原文：「decision_logs 里 A 股和 crypto 混存（crypto 的市场日是 UTC），所以只能逐行按 symbol 推市场」 |
| crypto 日线取不取得到 | ✅ 落**同一张** `daily_quotes`（`market='crypto'`，460 币种，最新 07-26 —— 比 A 股还新） |
| `infer_market_from_symbol('BTCUSDT.BN')` | ✅ 实测返回 `crypto` |
| `outcome_eval` 内核 | ✅ **纯函数、市场无关**（调用方切好 bars 传入，只按 bar 序号算） |

**这是时区统一工程（[[timezone-unification]]）顺手带到的** —— 那轮改造覆盖了 crypto，本卡因此意外地不用返工。

### 🔴 但数据层面有雷 —— 见新卡 [[P0-4]]

| 实测 | 数字 |
|---|---|
| `decision_logs` 总行数 | 98 |
| **crypto 来源占比** | **53 行 = 54%（最大来源）** |
| `outcome_status = completed` | **0** |
| 🔴 `BTCUSDT.BN BUY entry_price=100.0`（BTC 真实价 6 万+） | **39 行 = 全表 40%** |

**本卡的评估管道是对的，喂给它的东西不对。** `source="crypto"` 的 45 条来自 `crypto_intel_engine/execution.py` 的**订单成交/挂单回执**，不是 AI 建议 —— 拿它们算胜率在语义上就是错的；而 39 条 entry=100 一旦攒够 5 根 bar 会被评成 **+64900% 的 win**。

**详见 `P0-4-decision-log-hygiene.md`。P0-1 的数字在 P0-4 完工前不可信。**

### ⚠️ 一个口径差异（不是 bug，但任何并排展示都要标注）

`HORIZONS = (5, 20)` 数的是 **bar 数**：

- A 股 20 根 bar ≈ **4 个自然周**
- crypto 20 根 bar = **20 个自然日**（7×24 每天出 bar）

**两者的「20 日胜率」不是同一个东西。** 跨市场混算或并排展示时必须标注窗口长度不等。

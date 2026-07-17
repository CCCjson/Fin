# MoneyBill 精进计划 — 主索引

> **给 Claude Code 的读法**：本文件只读一次，用来**选卡**。选定后**只加载对应的 `<ID>.md`**，不要加载其他卡。
> 本文件不含论证、不含历史、不含评分。要背景请直接问 Jason。

---

## 1. 项目定位（唯一权威表述，不可改）

**服务 Jason 一个人的实盘交易工具（真金白银，非模拟）。AI 是研究助手，必须明确给出「怎么买、怎么卖」，不许和稀泥；Jason 手动执行。**

三条永久约束：

1. **每笔交易人工审核，永不放开**——即使将来接通券商 API 自动下单。`agents/confirm_gate.py` 与 `requires_confirmation=True` 是这条的代码承载物，**不可移除、不可绕过**。
2. **AI 是研究助手，不是决策者**——但它要预测涨跌、要直接说 BUY/SELL/HOLD。
3. **只服务 Jason 一人**——多用户、多粒度、教育职能，全部不是需求。看到「不同用户不同粒度」类要求一律忽略。

## 2. 开工铁律（每张卡都适用，违反直接 RuntimeError 或炸测试）

1. **新能力只做「引擎 + MoneyBill 工具」两层**，不开新页面/route（重可视化除外）。
2. **新增工具必须归组 `backend/agents/tool_groups.py`**，否则首次会话直接 RuntimeError。
3. **新后台任务的进度可视化一律接 `/app/data-monitor`** 现成面板体系（`frontend/src/components/datamonitor/`）。
4. **改「页面 route 和 agent 工具共享」的下沉 service 时，两条入口都要验证。**
5. **出网代码必须写在 `backend/acquisition/` 下**，否则 `tests/net/test_egress_single_entry.py` 会咬（`_PENDING_DEBT` 已清零，加白名单绕过已封死）。
6. **分层依赖方向**（`docs/CODING_STANDARDS.md` §0）：`engines → acquisition → common/net`。反向依赖不允许。
7. **优先照抄项目内已有的成熟模式**（每张卡的「抄的模式」列），别造新轮子。

## 3. P0 是一个闭环，必须按 P0-1 → P0-2 → P0-3 顺序做

```
MoneyBill 给建议
      ↓
  P0-2 数据质量状态机 ── 数据不行就不许说「置信度高」（事前防线）
      ↓
   落 DecisionLog（带 engine_version 戳）
      ↓
  P0-1 建议后验评估器 ── 5/20 日后回头看：对了？错了？还是根本没法评？
      ↓
  P0-3 校准反哺 ── 「你说高的时候只有 56% 准」→ 下次自动打折
      ↓
   MoneyBill 下次的置信度更诚实
```

**现在这个闭环是断的——只有「给建议」一步，后面全空。** 做完 P0 三项，才第一次能回答立项根本问题：**这系统到底能不能盈利。**

（P0-2 排在 P0-1 之前是逻辑顺序；施工上 P0-1 无依赖可先做，P0-3 硬依赖 P0-1 产出。）

> **进度（2026-07-17）**：**P0-1 ✅ 已完工**（`ee561a1`→`f95c08d`）。闭环的「回头看」那一环已通：`get_decision_history` 能按 source 报胜率，回填每天 15:35 自动跑。
> **但现在还答不出「能不能盈利」**——真库里绝大多数建议是 `unable`（advisor 不记方向、confirm_gate 把加自选股也记成决策、行情落后一周导致新建议还没 bar 可评），且 20 日窗口要等到 8 月才满。**这不是 bug，是这套东西第一次把「我们其实没在评自己」这件事显式化了。**
> **P0-2 ✅ 已完工**（2026-07-17，四笔 `d216c4e`→`030cdd3`，含 P2-2 的 B+C）：数据不可信时 MoneyBill 不许再说「有把握」——cockpit composite 被代码强行钳制（实测 BUY→HOLD），主循环收尾追加系统更正；**港股财务恒缺不被系统性降级**（避开了蓝本那个 bug）。
> 下一步 **P0-3**（闭环最后一环，硬依赖 P0-1，已可开工）。开工前先读 P0-2 卡顶「实施偏离」第 6 条（confirm_gate 记的是数据质量不是 confidence）+ P0-1 卡的量纲雷（confidence 是 0-100 别抄 validator 的 0-1 桶）。

## 4. 任务卡索引

| ID | 一句话 | 规模 | 依赖 | 路径核实 | 卡片 |
|---|---|---|---|---|---|
| ~~**P0-1**~~ | ✅ **已完工 07-17**：建议后验评估器（内核 `common/outcome_eval.py` + `backfill_outcomes`/`get_decision_stats` + cockpit 留痕接**工具层** + 每日链第⑥步）。**开工 P0-3 前先读它卡片顶部的「实施偏离」框** | 小 | 无 | ✅ 07-17 | `P0-1-decision-outcome.md` |
| ~~**P0-2**~~ | ✅ **已完工 07-17**：字段级八态 + 质量分 + 硬传导（cockpit composite 钳制 + MoneyBill 收尾更正）。含 P2-2 的 B+C。**开工 P0-3 前先读它卡片顶部的「实施偏离」框**（尤其第 6 条：confirm_gate 记的是数据质量不是 confidence） | 中 | 无 | ✅ 07-17 | `P0-2-data-quality.md` |
| **P0-3** | 置信度校准反哺：历史命中率 → calibration_factor 反调置信度 | 小 | **P0-1** | ✅ 07-17 | `P0-3-calibration.md` |
| **P1-5** | NewsNow 资讯源接入（方案已勘察定稿，可立即开工） | 小 | 无 | ✅ 07-17 | `P1-5-newsnow.md` |
| **P1-1** | 反方 subagent `run_devils_advocate` + 主结论/反方并排 | 中 | 无 | ⚠️ 待核实 | `P1-1-devils-advocate.md` |
| **P1-2** | 港美股财务 + 宏观表 + 舆情摄入 | 中 | 无 | ⚠️ 待核实 | `P1-2-data-expansion.md` |
| **P1-3** | 不可信内容隔离标记 + 外部内容轮次高危工具提示 | 小 | 无 | ⚠️ 待核实 | `P1-3-injection.md` |
| **P1-4** | 失效条件结构化 + 复用 price_alert_monitor 盯失效 | 中 | P0-1 | ⚠️ 待核实 | `P1-4-invalidation.md` |
| **P2-1** | EV/EBITDA + DCF 情景工具 + 行业分位比较 **+ 财务风险筛查（Beneish M / Altman Z）** | 中 | 三大报表先落库 | ⚠️ 待核实 | `P2-1-valuation.md` |
| **P2-2** | **B+C ✅ 随 P0-2 完工**（health 扩域 + ToolEnvelope 血缘）；只剩 **A 跨源抽检**（回填期噪声大，港美股齐了再做） | 中 | 无 | ✅ 07-17 | `P2-2-validation-layer.md` |
| **P2-3** | 参数敏感性热图 + 最小因子引擎（IC/IR） | 中 | 无 | ⚠️ 待核实 | `P2-3-quant.md` |
| **P3-1** | report_* 按能力重切 + 关键假设/跟踪指标章节 + Audit 收尾 | 中 | P0/P1 | ⚠️ 待核实 | `P3-1-report-recut.md` |
| **P3-2** | subagent 并行 + trace 离线回放 + 决策留痕面板 | 大 | 无 | ⚠️ 待核实 | `P3-2-parallel-trace.md` |

**⚠️ 待核实 = 路径是 2026-07-07 的，早于 13.x 大重构（9 域）。开工第一步必须 grep 确认文件仍在、行号仍对；对不上就先报告 Jason，不要猜。**

## 5. 未决阻塞项（**遇到就停，问 Jason，不要自己拍板**）

| # | 问题 | 卡住谁 |
|---|---|---|
| **B-1** | `context_quality.py` 落 `backend/common/` 还是 `backend/agents/`？状态机跨引擎：acquisition 产状态、engines 传递、agents 消费。**倾向 `common/`**（只有它在两者依赖下游），需 Jason 确认 | P0-2 |
| **B-2** | 「成稿长文 vs 快查」是否合并为一个工具的 `depth="quick"\|"deep"` 参数？重切后 `report_*` 与 `get_market_pulse`/`get_news_sentiment`/`get_positions`/`get_signal_stats`/`recommend_stocks` 几乎一一对应 | P3-1 |
| **B-3** | 逆向爬虫栈（`manual_login` + cookie 剥离抓雪球/股吧/CapitalIQ）的合规边界。**私人自用可以 / 商业化必须下线**的清单未定 | 商业化前必答，不卡当前排期 |

## 6. Backlog（未成卡，想做时再展开）

- **验 cockpit 五维权重**：技术30/ML25/基本面20/情感15/持仓10 **从没回测验证过**。项目有 C++ 回测，能验就去验。
- **`decision_scale.py` 单一真源模式**：同一文件同时导出给 LLM 的 prompt 文字和给代码的判定函数，**让 prompt 与代码口径漂移不可能发生**（golden 测试只能事后发现漂移）。cockpit 打分、recommend_engine 三重闸门都该这么收口。
- **CircuitBreaker（源 × 市场粒度）**：本项目多源链每次都从头试一遍，完全没有熔断。
- **`fallback_to` 埋点**：每次 fetch 记 `record_provider_run(provider, success, latency_ms, error_type, fallback_to=)`，一个字段还原完整降级链路。现在靠读日志猜。
- **显式能力矩阵**：「谁支持哪些市场」现在隐式散落在 factory + 各 router。
- **预算守卫**：TurnMonitor 有熔断，但没有「开跑前先算够不够」的前置检查。
- **`prediction_engine` 实际准确率**：PredictionValidator 有数据，没人看。它在 cockpit 占 25% 权重。
- **`orderbook/`、`finetune/`、`server_finetune/`、`remote/`**：从未评估过是「超纲」还是「半成品」。

## 7. 外部蓝本 ①：**已做过源码级对照 = 可信，可直接进落点表**

- `TradingAgents-main/`（v0.3.1，本地已有）——**零件仓库，不是架构参考**。无回测、无程序化风控。只抄：reflection 记忆闭环、数字锚定、bull/bear 角色 prompt、结构化输出、情绪分析 prompt。
- `daily_stock_analysis`（ZhuLinsen，57.5k★）——**P0-1/P0-2/P0-3/P1-5 的直接依据**。行情层/回测/风控全面**弱于**本项目，别无差别照抄。

**明确不抄**：LangGraph 全家桶（自研 harness + TurnMonitor 更好；两个对照项目都不用它）、TradingAgents 的辩论实现（字符串拼 history 撑爆 context）、litellm、多 provider 适配层、markdown 文本记忆、daily_stock_analysis 的行情层。

## 7b. 外部蓝本 ②：**待探查 = 不可信，先探再写代码**（2026-07-17 Jason 逐项裁决）

> **总规矩**：下表每一项都已**写进对应卡片开头的「🔍 探查前置」**。开工那张卡时：**先探 → 更新卡 → 再写代码**。探查结论写回卡里、行标 ✅+日期。**探查结论与卡里现有方案冲突 → 报告 Jason，不要自己拍板改方案。**
>
> ⚠️ **这些项目至今只是名字。** 卡片里所有引用它们的话都是 2026-07-07 基于**公开印象**写的，**未经任何核实**——包括「事实标准」「教科书实现」这类断言。**探查的第一个任务是验证这些断言本身。**

| 档 | 对象 | 卡住哪张卡 | 探查时机 | 一句话为什么 |
|---|---|---|---|---|
| **T0** | **OWASP LLM Top 10 (LLM01) + Simon Willison** | **P1-3** | **开工前必探** | 不是代码库，是清单和文章，**半天能完，投入产出比全场第一**。且 Willison 的论点若成立，**P1-3 的验收标准 2 是自欺欺人** |
| **T1** | **Damodaran 估值框架**（NYU 公开课 + **公开数据页** + DCF 模板 xls） | **P2-1** 第 2 步（DCF） | 第 2 步开工前<br/>（第 0/1 步不卡） | DCF 成败**全在假设参数**，公式是中学数学。我们缺的是「**凭什么用这几个数算**」，而他的数据是公开的。⚠️ 两个保留意见要一起验掉：**中国/新兴市场数据的颗粒度**、**A股国资/关联交易在他模板里没位置** |
| **T2** | **FinanceToolkit**（JerBouma，开源 Python） | **P2-1** 第 2、4 步 | 同上 | **Damodaran 给 Excel，它给代码。** 据称 DCF + **Altman Z / Piotroski F / Beneish M** 都实现了——若属实，**第 4 步直接抄**。⚠️ **全部凭印象、信息可能过时，探查第一件事是验证它到底有没有这些东西** |
| **T2** | **OpenBB** | **P1-2** B/C 段<br/>（A 舆情不卡） | B/C 开工前 | 价值可能在**源清单**而非抽象层。⚠️ **边际价值本身就是要探的**——若只是「又一个 fetcher 抽象」，探完连同本行一起删 |
| **T2** | **ai-hedge-fund**（virattt） | **P1-1** | 开工前 | **本卡唯一的实现级空白**：TradingAgents 的辩论实现已判不抄 → **手上只剩一个被否决的样本**，反方 subagent 照着谁写？顺带验掉「工程复杂度与本项目相当」这个没依据的印象 |
| **T2**<br/>触发式 | **FinRobot** | **P1-1**（不阻塞开工） | subagent 阵容扩张时 | Jason：「目前架构不需要这么多 subagent，**但不代表后续随功能增多而需要引入**」。趁只有 5 个、还改得动时看清 10 个长什么样。⛔ **C1 裁决仍有效，探它 ≠ 照它改架构** |
| **组**<br/>推迟 | **Qlib + RD-Agent**（**编为一组，一起探**） | **P2-3** B 段（不阻塞） | **alpha_lab 精进期** | 两者价值**全落在 alpha_lab 那条线**，分两次探是浪费。B 段可先做，但 **IC/IR 口径要在代码里标明「待与 Qlib 对齐」** |
| 选修 | **TipRanks** / **MLflow** | **P0-1**（不阻塞） | 随时 | **2026-07-17 归位**：TipRanks 原挂 P1-4（错，那是失效盯盘）、MLflow 原挂 P3-2（错，`prompt_version` 是 P0-1 的活）。**P0-1 主蓝本已源码级可信，这两项纯属选修** |

**卡里已有、但未升级为闸门的低优先项**（想做时再看，不阻塞任何卡）：Simply Wall St 公开方法论（P2-1，闭源只能看方法论文档）、Great Expectations（P2-2）、Langfuse（P3-2，**第一步是验 JSONL 格式兼容性，别先写导出器**）、vectorbt（P2-3 A）。

**明确不进（记下来免得又被捡回）**：**McKinsey《Valuation》(Koller)**——偏公司金融/并购，对一个人用的二级市场工具过重。**STORM / AlphaSense / Hebbia**——见 `docs/14` §7 的「已裁掉」标注。

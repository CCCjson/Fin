---
id: P1-6
title: ML 打分维度重建（幽灵维度 → 横截面模型）
size: A 段小 / B 段大
depends: A 段无依赖；B 段绑投资组合模块（共用「全市场排序」输出）
paths_verified: 2026-07-27
---

# P1-6 ML 打分维度重建

> **一句话**：cockpit 五维里 ML 占 25%，**但它从来没有产出过一个数**。模型目录空、两张表 0 行、每日链没有任何 ML 步骤。scorer 对缺失维度自动重新归一化 → **这 25% 被静默摊给了其他四维，而系统从不吭声**。这张卡先让这件事说出口（A 段），再把 ML 真正建起来（B 段）。

## 0. 现状实锤（2026-07-27 勘察，全部当场验过）

| 检查项 | 实测 |
|---|---|
| `backend/prediction_engine/saved_models/` | **空目录**，0 个模型 |
| `trained_models` 表 | **0 行** |
| `prediction_records` 表 | **0 行** |
| 每日链 6 步（`data_engine/daily_pipeline_scheduler.py`） | daily → signals → tracking → limit_up → valuation → decision_outcome，**没有任何 ML 步骤，也没挂 `PredictionValidator.backfill_outcomes`** |
| 全项目 sklearn/xgboost/torch 引用 | **除 `prediction_engine/` 外零处**。`limit_up_engine` 是规则打分、`alpha_lab` 是 LLM 产因子、`recommend_engine` 走 cockpit light |
| crypto 五维（`crypto_intel_engine/scorer.py:179`） | technical 30 / derivatives 20 / regime 20 / flow 15 / sentiment 15 —— **没有 ML 维** |

**代码是齐的**（1350 行：LSTM + XGBoost + Ensemble + Validator + 远程 GPU 训练桥），**只是从没通过电**。

### 25% 权重实际去了哪

`aggregator._score_ml()` → `PredictionEngine().predict()` → 无模型 → `FileNotFoundError` → `ml = None` → `scorer.py:131` 对可用维度**重新归一化权重**：

| 维度 | 纸面权重 | **实际生效** |
|---|---|---|
| technical | 30% | **40%** |
| ml | 25% | **0%** |
| fundamental | 20% | **26.7%** |
| sentiment | 15% | **20%** |
| position | 10% | **13.3%** |

批量选股（`recommend_stocks`）走 light 档更狠：主动跳过 ml + sentiment，`dimension_coverage = 0.6`。

⚠️ **这不是 bug，是设计**：`dimension_coverage` 字段就是为此而生（scorer.py:103 的注释写得很清楚：「一维算出的 65 与五维算出的 65 在下游长得一模一样……这个字段就是那个哑巴」），`model_id` 也老实写着 `rule:cockpit_light`。**问题是这份诚实没传到 MoneyBill 的嘴里** —— Jason 看到的建议不会告诉他「这个分是四维算的」。

---

## 1. 🔴 算力裁决：砍掉 LSTM，只做 GBDT（2026-07-27 Jason 确认远程 GPU 已不可用）

`prediction_engine/remote_predict.py` 走 SSH 到 `deepoptica` 远程 GPU 训练。**该服务器已不可用**，且 `.env` 里 `PREDICTION_REMOTE_DIR` / SSH 相关配置**本来就一条都没有**（走的是硬编码默认路径）。

**结论：这不是损失，是逼出了更对的技术选型。**

### 为什么砍 LSTM 是升级不是妥协

1. **横截面选股 = 表格数据 + 排序问题，GBDT 是这类问题的 SOTA**，不是「没 GPU 的退路」。Qlib 的官方 baseline 就是 LightGBM。
2. 原设计里 LSTM 是**预测价格序列**（回归），然后被 `ensemble.py:35` 的 `lstm_prob_up = 0.5 + return * 5` 硬掰成概率——**那个 `5` 是凭空来的**。砍掉 LSTM 同时消灭了这个拍脑袋的转换。
3. **CPU 跑得动**（M5 / 10 核 / 16GB，`xgboost 3.2.0` 已装）：
   - 全市场面板 ≈ 5201 只 × 870 交易日 ≈ **452 万行** × ~40 特征 float32 ≈ **720MB**
   - XGBoost `tree_method="hist"` 10 核单轮训练：分钟级
   - walk-forward 每季度重训、2023→2026 约 14 期 → **一晚上跑完**

### ⛔ 16GB 是真约束（有前科）

见 [[backend-memory-leak-22gb]]：本项目出过 22GB 内存卡死事故。ML 训练是新的大内存入口，**三条硬要求**：
- 特征矩阵**必须 float32**（默认 float64 直接翻倍到 1.4GB）
- 按时间窗**分块加载**，不许一次性 `SELECT *` 全市场全历史进内存
- 每期训完**立即释放**（`del` + 不留全局引用），不许像 bge-m3 那样常驻

### 备选方案（不推荐，但记下来免得被重新捡回）

| 方案 | 结论 |
|---|---|
| **PyTorch MPS 后端跑 LSTM** | 技术上可行——[[alpha-lab]] 记的「M5 Metal 有 bug」是 **Ollama 自己的编译问题**，PyTorch MPS 是另一条路，`torch 2.10.0` 已装。但对横截面排序任务不值得，**不做** |
| **按小时租云 GPU**（Colab / AutoDL / Lambda） | GBDT 根本不需要 GPU。**只有将来真要上深度序列模型时才重新评估** |
| **MLX** | 对表格 GBDT 无意义（它是给 LLM 推理用的，见 Alpha Lab 那条） |
| **装 LightGBM** | 比 XGBoost 在大规模表格上更快更省内存，且**将来与 Qlib 对齐方便**（P2-3 已埋了「IC/IR 口径待与 Qlib 对齐」的伏笔）。**建议装但不阻塞**，XGBoost 已够用 |

---

## 2. 阶段 A｜止血：让缺席变显式（**立刻做，半天，零 ML 含量**）

> Jason 2026-07-27 拍板：**A 立刻做**。

**不改任何打分逻辑，只是不再让系统假装自己有五维。**

`dimension_coverage` 已经算好了（scorer 返回值里），只是没人往上传。要做的就是把它接到嘴上：

- MoneyBill 给建议时，若 `dimension_coverage < 1.0`，明确说出来：
  「本次评分未含 ML 维（无模型），实际由四维加权，覆盖度 75%」
- 覆盖度低于某阈值（建议 0.7，即 light 档的 0.6 会触发）时，措辞要更收敛——这跟 P0-2 数据质量硬传导是**同一个哲学**：*不可信的东西不许说得斩钉截铁*

### A 段验收标准

1. `dimension_coverage < 1.0` 时，MoneyBill 输出**必然**出现维度缺席披露（门禁测试钉死，不靠 prompt 自觉）。
2. 披露文本里**说得出缺了哪几维**，不是笼统一句「数据不全」。
3. **不动 `WEIGHTS`、不动 `SCORER_VERSION`** —— A 段是纯披露，打分一个数都不变。

### ⚠️ A 段的一个诱惑：别把 `ml` 从 WEIGHTS 里摘掉

摘掉 + bump SCORER_VERSION 看起来更"干净"，但**会丢掉将来 B 段接回来的位置**，而且让历史 SCORER_VERSION 对比变复杂。**保留权重 + 显式披露**是正解。

---

## 3. 阶段 B｜横截面 ML 打分（**绑投资组合模块一起做**）

> Jason 2026-07-27 拍板：**B 跟投资组合模块绑**。

### 为什么绑：它俩要的是同一个东西

组合构建器要干的事是**从全市场选出 5-6 只**（见 [[portfolio-module-plan]] 阶段 1），而横截面 ML 的输出形态**正好就是「全市场排序」**。

现在 `recommend_engine` 的 top-N 走的是 cockpit light 单票打分（coverage 0.6、ML 权重 0），换成横截面模型是实打实的升级。**反过来说，如果 ML 单独做、组合单独做，两边会各自造一套「怎么排序」的逻辑。**

### 🔴 现有 `prediction_engine` 的 5 个方法论问题（不修就训 = 造负资产）

| # | 问题 | 位置 | 严重度 |
|---|---|---|---|
| 1 | **每票一模型 = 架构性死路**。5201 只 A 股 = 5201 个模型，每个仅 ~480 样本却喂几十个特征 → 必然过拟合，且运维上跑不动 | `engine.py:94`（模型按 symbol 分目录） | 🔴 致命 |
| 2 | **特征含非平稳绝对价格**。exclude 名单只排了 `date/symbol/id/amount`，`close`/`open`/`high`/`low`/`volume`/`close_lag1..10`/`ma5`/`ma20` **全部进了模型**。树按绝对阈值分裂，训练期 15 元学的分裂点、预测时 30 元全部失效 | `features.py:54` | 🔴 致命 |
| 3 | **LSTM scaler 前视泄漏**：先 `fit_transform` 全量、第 98 行才切 train/val，验证集 min/max 泄漏进训练 → 报出的 MSE 偏乐观 | `lstm_model.py:89-90` vs `:98` | 🟡（砍 LSTM 后自动消失） |
| 4 | **置信度拍脑袋且无校准**：`0.5 + return*5` 的 `5` 凭空来；confidence 直接乘进 cockpit（`score = 50 + 50*conf`）。项目已有 P0-3 校准框架，但那校准的是 composite，**管不到 ML 自己这个自由发挥的 confidence** | `ensemble.py:35` / `aggregator.py:216` | 🟡 |
| 5 | **标签没中性区、没扣成本**：`future_return > 0` 就算 UP。A 股 5 日涨跌接近 50/50，报出的 55% accuracy 里多少是基准率说不清；涨 0.1% 也算 UP，扣掉万2.5佣金+千1印花税压根不赚 | `xgboost_model.py:52` | 🟡 |

### B 段设计要点

**一个模型管全市场**，输出全市场排序分。

| 项 | 定案 |
|---|---|
| **模型** | XGBoost（`hist`）单模型；LightGBM 可选替换。**不做 LSTM、不做集成** |
| **训练样本** | 全市场 × 交易日面板。`signals` 表实测干净（2023-01-03 起、870 交易日、181 万条），前视偏差已在 [[portfolio-module-plan]] 验过 |
| **特征** | **只用无量纲的**：`return_*`、`bias_*`、`vol_ratio_*`、本身有界的指标（RSI/KDJ）。**绝对价格/绝对量一律剔除**（问题 2） |
| **财报特征** | 按 `report_date + 45 天 <= 决策日` 对齐（与组合模块同款口径） |
| **⛔ 估值** | **绝不碰 `stock_valuations`** —— 只有 8 个快照日，用它 = 拿今天的 PE 选一年前的股票 |
| **标签** | **未来 N 日相对市场中位数的超额收益** + 中性区（\|超额\| < 1% 的样本丢弃）。去掉大盘 beta，这才是选股模型该学的 |
| **训练方式** | Walk-forward 滚动重训：每季度用 T 之前数据训、预测 T+1 季度。**绝不全期一次训完**（那是前视） |
| **输出** | 全市场排序分 → ①接回 cockpit `ml` 维；②直接喂组合构建器当候选源 |
| **闭环** | **必须接 P0-1 后验评估**。别再重复一次「有模型没人看准确率」（`prediction_engine/validator.py` 写得挺好但从没挂进每日链） |

### 🔴 B 段开工前必须先拆的雷

`aggregator._score_ml` 每调一次 `predict()` 就 **INSERT 一条 PredictionRecord**（`engine.py:182`）。

现在 0 行是因为**压根没模型**。**模型一有就炸**：批量选股每只票都走 cockpit → 疯狂灌库，且这些记录没人回填。

**开工第一件事：把「打分」和「留痕」解耦** —— 打分路径只读不写，留痕走独立的、有节流的入口。

### B 段验收标准

1. 一个模型给全市场打分，`saved_models/` 里**只有一个模型目录**（不是 5201 个）。
2. Walk-forward 回测报出 **IC / IR / 分组超额**，且口径在代码注释里标明「待与 Qlib 对齐」（同 P2-3 B 段）。
3. ML 分接回 cockpit `ml` 维后，`dimension_coverage` 回到 1.0，且 **A 段的披露自动闭嘴**（说明 A 段做对了）。
4. ML 预测进 P0-1 后验闭环，每日链有对应步骤。
5. 打分路径**零写库**（雷已拆）。
6. **前端收敛到 7 页**：模型相关只剩「模型实验室」一处入口，`Prediction.tsx` / `FineTune.tsx` 均已删除，工具组 `backtest_ml` 已拆开（§6）。

---

## 4. 🪙 crypto 兼容（2026-07-27 重设计｜**A 段 = ① · B 段 = ②**）

> 先读 `00-PLAN.md` §4b 通用口径。

### 🔴 A 段：披露机制要覆盖**两个 scorer**，不是一个（重设计新增的关键点）

原先写的是「A 段与市场无关，披露逻辑通用」—— **一半对一半错**。逻辑确实通用，但**落点有两处**：

| scorer | 位置 | 有 `dimension_coverage` 吗 |
|---|---|---|
| 股票 cockpit | `cockpit_engine/scorer.py:103` | ✅ |
| **crypto cockpit** | `crypto_intel_engine/scorer.py:602` | ✅ **同款已有** |

**两个 scorer 各写了一遍同一套「缺维 → 重新归一化 → 记 coverage」的逻辑。** A 段若只接股票那条，crypto 侧的缺维披露照样是哑巴。

而且 **crypto 更常缺维**：五维里的 `flow`（资金流）和 `sentiment`（新闻事件）都依赖外部免费源，取数失败是常态 —— `scorer.py:380/438` 专门为此写了 `weight_covered` 的部分覆盖机制。**crypto 的 coverage < 1.0 出现频率比股票高。**

> ✅ **一个好消息**：crypto 侧已经把 `dimension_coverage` / `weights_used` / `available_dimensions` 存进 `DecisionLog.input_snapshot` 了（`agents/tools/crypto_tools.py:178-183`）。**数据早就有，还是那句话 —— 病在这份诚实没传到 MoneyBill 嘴上。**

**A 段落点因此 +1**：`crypto_tools.py` 的 `analyze_crypto` 返回值也要把缺席维度名单往上带。

### B 段：crypto 横截面**能不能做，先验样本量**

| 项 | A 股 | crypto |
|---|---|---|
| 标的数 | **5201** | **460**（实测 `daily_quotes` distinct symbol） |
| 面板行数 | ≈ 452 万 | 数量级小两档 |
| 行业分类 | 有 | **无** |
| 单一标的主导 | 无 | **BTC 主导度极高**，多数币是 BTC 的 beta |

**「未来 N 日相对市场中位数的超额收益」这个标签在 crypto 上要重新想**：460 个高度相关的标的，中位数很大程度就是 BTC 走势，减掉它之后剩下的残差信噪比有多少 —— **没验过，别想当然。**

🔗 **与 [[P2-3]] B 段是同一个问题**：那张卡的 IC/IR 横截面口径面对的是**一模一样**的三个障碍（样本量/无行业/BTC 主导）。**验一次，两张卡共用结论。**

### 其他 crypto 差异

- **7×24 无交易日历** → 「未来 N 日」= N 个自然日；walk-forward 的「每季度」在 crypto 上要重新定期界
- **无财报** → B 段的「财报按 report_date + 45 天对齐」这条**整条不适用**，crypto 的对应物是链上数据（TVL/解锁表，见 [[P2-1]] 的 crypto 章节）
- **⛔ 别拿 A 股的做法照搬** —— `00-PLAN.md` §4b.4 反模式 3：每次用之前先验样本量

---

## 5. 落点

| 类型 | 文件 | 要做什么 |
|---|---|---|
| **A 段** | | |
| 修改 | `backend/agents/tools/analysis_tools.py` | cockpit 工具返回值把 `dimension_coverage` + 缺席维度名单往上带 |
| 修改 | `backend/agents/`（收尾/披露处，参考 P0-2 的「系统更正」机制） | coverage < 1.0 时强制追加披露。**抄 P0-2 那套硬传导，别靠 prompt 自觉** |
| 新增 | `backend/tests/` | 门禁：coverage < 1.0 必然出现披露 |
| **B 段** | | |
| 抄的模式 | `backend/prediction_engine/validator.py` | 后验回填 + 分桶校准**自家已有**，直接复用（P0-3 就是抄的它） |
| 抄的模式 | `backend/alpha_lab/walk_forward*` | 滚动训练/评估的既有骨架，先勘察能不能直接复用 |
| 重写 | `backend/prediction_engine/features.py` | 剔除所有非平稳绝对量；财报滞后对齐 |
| 重写 | `backend/prediction_engine/engine.py` | 每票一模型 → 单一横截面模型 |
| 删除 | `backend/prediction_engine/models/lstm_model.py`、`ensemble.py`、`remote_predict.py` | 砍 LSTM + 远程 GPU 桥（服务器已不可用） |
| 修改 | `backend/cockpit_engine/aggregator.py` | `_score_ml` 改读横截面分；**拆掉打分即写库的雷** |
| 修改 | `backend/data_engine/daily_pipeline_scheduler.py` | 每日链加 ML 预测 + 后验回填步骤 |
| 修改 | `backend/agents/tool_groups.py` | `backtest_ml` 拆成 `backtest` + `ml_models`；`PAGE_GROUPS` 同步（§6.5） |
| **B 段前端**（§6） | | |
| 新增 | `frontend/src/pages/ModelLab.tsx` | 「模型实验室」= Prediction 重写 + FineTune 搬入，两 Tab + 共用外壳 |
| 删除 | `frontend/src/pages/Prediction.tsx`（755 行）、`FineTune.tsx`（700 行） | 合并后删除。⚠️ **Prediction 是重写不是搬运**（§6.4） |
| 修改 | `frontend/src/components/shell/MainStage.tsx`、`ToolsDrawer.tsx` | 8 页 → 7 页；路径统一 `/app/models`；图标去歧义 |

---

## 6. 前端重构：模型相关全部收进一个页（Jason 2026-07-27 拍板）

> **原话**：「把模型相关的工具放在一个界面，比如融合进模型微调界面，不要放在策略回测界面。同时优化一下界面结构，让界面清晰简洁。」

### 6.1 现状盘点（2026-07-27 实测）

**8 页导航**（`components/shell/ToolsDrawer.tsx:15`）：

| 分组 | 页面 |
|---|---|
| 分析工作台 | K线分析 📈 `/app/market` · 策略回测 🔬 `/app/backtest` · **股价预测 🔮 `/app/prediction`** · 订单簿 📊 `/app/orderbook` |
| 运行与高级 | 数据监控 🛰️ `/app/data-monitor` · 自动化交易 🚦 `/trading` · **模型微调 🧪 `/fine-tune`** · 设置 ⚙️ `/app/settings` |

**四处结构问题**：

1. 🔴 **两个模型页分散在两个不同分组**：`Prediction.tsx`（755 行，ML 涨跌预测）在「分析工作台」，`FineTune.tsx`（700 行，LLM 微调）在「运行与高级」。**它俩结构惊人地相似**——都是「配置训练 → 看进度 → 看评估报告 → 看日志」，却各写了一遍。
2. 🔴 **后端工具组把回测和 ML 绑死了**（`agents/tool_groups.py:38`）：`"backtest_ml": "策略回测(C++ 引擎) + ML 涨跌预测"`，且 `PAGE_GROUPS` 里 `/app/backtest` 和 `/app/prediction` **都预载这同一组**。这就是「模型的东西放在策略回测那边」的后端体现。
3. 🟡 **路径前缀不一致**：6 个页在 `/app/*`，`/trading` 和 `/fine-tune` 两个裸路径是历史遗留。
4. 🟡 **图标语义打架**：🔬（策略回测）和 🧪（模型微调）都是实验器材，扫一眼分不清谁是谁。

### 6.2 目标结构：8 页 → 7 页

| 分组 | 页面 |
|---|---|
| 分析工作台 | K线分析 📈 · 策略回测 🔬 · 订单簿 📊 |
| 运行与高级 | 数据监控 🛰️ · 自动化交易 🚦 · **模型实验室 🧪 `/app/models`** · 设置 ⚙️ |

**「模型实验室」= `Prediction` + `FineTune` 合并**，路径统一到 `/app/models`。

⚠️ **这不违反「不开新页面」铁律，反而是在执行它**（见 [[architecture-convergence]]）：净减一页、两处重复的训练台合成一处。铁律禁的是「为新能力开新页」，不是「合并已有页」。

### 6.3 Tab 设计：按模型类型分，不按操作分

两类模型的训练配置差异太大（GBDT 超参 vs LoRA 参数），硬塞进同一个「训练」Tab 只会更乱。

**Tab 1｜选股模型**（横截面 GBDT，B 段产物）
| 子区 | 内容 | 相对现状 |
|---|---|---|
| 训练 | 时间窗 + 超参 + walk-forward 触发 | 🔴 **不再选 symbol** —— 每票一模型已废 |
| 排序榜 | 全市场当期打分 Top/Bottom + 分组分布 | 🔴 **替代**原「预测分析」的单票价格预测图 |
| 评估 | IC / IR / 分组超额 + P0-1 后验命中率 | 🔴 **替代**原「预测验证」的准确率统计 |

**Tab 2｜LLM 微调**：现有 `FineTune.tsx` 内容原样搬（训练配置 / Loss 曲线 / 评估报告 / 训练日志）。

**共用外壳**：模型清单、训练任务状态、日志区——这三块两页现在各写了一遍，合并后共用一套。

### 6.4 🔴 关键认知：Prediction.tsx 是「重写后并入」，不是「搬过去」

755 行里**大部分会因为 B 段的技术选型而直接失效**：

| 现有内容 | B 段后 |
|---|---|
| Tab1 训练配置里选股票 | ❌ 废（横截面模型没有「训哪只票」这回事） |
| Tab2 单票价格预测折线图 | ❌ 废（砍了 LSTM 就没有 `predicted_prices`） |
| 远程 GPU 训练的进度/控制 UI | ❌ 废（`deepoptica` 已不可用，`remote_predict.py` 一并删） |
| Tab3 准确率/校准统计 | ♻️ 保留骨架，指标换成 IC/IR/分组超额 |

**别在 B 段之前先搬一遍 UI** —— 搬完就得推倒重写。

### 6.5 后端工具组同步拆分

```
backtest_ml  →  backtest    "策略回测(C++ 引擎)"
             →  ml_models   "选股模型打分/训练/评估"
```

`PAGE_GROUPS` 相应改成 `/app/backtest → backtest`、`/app/models → ml_models`，删掉 `/app/prediction`。

⛔ **新增/改名工具必须归组**，否则首次会话直接 RuntimeError（见 [[moneybill-token-optimization]]）。

### 6.6 时序：前端重构整体归 B 段

技术上「合并两页 + 统一路径 + 拆工具组」可以独立先做，**但没意义**——合并完 B 段又要重写 Tab 1 的全部内容，等于跑两趟 `restart.sh` 做同一件事。

**A 段不碰前端**（纯披露，改的是 MoneyBill 的话术）。

### 6.7 迁移注意

- 旧路径 `/app/prediction`、`/fine-tune` 要么保留重定向、要么确认 **agent 的 navigate 工具**不会再指向它们（`MainStage.tsx:43` 对未知路径会兜底回聊天，不会白屏，但 agent 导航过去等于什么也没发生）
- 改完前端**必须 `bash restart.sh` 全量**（App 端无热更新，前端产物编译进二进制）

---

## 7. Jason 已裁决（2026-07-27，别再重问）

1. ✅ **`Prediction.tsx` 怎么办** → **合并进「模型实验室」**，模型相关的东西全部收在一个页，不要放策略回测那边；同时精简导航结构。详见 §6。
2. ✅ **B 段排期锚点** → **等组合模块阶段 1 用规则跑通后再替换排序源**。先让组合能跑，再换更好的排序源，避免两个大件同时不稳定。
3. ✅ **A 段立刻做 / B 段绑组合模块**（§2、§3 已记）。
4. ✅ **算力**：远程 GPU 已不可用 → 砍 LSTM 只做 GBDT，本机 CPU 跑（§1）。

## 8. 当前状态

**📋 计划阶段，未开工**（Jason 2026-07-27：「先不开工，先完善计划方案」）。

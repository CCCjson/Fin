---
id: P0-4
title: 决策留痕卫生（DecisionLog 被执行记录污染 + entry_price 定时炸弹）
size: 小
depends: 无（但**卡住 P0-1/P0-3 真正产出可信数字**）
paths_verified: 2026-07-27
status: 🔴 计划阶段未开工 —— 本卡由 crypto 兼容重设计过程中查生产库发现
---

# P0-4 决策留痕卫生

> **一句话**：`decision_logs` 里有 **39 行 `BTCUSDT.BN BUY entry_price=100.0`**（BTC 真实价 6 万+）。它们现在因为 bar 不够全是 `unable` 所以没出事，**等 bar 攒够就会被评成 +64900% 的 win**，占全表 40%，直接灌进 P0-1 的胜率和 P0-3 的校准系数。
>
> **P0 闭环的代码是通的，但它的输入没人洗过。** 这张卡就是洗输入。

## 0. 实锤（2026-07-27 查生产库 `backend/data/market.db`，全部当场验过）

```
decision_logs 总行数 = 98

source 分布：
  crypto                45   ← 46%，最大来源
  moneybill             22
  report_picks          10
  crypto_cockpit         8
  moneybill_recommend    8
  advisor                3
  cockpit                2

outcome_status 分布：
  unable    67   pending  17   (未评) 14   completed  0   ← 一条都没评出来过
```

### 🔴 问题 1：`source="crypto"` 的 45 条**根本不是建议，是订单执行记录**

| 写入点 | 写的是什么 |
|---|---|
| `crypto_intel_engine/execution.py:389`（`record_crypto_trade`） | **成交回执**：`entry_price=成交价`，`output_text="币安现货成交 order=..."` |
| `crypto_intel_engine/execution.py:411`（`record_crypto_resting`） | **挂单回执**：`entry_price=挂单价`，`output_text="币安限价单挂出（未成交）"` |

**P0-1 评的是「AI 预测得对不对」，这两条写的是「订单发生了什么」——语义完全不同的东西共用了一张表。**

「我以 6.5 万挂单买了 BTC」这件事没有「对错」可评，它是**已发生的事实**，不是预测。拿它去算胜率，等于问「这笔成交的准确率是多少」。

### 🔴 问题 2：39 条 `entry_price=100.0` 的 BTC 买入（定时炸弹）

```
id=50..104  BTCUSDT.BN  BUY  entry=100.0  sl=None  tp=None  src=crypto
时间集中在 2026-07-22（23 条）与 2026-07-24（16 条）
其中 07-24 01:08:55 同一秒 5 条
```

BTC 同期真实价约 **6.1~6.5 万**。`entry_price=100.0` 只可能是：挂了个永远不会成交的测试限价单，或把「100 USDT 金额」传进了 price 参数。**无论哪种，它都不是一个可评的建议价。**

**引爆条件**：`outcome_eval.evaluate_single` 只要拿到 ≥5 根 bar 就会算
`(未来价 − entry) / entry = (65000 − 100) / 100 ≈ **+64900%**` → `_label()` 判 **win**。

**引爆后果**（这才是真正严重的地方）：

1. `get_decision_stats(source="crypto")` 胜率直奔 100%
2. **P0-3 的校准被架空** —— `compute_calibration` 只下调不上抬，看到 100% 命中率 → `calibration_factor` 恒为 1.0 → 「历史命中率反哺置信度」这个机制**对 crypto 永久失效，且不会报错**
3. 这 39 条占全表 40%，**跨 source 的任何聚合统计都被带偏**

> 同款教训：[[P1-6]] §3 的「打分即写库」雷 —— **现在 0 行是因为压根没模型，模型一有就炸**。本卡是同一类病的**已经装填版**：数据已经在库里，只等 bar 攒够。

### 🟡 问题 3：`action` 大小写不统一

`Counter({'BUY': 69, None: 14, 'HOLD': 7, 'buy': 6, 'SELL': 2})`

`outcome_eval` 内部做了 `.strip().upper()` 所以**评估侧不受影响**（这是对的）。但任何**按 `action` 字段直接 GROUP BY 的统计**都会把 `BUY`/`buy` 劈成两组。

### 🟡 问题 4：completed = 0（98 条一条没评出来）

| unable_reason | 条数 | 性质 |
|---|---|---|
| `insufficient_bars` | 30 | ✅ 可重试，等 bar 攒够自然补齐 |
| `no_quotes` | 16 | ✅ 可重试（回填当时还没有次日 bar） |
| `no_action` | 14 | ❌ 不可重试 —— advisor/moneybill 压根没记方向 |
| `action_not_directional` | 5 | ❌ HOLD 无方向，设计如此 |
| `no_entry_price` | 2 | ❌ |

**这本身不是 bug**（`00-PLAN.md` §3 已说明「闭环通了但数据要养」）。列在这里是因为：**问题 2 的引爆时刻就藏在这 30 条 `insufficient_bars` 里** —— 它们正在倒计时。

---

## 1. 要做什么

### 第 1 步｜止血：把执行记录从「可评建议」里摘出去（**最优先**）

「订单执行留痕」这个需求是**合理的**（Jason 要能回溯下过哪些单），错的是**它和 AI 建议共用同一张表 + 同一个评估管道**。

三个方案，**倾向 A**：

| 方案 | 做法 | 评价 |
|---|---|---|
| **A（倾向）** | `DecisionLog` 加 `entry_kind` 字段（`advice` \| `execution`），执行留痕写 `execution`；`backfill_outcomes` 的 `unfinished` 条件**只捞 `advice`** | 改动最小、语义最清晰、历史数据可回填；执行记录仍可查可审计，只是不进胜率 |
| B | 执行留痕改用 `source="crypto_exec"` 等独立 source，评估侧维护一张 source 白名单 | 靠命名约定，**下一个新 source 又会忘**（这次就是这么出的问题） |
| C | 执行记录整个搬去别的表 | 最干净但改动最大，且要迁历史数据 |

⛔ **不许做的**：直接删掉 `execution.py` 里那两处 `record_decision`。那会**丢掉订单审计线索**，Jason 要的「下过哪些单」就没了。**问题是分类，不是留痕本身。**

### 第 2 步｜洗历史：39 条 entry=100 的处置

**不能简单 `DELETE`** —— 它们是 Jason 07-22/07-24 实测下单流程的真实痕迹，删了就没了。

按第 1 步的方案 A，它们会自动被标成 `entry_kind='execution'` → 不进评估 → **炸弹自动拆除，数据一行不丢**。这是倾向 A 的主要理由。

**另外补一道独立防线**（防的是将来别的来源再塞脏价格进来）：
`record_decision` 里加**入库期离谱值检查** —— `entry_price` 与该 symbol 当日收盘价偏离超过某个倍数（建议 10 倍）时，**拒绝写入 entry_price 并记 warning**，而不是照单全收。

> ⚠️ 这条要谨慎：crypto 有真实的极端行情，阈值定太紧会误伤。**建议只 warning + 打标，不静默丢弃**。

### 第 3 步｜统一 action 大小写

写入期 `record_decision` 里 `action = action.strip().upper()`。历史数据一条 UPDATE 收口。

### 第 4 步｜门禁

1. `entry_kind='execution'` 的行**永不出现在** `backfill_outcomes` 的候选集里
2. `record_decision` 写入的 `action` **必然是大写**
3. 离谱 `entry_price` 会被打标（拿 entry=100 的 BTC 当固定用例）

---

## 2. 🪙 crypto 兼容（分类 ④：已完工但 crypto 实况未验）

**本卡本身就是 crypto 重设计的产物** —— 它是「P0 三张卡当 crypto 不存在，而 crypto 是 DecisionLog 的 54%」这个事实的第一个后果。

按 `00-PLAN.md` §4b.3 的通用口径，本卡额外要注意：

- **`no_quotes` 在 crypto 上恢复得比股票快**（7×24 每天出 bar，5 根只要 5 天；A 股要 1 周）。所以 **crypto 的炸弹比股票的先引爆**。
- **胜率必须按 source 分桶**（`get_calibration_factor("crypto_cockpit")` 已经是对的），但**跨 source 的总览统计目前没有分桶**，问题 2 引爆后会带偏它。
- 20 日窗口在 crypto = 20 自然日、在 A 股 ≈ 4 周 —— **两者的「20 日胜率」不是同一个东西**，任何并排展示都要标注。

---

## 3. 落点

| 类型 | 文件 | 要做什么 |
|---|---|---|
| 修改 | `backend/data_engine/storage/models.py`（`DecisionLog`） | 加 `entry_kind` 字段（默认 `advice`） |
| 修改 | `backend/decision_log.py:114`（`record_decision`） | 收 `entry_kind` 入参；`action` 强制大写；`entry_price` 离谱值打标 |
| 修改 | `backend/decision_log.py:338`（`backfill_outcomes`） | `unfinished` 条件加 `entry_kind == 'advice'` |
| 修改 | `backend/crypto_intel_engine/execution.py:389, 411` | 两处传 `entry_kind="execution"` |
| 新增 | `backend/scripts/`（一次性迁移） | 历史数据回填 `entry_kind` + action 大写归一 |
| 新增 | `backend/tests/` | 四条门禁（见第 4 步） |

## 4. 为什么这张卡值得插在 P1 之前

P0 三项的价值**全部依赖 DecisionLog 是干净的**：

```
P0-1 后验评估  ← 输入是 DecisionLog
P0-3 校准反哺  ← 输入是 P0-1 的胜率
```

输入脏 → 后面两层算得再对也是错的，**而且是「看起来很正常」的错**（100% 胜率不会报错，只会让人更信 AI）。

现在动手成本极低（98 行数据、炸弹还没引爆）；等评出来再修就要**同时修代码 + 洗已经进了校准的历史结果**。

---
id: P0-4
title: 决策留痕卫生（DecisionLog 被执行记录污染 + entry_price 定时炸弹）
size: 小
depends: 无（但**卡住 P0-1/P0-3 真正产出可信数字**）
paths_verified: 2026-07-27
status: 🟢 批次1（止血）+ 批次2（防复发）已完工 2026-07-27 —— 炸弹已拆且路已焊死；**批次3 已拆分迁出 → `docs/15.策略竞技场/` 的 S0（查询侧）与 S4（写入侧）**，本卡到此收尾
---

# P0-4 决策留痕卫生

> ## ⚠️ 实施偏离 / 探查结论（**动这张卡剩下两个批次前必读**）
>
> 2026-07-27 开工时先查了生产库与源码，**推翻了本卡两处前提**，方案随之调整：
>
> ### 1. 🔴 那 39 条不是 Jason 的实测痕迹，**是测试写进生产库的**
> 下方第 2 步原文写着「它们是 Jason 07-22/07-24 实测下单流程的真实痕迹，删了就没了」——**错的**。
> 40 条脏行（39 成交 + 1 市价零成交）的 `output_text` 全带 `order=BTCUSDT.BN:999`，
> 这个 order_id **只存在于** `tests/test_crypto_strategy_engine.py:199` 的 `_FakeOrder`，
> 价 100.0 / 量 0.5 / 手续费 0.05 与 `test_confirm_executes_and_records` 逐字对得上。
> 泄漏机制、结构性堵法、两个坑 → `docs/GOTCHAS.md`「测试写进生产库」。
> **处置**：Jason 拍板删、不备份 → `backend/scripts/purge_test_residue_decisions.py`，已执行（99 → 58 行）。
>
> ### 2. 🔴 「crypto 占 decision_logs 54%」这个结论本身是污染的产物
> 53 条 crypto 里 40 条是测试残留 → **真实 crypto 留痕只有 13 条（5 execution + 8 crypto_cockpit）= 22%**。
> `00-PLAN.md` §4b.1 那张表已同步更正。
>
> ### 3. 分类改成**三值**，且 `confirm_gate` 也在治理范围内（本卡原文只点了 execution.py 两处）
> `agents/confirm_gate.py:104`（`source=moneybill`，22 行 = 当时全表 22%）记的是「每一次经确认的
> 工具调用」——加自选股 / 建预警 / 编策略 / 下单，**没有一条是预测**。而且下单那几条与
> `execution.py` 的回执是**同一笔单的双重留痕**（实测 id 42/43 是同一秒同一张 ETH 挂单）。
> Jason 拍板三值 `advice | execution | ops`：真下单/补录成交 → `execution`，其余受确认门的工具 → `ops`。
> 判定表 `common/decision_kind.py::CONFIRMED_TOOL_KINDS`，新增确认门工具漏登记有门禁咬。
>
> ### 4. 本卡漏了一处：**只堵 `backfill_outcomes` 不够**
> 回执的 `outcome_status` 恒为 NULL，会被 `get_decision_stats` 的 `total` / `pending` 全额计入 ——
> 分母照样脏。查询侧（`query_decisions` / `get_decision_stats`）默认也改成只看 `advice`。
>
> ### 5. 第 2 步的「入库期离谱值检查」→ **改到评估期，推迟到批次2**
> 写入期做要在留痕热路径上加一次 DB 查询才拿得到参考价；评估期做是**纯函数、可测、
> 能追溯已入库的历史行**，且天然被 `ENGINE_VERSION` 背书。批次2 落 `common/outcome_eval.py`，
> 判定改动需 bump `ENGINE_VERSION` → v2。
>
> ### 6. 第 3 步 action 大写归一 → **推迟到批次2**（它不是炸弹，是统计口径瑕疵）

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

✅ 已落 `tests/test_decision_entry_kind.py`（13 条）：

1. `execution`/`ops` 的行**永不出现在** `backfill_outcomes` 的候选集里
2. `entry_kind IS NULL` 视同 advice（与 `normalize()` 同口径 —— 宁可多评一条噪声，也不能把真建议**静默**排除在评估外）
3. **那颗具体的炸弹**：entry=100 的 BTC + 6.5 万的 bar → 不被评；同时用对照行证明「若当成 advice 就是一条 +64900% 的 win」（对照组失效也会红，防这条测试哪天测了个空气）
4. 胜率**分母**里没有 execution/ops，但显式传 `entry_kind=` 仍查得到（审计线索一条不丢）
5. 校准样本不含回执（纵深防御）
6. **每个 `requires_confirmation=True` 的工具都已归类**，新增漏登记 → 红。⚠️ 判定读**源码 AST 不读运行期 `REGISTRY`**：别的测试会往全局 REGISTRY 里塞假工具，读 REGISTRY 会「单跑绿、全套红」
7. 全项目 `record_decision(` 写入点必须在 `_WRITE_SITES` 表里；非 advice 的写入点必须**显式**传 `entry_kind=`（不许靠默认值兜底）
8. 存量归类迁移幂等 + **永不覆盖**已显式标好的值。⚠️ 造「存量行」必须用裸 SQL 打回 NULL：ORM 的 `default="advice"` 对 `entry_kind=None` 也会生效（SQLAlchemy 把 None 当「没给值」）

⏳ 批次2 再加：`action` 必然大写、离谱 `entry_price` 被打标。

---

## 2. 🪙 crypto 兼容（分类 ④：已完工但 crypto 实况未验）

**本卡本身就是 crypto 重设计的产物** —— 它是「P0 三张卡当 crypto 不存在，而 crypto 是 DecisionLog 的 54%」这个事实的第一个后果。

按 `00-PLAN.md` §4b.3 的通用口径，本卡额外要注意：

- **`no_quotes` 在 crypto 上恢复得比股票快**（7×24 每天出 bar，5 根只要 5 天；A 股要 1 周）。所以 **crypto 的炸弹比股票的先引爆**。
- **胜率必须按 source 分桶**（`get_calibration_factor("crypto_cockpit")` 已经是对的），但**跨 source 的总览统计目前没有分桶**，问题 2 引爆后会带偏它。
- 20 日窗口在 crypto = 20 自然日、在 A 股 ≈ 4 周 —— **两者的「20 日胜率」不是同一个东西**，任何并排展示都要标注。

---

## 3. 落点

### ✅ 批次1（止血）已完工 2026-07-27

| 类型 | 文件 | 做了什么 |
|---|---|---|
| 新增 | `backend/common/decision_kind.py` | **三值真源**：`advice/execution/ops` + `CONFIRMED_TOOL_KINDS` 判定表 + `normalize()`。已进 mypy 强检名单 |
| 修改 | `data_engine/storage/models.py`（`DecisionLog`） | 加 `entry_kind`（默认 `advice`） |
| 修改 | `data_engine/storage/database.py`（`init_db`） | `ALTER TABLE` + **存量按 source 一次性归类**（`WHERE entry_kind IS NULL` → 幂等且永不覆盖已标好的值） |
| 修改 | `decision_log.py` | `record_decision` 收 `entry_kind`；`_IMMUTABLE_REFRESH_FIELDS` 纳入；`backfill_outcomes` 候选集 + **symbols 子查询**同步加；`query_decisions`/`get_decision_stats` 默认只看 advice；`compute_calibration` 加纵深防御；缺字段告警只对 advice 吼 |
| 修改 | `crypto_intel_engine/execution.py` | 成交/挂单 → `execution`，理财申购 → `ops` |
| 修改 | `agents/confirm_gate.py` | `entry_kind=kind_for_confirmed_tool(pending.name)` 运行期分流 |
| 新增 | `tests/conftest.py`（**根级**） | 结构性堵死「测试写生产库」：整体替换 engine/SessionLocal，逃生门 `FIN_TEST_USE_REAL_DB=1` |
| 新增 | `tests/test_decision_entry_kind.py` | 13 条门禁（见第 4 步） |
| 新增 | `scripts/purge_test_residue_decisions.py` | 一次性清 41 行测试残留，已执行 |

**生产实测**：`decision_logs` 99 → 58 行；`entry_kind` = advice 31 / execution 16 / ops 11；
`backfill_outcomes` 候选集从「全表」收敛到 **23 条（全部 advice）**；胜率分母 31（此前 98）。
全套 **1614 passed**，跑前跑后生产库行数不变。

### ✅ 批次2（防复发）已完工 2026-07-27

| 类型 | 文件 | 做了什么 |
|---|---|---|
| 修改 | `common/outcome_eval.py` | **离谱入场价守卫**：`ENTRY_PRICE_OUTLIER_RATIO=10.0`、`_reference_price` / `_is_entry_outlier`、新 unable 原因 `entry_price_outlier`（**不可重试**）；新增 `normalize_action()` 作为全项目 action 的唯一一把尺子；**`ENGINE_VERSION` → `decision-outcome-v2`** |
| 修改 | `decision_log.py` | `record_decision` 写入期归一 action（**单一收口点**，不改调用方）；`query_decisions` 的 action 入参也归一 |
| 修改 | `data_engine/storage/database.py` | 存量 action 大小写一次性 UPDATE（`WHERE action <> upper(trim(action))` 天然幂等） |
| 新增 | `scripts/reset_decision_outcomes_v2.py` | §C 两条：清非 advice 行的评估残留 + 趁 `completed=0` 整体重刷成 v2。已执行 |
| 新增 | `tests/common/test_outcome_eval.py` +9 条 | 炸弹固定用例 / 对照组 / 双向 / 边界严格 `>` / 顺序在 MIN_BARS 之前 / 0 bar 仍报 no_quotes / 参考价不可用则放行 / action 归一 ×2 |
| 新增 | `tests/test_decision_action_normalize.py`（9 条） | 写入期归一、查询入参归一、存量迁移幂等 + 不变式 |
| 修改 | `tests/test_decision_entry_kind.py` | 原「对照组」entry 从 100 调到 6500（否则被第二道防线拦掉 → 对照组失效却看不出来），并新增一条**纵深防御**测试：分类层被绕过时守卫仍咬得住 |

**三个关键设计取舍**（改这块前必读）：

1. **参考物取「首根 bar」而不是当日收盘价** —— 首根 bar 是调用方已切好递进来的，评估期因此仍是**纯函数、不查库**；且它是决策次日的价，与 entry 只隔一天，**任何合法资产隔夜都不可能偏离 10 倍**，阈值极其安全。
2. **守卫位置在 `MIN_BARS` 之前、`if not bars` 之后** —— 离谱价 1 根 bar 就判得出，放后面会先报 `insufficient_bars`（可重试）→ 每天被重扫，白等攒够 5 根；而 0 根 bar 时没有参考物，说「离谱」是猜，诚实地留在 `no_quotes` 里。
3. **阈值用严格 `>`** —— 10:1 拆股会让复权价与原始 entry 恰好差 10 倍整，不该误杀。反过来 20:1 这类会被标 outlier，而那种情况本来也算不出有意义的收益率。

**生产实测**（改完当场验）：全套 **1633 passed**（基线 1614 + 19 新增）；`init_db` 归一 6 行小写 action；重刷脚本清空 60 行的 12 个 outcome 列后按 v2 重评 33 条 → 待评 18 / 没法评 15；**`engine_version` 只剩 `decision-outcome-v2`**（v1 彻底消失）；execution/ops 的 **27 行残留评估戳清零**；`entry_price_outlier` 命中 **0 条**（如预期 —— 脏行批次1 已删干净，这道守卫是纯防复发不是止血）；action 大小写脏行 0。

> ⚠️ **`completed=0` 这个重刷窗口已经用掉了**。以后再 bump `ENGINE_VERSION`，不可重试的 unable 行不会被回填重扫 → 会长期混着两版；那时候重刷就要连「已经进过 P0-3 校准的历史结果」一起洗，得掂量。

### 🔀 批次3 已迁出（2026-07-27）→ `docs/15.策略竞技场/`

批次3 拆成读写两半，并入新主线（策略竞技场需要按 source 查胜率，把它挡在关键路径上了）：

| 去向 | 内容 |
|---|---|
| **doc15 `S0`**（查询侧，都在 `get_decision_history` 一个工具里，一次改完更经济） | `_SOURCES` 补全 + **建 source 真源 + 门禁** / 开 `entry_kind` 入参 / `exec_state` 派生 / `verbose` |
| **doc15 `S4`**（写入侧，要动写入点与数据模型） | 双重留痕收口 / **归因维度**（与「这笔成交属于哪条策略」合并成一套，别建两次） |

**开工前先读迁出时的四条探查实锤**（都当场验过，推翻了本卡原文的说法）：

1. 🔴 **双重留痕不能靠「删 crypto 侧」解决** —— `record_crypto_trade/resting` 有**两个调用方**：
   `crypto_tools.place_crypto_order`（走 confirm_gate → 双重）与 `crypto_strategy/pending.py:420,426`
   （**不走 confirm_gate** → 只有这一条）。删了 crypto 侧，半自动策略引擎的下单留痕直接断线。
   → 倾向方案：内层加 `log_decision` 开关，`crypto_tools` 传 `False`（confirm_gate 那条**信息更全**：
   带 session_id/turn_start_idx/model_id/data_quality/完整 input_snapshot），`pending.py` 保持 `True`。
   代价：crypto 行那句人话（「币安限价单挂出（未成交，盘口等待撮合）」）会丢 → 让
   `place_crypto_order` 的返回 `data` 带一个 `exec_note`，confirm_gate 记 `output_summary` 时自然带走。
2. 🔴 **`_SOURCES` 漏的是 4 个不是 3 个**，且它是**手写枚举靠人同步——与 entry_kind 那颗炸弹同款成因**。
   补完这次下一个新 source 照样会漏 → 必须建真源 + 门禁（见 S0）。
3. 🔴 **16 条 execution 里 6 条是「从没到达交易所的失败尝试」**（风控未通过 / `金额 4.97 低于最小名义额 5.0`）。
   真正到币安的挂单只有 **5 张**，成交 **0 条**。光把 execution 露出来，LLM 只会说
   「你有 16 笔未成交订单」——**而真相是 5 张**。→ 需要 `exec_state` 派生（`filled｜resting｜blocked`）。
4. 🔴 **`/decisions` 不只是页面不存在**：`api/routes/` 里**没有任何 route 暴露 DecisionLog**，
   `get_decision_history` 是唯一出口。所以它裁掉的 `input_snapshot`/`output_summary`/`output_text`/`reasons`
   **是永久看不见的** → 需要 `verbose`（限 `limit<=3`）。

## 4. 为什么这张卡值得插在 P1 之前

P0 三项的价值**全部依赖 DecisionLog 是干净的**：

```
P0-1 后验评估  ← 输入是 DecisionLog
P0-3 校准反哺  ← 输入是 P0-1 的胜率
```

输入脏 → 后面两层算得再对也是错的，**而且是「看起来很正常」的错**（100% 胜率不会报错，只会让人更信 AI）。

现在动手成本极低（98 行数据、炸弹还没引爆）；等评出来再修就要**同时修代码 + 洗已经进了校准的历史结果**。

# 决策日志

<!-- 只追加，不修改已有条目。要推翻某条决策，追加一条新的并注明取代了哪条。 -->
<!-- 理由和"被否决的方案"比结论更重要 —— 后面的 session 靠它们判断该不该推翻。 -->

## 格式

### YYYY-MM-DD — <决策标题>
- **决定**：
- **理由**：
- **否决了**：<方案，以及为什么不选>
- **影响范围**：

---

### 2026-08-03 — step-loop 跑在专用分支 `feat/s5-stock-arena`，不直推 master
- **决定**：本轮 PLAN 的全部任务在 `feat/s5-stock-arena` 上推进，每轮 commit 推该分支。
- **理由**：step-loop 每轮自动 commit + push，master 上出问题只能逐轮 revert；
  且这仓库常有 Jason 预先 staged 的在制品，直推 master 容易混进不属于本轮的改动。
- **否决了**：在 master 上跑（跟前几张 S 卡做法一致）—— 单轮粒度虽小，但 8 条任务里有两条
  是「必然停机」的大件（07 改 schema、08 重写实盘链路），那两条在 master 上滚起来风险不对等。
- **影响范围**：git 分支策略；合回 master 由 Jason 决定时机。

### 2026-08-03 — PLAN 优先级：先补「股票地基修好了但上层不认它」的半截工程
- **决定**：任务顺序 = 股票竞技场（01-04）→ 零碎欠债（05-06）→ 大件（07-08）；
  真券商接入**不进本轮 PLAN**。
- **理由**：S5 已建成 `strategy_trades` 台账和全自动执行链，但 `performance.py`/`arena.py`
  仍只读 `crypto_trades` → **股票策略进不了竞技场，四道门槛对它们等于不存在**。
  这是半截工程，放着越久后面接券商越难受。
- **否决了**：先做 08（crypto 引擎迁移）—— 它是最大的技术债，但它动的是**正在跑真钱**的链路，
  而 01-04 是纯读侧扩展、风险低得多，先做能让竞技场对股票立刻可用。
- **影响范围**：`crypto_strategy/performance.py`、`crypto_strategy/arena.py`、
  `strategy_runtime/ledger.py` 的读侧调用方。

### 2026-08-03 — `strategy_trades` 的 price/quantity/commission 改记「券商回报值」
- **决定**：`strategy_runtime/executor.py::_record` 从「记决策价 + 手续费 0」改成
  「记券商回报的 `filled_price` / `filled_quantity` / `commission`」，且**只有
  `FILLED` / `PARTIAL_FILLED` 才写台账**（`SUBMITTED` / `PENDING` 不写）。
- **理由**：股票战绩（任务 01）就是拿这张台账算的。按决策价 + 零手续费记账等于
  抹掉滑点和费用，偏差方向**恒为高估** —— 一条其实不赚钱的策略会因此拿到上位资格，
  而竞技场四道门槛正是靠这个数判切不切换。
- **否决了**：(a) 只改读侧、读的时候按费率补一个估算费用 —— 那是拿假数字盖住假数字，
  且与 A 股印花税、真券商阶梯佣金都对不上；(b) 连 `SUBMITTED` 一起记 —— 那是凭空
  捏造一笔可能永远不会发生的成交。
- **⚠️ 接缝**：改动前后的台账行**不同口径**（老行=决策价零费用，新行=成交价含费）。
  当前生产库里股票台账基本是空的（调度器刚上、只有 PaperBroker），影响可忽略；
  但跨这个时间点算战绩时要知道有这道缝。
- **🔴 欠债**：目前没有「成交回报回填台账」这一环，所以接真券商后**挂单成交不会自动
  补记**。接券商时必须一起补，否则限价单的战绩会系统性缺失。
- **影响范围**：`strategy_runtime/executor.py`、`strategy_trades` 表的数据语义。

### 2026-08-03 — 股票日收益序列按**交易日**补齐，不按自然日
- **决定**：`daily_returns` 的股票分支用 `trading_calendar.trading_days()` 补齐序列；
  日历盖不到的头尾用工作日启发式补上，并把 `calendar_confidence` 降级；
  **日历内部的空洞不补**。
- **理由**：crypto 7×24 才等价于自然日。股票按自然日补会凭空塞进 ~30% 的零，
  把 μ 和 σ 一起稀释，而门槛② 正好吃这两个数。头尾必须补是因为 `trading_days()`
  超出覆盖范围时**只返回盖住的那一段**（对缺口扫描是对的，对收益序列等于
  序列尾部悄悄少几天，而少掉的恰恰是最近、最该看的那几天）。
- **否决了**：(a) 沿用自然日补零 —— 见理由；(b) 把「工作日集合 − 日历集合」当缺行
  一并补上 —— 那会把**国庆七天**判成七个缺口，等于用更差的判据推翻更好的判据；
  中等空洞属于 `gap_engine` 的责任面，不在这一层解决。
- **⚠️ 三态 confidence**：`certain` / `partial`（日历滞后于收盘，常态）/ `suspected`
  （整段是启发式）。两态的话标记会**恒亮**，一个永远亮着的警告灯等于没有。
- **影响范围**：`crypto_strategy/performance.py`、arena 的 `_snapshot`。

### 2026-08-03 — 「口径可比」门槛加卡同市场（**止血，不是分族评比**）
- **决定**：`arena.evaluate_challenger` 的 `comparable` 门槛增加 `same_market`，
  且是 **fail-closed**（取不到市场就不放行）。
- **理由**：任务 02 让股票策略第一次能产出真实收益序列，而分母是**全局**的总资金设置、
  分子却是不同币种 —— 拿 A 股的 CNY 收益率和币的 USDT 收益率比大小没有意义，
  会给出跨市场的错结论。fail-closed 是因为「取不到值 → 当作满足」正是 S5 一路
  踩过来的失败模式（`_gates_snapshot` 手拼 snapshot 漏 `market`，`None == None`
  判过，把「四道门槛全过」的假记录写进了切换留痕）。
- **⛔ 这不是任务 03**：它没有决定「每个市场族一个卫冕者」，那要改裁决 7 的读法、
  要 Jason 拍板。已知副作用：一旦有股票策略 arm 成 live，crypto 侧挑战者会集体
  卡在 comparable、整个竞技场「无结论」—— 这是诚实降级，03 分族后自然解开。
- **顺带**：`arena.snapshot_of()` 成为 snapshot 的唯一出口，⛔ 别再手拼第二份。
- **影响范围**：`crypto_strategy/arena.py`、`crypto_strategy/service.py`。

### 2026-08-03 — 裁决 7 改读作「**每个市场**同期只有一条 live」（Jason 拍板）
- **决定**：卫冕者 / 挑战者 / 退位 / 冷却期**全部按 `market` 分族**。
  取代裁决 7 的字面读法（`docs/15.策略竞技场/00-PLAN.md:124`「同一时期只有一条 live」）——
  那句话写在项目只有 crypto 的时期，没有市场限定词。
- **理由**：裁决 6 否掉过「多条策略同时 live 各分资金」，但它否的是**同一市场内**
  多条策略抢同一批标的、还可能对同一标的下反向单。跨市场没有这个问题：标的不重叠、
  券商账户不同、风控 `broker_info` 本来就各取各的。所以这不是推翻裁决 6，
  是补一个当时不存在的维度。
- **否决了**：(a) 字面执行 —— 等于「同一时间只能自动交易一个市场」，
  股票竞技场做出来只能看不能用；(b) 分族评比但 live 名额全局唯一 ——
  多一个「族内冠军 ≠ 在跑的那条」的概念要解释，且没解决根本问题。
- **影响范围**：`arena.evaluate_arena` / `_days_since_last_switch`、
  `service._supersede_siblings` / `_current_live`。
  🔴 判定层和**退位逻辑必须一起改** —— 只改判定层的话「每市场一个卫冕者」在数据层立不住。

### 2026-08-03 — 总资金 = **各市场资金之和**（Jason 拍板，方向已定，实施单列）
> 🔄 **本条已于 2026-08-04 被推翻**，见本文件最后一条「「总资金」这个概念整个退役」。
> ⛔ 别照这条做：**不做求和值**（各市场是 CNY/HKD/USD/USDT，相加是混币种）。
> 下面的内容原样保留只为留痕当时的理由。
- **决定**：每个市场有自己的资金基数，全局 `total_capital` 是它们的**和**（不是共用一个数）。
- **理由**：上一条让两个市场可以同时跑真钱，而仓位大小和收益率分母目前都取同一个
  全局 `UserSettings.total_capital`（默认 5000）—— 两边同时 live 时总敞口会翻倍。
- **⚠️ 实施不在任务 03 里**：`get_total_capital()` 被风控规则、仓位换算、crypto 引擎
  多处消费，动它属于「影响 3 个以上文件 + 涉及仓位上限」的大改动
  （memory `risk-total-position-floor`：动任何仓位上限前必读）。已作为独立任务
  排进 PLAN，在 03 之后立刻做。
- **影响范围**：`trading_engine/risk/adapter.py`、`UserSettings`、
  `crypto_strategy/performance._capital_basis`、`strategy_runtime/scheduler`。

### 2026-08-04 — 任务 09 拆成 09a/09b/09c；纸面账户先按市场分池（Jason 批准）
- **决定**：原任务 09（迁风控那批 + 删 `get_total_capital()`）拆三轮，本轮只做 **09a**：
  `get_paper_broker(market)` 按市场各开一个 `PaperBroker`，初始现金 = 该市场本金，
  未配置抛 `MarketCapitalNotConfiguredError`（fail-closed）。
- **理由**：勘察实测 `get_total_capital()` 有 **11 个消费方**（不是任务卡预估的 5 文件内），
  且风控那批四处里**三处要改语义而不是换函数**：
  (a) `build_broker_info()` 的 `cash = 本金 − 全部持仓成本`，而 `PortfolioCalculator`
  **完全不分市场** —— 分子换成 A 股本金、分母仍含港美股，算出的 `cash` 直接喂硬风控；
  (b) `get_paper_broker()` 是全局单例、一个现金池，却同时服务 A 股和美股两条策略线；
  (c) `max_daily_loss = capital × 3%` 是**建 RiskManager 时烤进去的**，而
  `position_sizing._risk_managers` 只按 pct 缓存 —— 不改键的话第一个市场的限额
  会被第二个市场静默复用。
  09a 是三者里唯一**不触碰仓位上限语义**的，所以先做。
- **否决了**：(a) 一轮做完 11 个文件 —— 单次 diff 同时覆盖硬风控和正在跑真钱的
  crypto 路径，审核范围失控、出问题只能整轮回滚；(b) 只迁展示批、风控批留着全局值 ——
  `get_total_capital()` 删不掉，且最咬人的部分被推到以后。
- **⚠️ 诚实记一笔**：09a **并不能**让「股票 BUY 成片 blocked_risk」消失。
  真凶是 `stock_adapter.broker_info()` → 无参 `build_broker_info()` → 全局 5000，
  那是 09b。09a 修的是「broker 自己的现金池是混币种共用的」这个独立问题 ——
  必要但不充分。
- **🔴 09b 开工前必须先拿到的答复**：分市场之后「总持仓 ≤ 80% / 留 20% 现金」
  是**每个市场各留自己本金的 20%**，还是仍按某种全局口径判？倾向前者
  （与「每市场独立本金、不折算汇率」一致，且后者需要一个已被退役的跨币种总量），
  但这是硬风控红线的作用域，得 Jason 点头。
- **⭐ 顺带修掉的**：日切 `settle_new_day()` 以前打在共享 broker 上 ——
  任一市场跨日会把**所有市场**的 T+1 持仓一起解冻。分池后自然按市场隔离。
- **影响范围**：`trading_engine/brokers/paper_broker.py`、`strategy_runtime/scheduler.py`、
  `agents/tools/trading_tools.py`、`tests/test_market_capital.py`。

### 2026-08-04 — 「总资金」这个概念**整个退役**，只留分市场本金（Jason 拍板）
- **决定**：三条一起拍的：
  1. **不要全局「总资金」**。`get_total_capital()` 逐步退役，代码一律用**分市场本金**。
     ⛔ 不做「总资金 = 各市场之和」的求和值 —— 各市场是 CNY/HKD/USD/USDT，
     求和就是混币种，而「每市场独立本金、不折算汇率」是投资组合模块已拍的板。
  2. **不自动迁移**：现有那个全局 5000 不往任何市场摊。四个市场必须在设置页**手填**，
     **未配置的市场 fail-closed**（策略不跑），⛔ 不许回落到某个全局值。
  3. **分三轮做**：本轮只做地基（配置键 + 读取 API + 设置页 + AI 只读黑名单），
     **一个消费方都不迁**；下一轮迁策略侧（股票调度器 / crypto 引擎 / 收益率分母）；
     风控那批（风控规则 / 仓位换算 / PaperBroker 初始资金）**单开一轮**并配门禁。
- **理由**：`get_total_capital()` **今天就是「A股口径的人民币」**（`crypto_strategy/engine.py:325`
  写死的注释），crypto 早就绕开它走 `capital_basis="real_total_value"`。
  把它改成混币种的和，会让 **14 个消费方同时变语义**，而其中有风控 ——
  该注释还记着一次实锤事故：口径混用导致 **live 策略每笔 BUY 恒被 blocked_risk，
  一单也下不出来**，且只在 run 日志里留痕。
- **否决了**：(a) 「和」只作展示 —— Jason 认为留着这个概念早晚还会有人拿它当分母；
  (b) 现有 5000 归 A 股、其余回落全局值 —— 回落就是把「未配置」和「配置成某个值」
  混成一件事，而 fail-closed 才看得见谁还没配。
- **⚠️ 必须一起做**：新键要加进 `agents/tools/settings_tools.py::_RISK_READONLY_KEYS`
  （那张表让 AI **永远不能**写资金/风控键）。漏了 AI 就能改本金了。
- **影响范围**：`trading_engine/risk/adapter.py`、`UserSettings` / `SETTINGS_SCHEMA`、
  设置页，以及后续两轮的 14 个消费方。

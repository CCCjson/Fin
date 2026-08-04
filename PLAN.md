# 实现计划 — S5 收尾 + 全部剩余欠债

> 2026-08-04 重写。前六条（01 / 02 / 03 / 03b / 03c-1 / 03c-2）已完成并推送，
> 内容见 `git log` 与 `DECISIONS.md`。本文件只列**还没做的**。

---

## 🔒 零、这份计划的硬规矩（每轮开工前读一遍）

Jason 2026-08-04：**「不要再过度开发、引入新的问题」**。下面几条是对这句话的翻译，
比任何一条任务都优先。

1. **一轮只做一条任务。** 看到相邻任务顺手就能做完时**仍然停手**。
2. 🔴 **审核的 SUGGEST 分两类，只做第一类**：
   - **会出错的**（写错了、会静默失效、会说假话、没有测试保护）→ 做；
   - **更优雅的**（抽象更干净、少一层重复、命名更好）→ **记进 `PROGRESS.md` 的
     「未解决」，本轮不做**。
   ⚠️ 前几轮的膨胀全来自把第二类也照单全收。**「PASS + 几条优雅型 SUGGEST」= 直接提交。**
3. **每条任务的改动文件数上限写在任务里**，超了就停机说明，别硬塞。
4. ⛔ **不新增页面 / route / 表**（架构铁律：新能力只做引擎 + 工具两层）。
   确有必要 → 停机问 Jason。
5. ⛔ **不顺手重构、不顺手改 crypto 现有行为**（它正在跑真钱，也是回归判定的基准）。
6. **大改动 → 写 `BLOCKED.md` 停机**：改 schema / 删文件 / 改已记录的决策 /
   动仓位上限 / 涉及密钥或真实下单 / 影响 3 个以上文件的重构。
7. 每轮结束：跑验收 + 全量 → `code-reviewer` 审核 → 提交 → 覆写 `PROGRESS.md`。

### 跑测试 / 提交的固定动作

```bash
cd backend && conda run -n quant python -m pytest <验收命令> -q   # 验收
cd backend && conda run -n quant python -m pytest tests/ -q       # 全量，必须从 backend/ 跑
gh auth switch --user CCCjson && git push https://github.com/CCCjson/Fin.git feat/s5-stock-arena
```

- ⚠️ `pytest` 不认 `--timeout=`；`conda run` 吞 stdout（调试加 `--no-capture-output`）；
  全量 1~5 分钟，会撞 600s 前台上限 → 用后台跑并重定向到文件再 tail。
- ⚠️ 改完后端要在 App 里生效必须 `bash restart.sh --backend`（App 无热更新）。
- ⚠️ **跑 `restart.sh` 期间别跑全量**（C++ 服务会短暂不可用，会假红一批）。
- ⚠️ **两条既有的假红，别去查**：
  - 单独跑 `test_crypto_strategy_engine.py + crypto_strategy/test_proposal_outcome.py`
    会红一条 `test_log_decision_switch_suppresses_the_duplicate_row`（跨文件顺序污染；
    把源码改动 stash 掉照样红，全量顺序下是绿的）；
  - `ruff check tests/` 会报 `test_crypto_cost_basis.py` 的一条 N805。

---

## A. 先收缝（不做会立刻咬人）

- [ ] **09（原 03c-3）迁风控那批 + 删 `get_total_capital()`** ｜ 改动 ≤ 5 文件
      验收：`pytest tests/test_market_capital.py tests/test_risk_total_position_floor.py -q`
      做什么：把最后几个消费方从 `get_total_capital()` 换成 `get_market_capital(market)`，
      然后**删掉 `get_total_capital()`**（删之前它必须一个消费方都不剩）。
      消费方：`trading_engine/risk/manager.py:62`、`trading_engine/position_sizing.py:74`、
      `trading_engine/risk/adapter.py:173`、`trading_engine/brokers/paper_broker.py:21`，
      外加只读展示那批（`recommend_engine` / `screener_engine` / `cockpit_engine` /
      `agents/tools/trading_tools.py` / `api/routes/screener.py` / `automation/price_alert_monitor.py`）。
      要点：
      - 🔴 **必读 memory `risk-total-position-floor`**：`max(总仓位上限, 单股上限)` 会
        **静默撤销 20% 现金保护**，那个坑被复制过三份，有 AST 门禁抓第四份。
      - 🔴 **03c-2 留下的接缝，这轮必须收**：`paper_broker.py` 的初始现金还是全局 5000，
        而仓位换算已按分市场本金算 —— Jason 填了 `capital_a_share=100000` 之后
        股票 BUY 会成片「预算不足 / blocked_risk」。**别把它当策略 bug 查。**
      - 展示类消费方**取不到本金时不许编一个数**，显示「未配置」即可。
      - ⚠️ 消费方多但每处都只是换个函数 —— 如果发现某处需要**改逻辑**才能换，
        那处停下来单列，别在这一轮里顺手改。

## B. 数据真伪（不做的话战绩是假的）

- [ ] **10 股票没接真券商时，不许 arm 到 live** ｜ 改动 ≤ 3 文件
      验收：`pytest tests/test_stock_scheduler_and_ledger.py tests/crypto_strategy/test_versioning.py -q`
      做什么：`crypto_strategy/service.py::arm` 对**股票市场**加一道 fail-closed 拦截：
      真券商未接入时拒绝 `mode=live`，话术直说「股票还没接真券商」。
      要点：
      - 🔴 现在 `strategy_runtime/scheduler.py::_adapter_for` **永远给 PaperBroker**，
        所以 arm 一条股票策略到 live，会把**纸面成交记成 `mode="live"` 的真钱战绩**，
        而 S4 的提案打分会拿它当真钱证据。
      - ⛔ 别顺手去接券商（范围之外）。这条只是**堵住假数据**。
      - 判据别写死成「永远拒绝」：留一个明确的开关点，真券商到位时改一处即可。

- [ ] **11 股票 tick 写运行记录** ｜ 改动 ≤ 4 文件
      验收：`pytest tests/test_stock_scheduler_and_ledger.py tests/crypto_strategy/test_performance.py -q`
      做什么：股票 tick 也往 `crypto_strategy_runs` 写一行（复用 crypto 那套形状）。
      要点：
      - 🔴 现在股票**一行 run 都不写**，所以 `strategy_health` 永远走「一条运行记录
        都没有」那条早返回 —— **「跑了但没开单」和「压根没跑」分不出来**，
        而那正是体检报告要回答的问题。
      - ⭐ `_log_run` 现在是 `CryptoStrategyEngine` 的方法。抽成一个共用函数
        （如 `crypto_strategy/runs.py::log_run`）两边都调，**别复制第二份**。
        ⚠️ 抽的时候 crypto 那侧行为要一字不变（全量绿是判据）。
      - 入口闸原因写 run 的**顶层 detail**（见 `_ENTRY_GATE_KEYS`），逐标的的写
        `decisions` —— 两层别混。

## C. 竞技场收尾

- [ ] **12（原 04）日收益序列纳入浮动盈亏** ｜ 改动 ≤ 4 文件
      验收：`pytest tests/crypto_strategy/test_performance.py tests/crypto_strategy/test_arena.py -q`
      做什么：日收益序列现在**只含已实现盈亏**，改成把每日持仓市值变动也算进去。
      要点：
      - 🔴 现口径**系统性偏袒「赢了就跑、亏了死扛」**：那种策略的序列是清一色正数 + 零，
        均值高、方差小、最大回撤 = 0，而真实持仓可能挂着 -40%。
        01/02/03 都刻意沿用了这个有偏口径，**就等这一轮修**。
      - 需要每日持仓市值：crypto 用 `crypto_bars`，股票用 `daily_quotes`。
      - ⛔ **取不到价时留 `None`，不许用 0 顶替**（S6 教训）：那天标成缺失，
        别静默当成「那天没波动」。
      - ⚠️ 顺手统一命名：crypto live 块叫 `book_positions`，其余叫 `open_positions`。

## D. 零碎欠债（小而独立）

- [ ] **13（原 05）cockpit 的 ML 维度缺席要显式披露** ｜ 改动 ≤ 3 文件
      验收：`pytest tests/ -q -k "cockpit or scoring or dimension"`
      做什么：`cockpit_engine/scorer.py` 的 `WEIGHTS` 里 ML 占 25%，但
      `aggregator._score_ml`（`aggregator.py:200`）没模型时返回 `None` →
      被 `weights_used` 归一化**静默摊给其他四维**。要把「这一维缺席」露出来。
      要点：
      - ⭐ 真实生效权重是 技术40 / 基本面26.7 / 情感20 / 持仓13.3 ——
        **纸面 25% 从没产出过一个数**，拿纸面权重推理全是错的。
      - ⛔ **只做「披露缺席」**，不碰模型重建（那是 B 段，绑投资组合模块）。
      - 归一化行为**别改**（改了会动所有历史打分的可比性），只把「哪几维缺席、
        实际权重多少」显示出来。

- [ ] **14（原 06）电平触发 → 边沿触发** ｜ 改动 ≤ 3 文件
      验收：`pytest tests/ -q -k "alert or trigger or monitor"`
      做什么：`automation/price_alert_monitor.py:145` 附近现在是**电平触发**——
      只要价格还在阈值那一侧，每轮扫描都会再报一次。改成**边沿触发**：穿越那一刻
      报一次，回到另一侧才重新武装。
      要点：
      - Jason 亲自提的，欠最久。
      - `docs/14.具体实现计划/P1-4-invalidation.md` §84 明说这跟「失效条件盯盘」
        是**同一个问题**，⚠️ 一起解决，别各修一遍。
      - 现有字段 `triggered_at` / `last_notified_at` / `repeat` 已经在了，
        ⛔ 先看清语义再动 —— **加字段 = 改 schema = 停机**。

## E. 需要 Jason 单独拍板才开工（**建议这轮不做**）

这两条**不是欠债，是新功能 / 纯重构**。按「不要过度开发」的口径建议先不碰；
真要做各自单开一轮并先停机对齐。

- [ ] **15（原 07）组合策略 DSL：`CryptoStrategySpec` 加子策略 + 权重**
      —— 动 schema + 数据迁移。S8 的组合回测引擎已经支持了，缺的只是 DSL 这一半，
      属于**新增能力，不修任何现有错误**。
- [ ] **16（原 08）crypto 引擎迁移到共用执行器（消灭两个执行器）**
      —— 🔴 这条链路**正在跑真钱**，涉及文件远超 5 个，而它**不带来任何新能力**，
      纯粹是「两份执行器看着难受」。在「别引入新问题」的前提下，这是最不该动的一块。

## 范围之外

- **真券商接入**：涉及密钥、对外网络请求、真实下单 —— 需要 Jason 先定券商与账户，
  单独立项。⚠️ 接的时候必须一并补「成交回报回填台账」：现在只有当场成交才写台账，
  挂着的限价单后来成交**不会补记**，限价单战绩会系统性缺失。
- **投资组合模块**、doc14 的 P1-1/P1-2/P1-3/P1-4/P2-*/P3-*。
- **第 14 步 mypy 强检扩域**。

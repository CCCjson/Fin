# 交接备忘

<!-- 每轮结束时整份覆写。这是 /clear 之后唯一存活的记忆。 -->
<!-- 全部完成时，在下方单独起一行写 ALL_DONE（必须顶格），循环脚本靠它停机。 -->

## 当前

下一步：**03c-3 迁风控那批**（风控规则 / 仓位换算 / PaperBroker 初始资金）
⚠️ **必读 memory `risk-total-position-floor`**，且要先收 03c-2 留下的那道接缝（见下）。

## 🔴 Jason 要先做一件事：去设置页填分市场本金

03c-2 让 fail-closed 真正生效了。**重启后没填本金的市场，策略一单都不会下**：

- 加密：run 行 + warning 日志会说「加密还没配置本金 —— 去设置页填」
- 股票：warning 日志 + 体检报告直接说本金没配（不再指去查 arm 状态）
- 设置页「分市场本金」那张卡，未配置的市场是琥珀色「未配置 · 该市场策略不跑」

这是他 2026-08-04 亲自拍板的口径（强制手填、不自动迁移、⛔不回落），不是 bug。

## 上一步做了什么

任务 **03c-2（迁策略侧三个消费方）** 完工：

- `strategy_runtime/scheduler.py::tick_one` —— 取分市场本金，None 就跳过并
  warning（⚠️ 闸放在**日切之前**：日切会解冻 T+1 持仓，是有副作用的动作）
- `crypto_strategy/engine.py::_capital` —— `config` 口径改取加密本金；
  `real_total_value` 那条腿取不到也回 None；`_run_one` 在**熔断判定之前**返回
- `crypto_strategy/performance.py::_capital_basis` —— 按市场取，返回 `float | None`
- `strategy_health` / `_verdict` 现在会把**入口闸的理由**念出来

审核两轮 PASS。这轮的 BLOCKING 和最有价值的两条 SUGGEST：

1. 🔴 **BLOCKING：我的验收命令漏了 `tests/test_crypto_strategy_engine.py`** ——
   而那正是直接测 `_run_one` / `_capital` 的文件，4 条被打红。⚠️ 修的时候又踩了
   模块级绑定：种子用 `from database import get_session` 播进了**内存库**，
   而 adapter 读的是**文件库** → 「播了种还是 fail-closed」。
   正解是借 `trading_engine.risk.adapter.get_session`（adapter 自己那个绑定）。
2. 🔴 **`real_total_value` 那条腿原本回 `0.0`** → 越过 `is None` 那道闸 →
   `check_daily_loss` 判「资金口径未知」放行 → 每个币回「仓位换算为 0」。
   **停摆了，而 run 行里一个字都没提资金** —— 最难查的那种停摆。
3. **信号够不够响**：审核判定「作为不误交易的保护够了；作为『3 秒内知道该干嘛』
   的信号，股票那侧不够**而且指反了方向**」（体检说「先确认是不是没被武装」）。
   已修：体检早返回先交叉一次本金。

验收：`pytest tests/test_market_capital.py tests/crypto_strategy/
tests/test_crypto_strategy_engine.py tests/test_strategy_runtime.py
tests/test_stock_scheduler_and_ledger.py -q` → 242 passed；全量 → **2137+ passed**。

## 下一步需要知道的

**03c-3 开工前必读：**

- 🔴 **必读 memory `risk-total-position-floor`**：`max(总仓位上限, 单股上限)` 会
  **静默撤销 20% 现金保护**，那个坑被复制过三份，有 AST 门禁抓第四份。
- 🔴 **03c-2 留下的接缝，03c-3 必须收**：`trading_engine/brokers/paper_broker.py`
  的初始现金还是 `get_total_capital()`（全局 5000），而仓位换算已按分市场本金算 ——
  Jason 一填 `capital_a_share=100000`，股票 BUY 会成片「预算不足 / blocked_risk」。
  **别把它当策略 bug 查**，是这道口径缝。
- 迁完风控那批，`get_total_capital()` 才能删；删之前它必须一个消费方都不剩。

**03c-2 定下的口径：**

- fail-closed 的两条腿要说**不同的话**：「没配本金」vs「账户总值取不到」。
- 入口闸的理由写在 run 的**顶层 detail**（`capital`/`daily_loss`/`counts`），
  与逐币的 `decisions` 不同层。⚠️ 加新闸门时 `_ENTRY_GATE_KEYS` 要一起加，
  否则新原因会哑掉。
- 测试要跑得起来必须**播种本金**（两个 conftest 各一份 autouse fixture），
  ⚠️ 一律借 `adapter.get_session` 播种。

**流程上的坑（新增两条）：**

- ⚠️ **既有的跨文件顺序污染**：单独跑
  `pytest tests/test_crypto_strategy_engine.py tests/crypto_strategy/test_proposal_outcome.py`
  会红一条 `test_log_decision_switch_suppresses_the_duplicate_row`。
  把源码改动 stash 掉**照样红** → **不是本轮引入**，全量顺序下是绿的。⛔ 别去查它。
- ⚠️ `tests/test_crypto_cost_basis.py` 有一条既有的 ruff N805，`ruff check tests/`
  会报，不是新引入的。
- 🔴 全量必须 `cd backend` 再跑；跑 `restart.sh` 期间别跑全量。
- 🔴 push 走 HTTPS：`gh auth switch --user CCCjson && git push https://github.com/CCCjson/Fin.git feat/s5-stock-arena`

## 未解决

- 🔴 **股票策略不写 `crypto_strategy_runs` 行**（01 发现，至今未解决）。所以股票的
  体检永远走「一条运行记录都没有」那条早返回。本轮给它加了本金交叉，但**根因还在**：
  股票没有 run 留痕，「跑了但没开单」和「压根没跑」分不出来。
- 🔴 **股票 mode="live" 目前是假的**：`_adapter_for` 永远给 PaperBroker，
  arm 到 live 会把纸面成交记成真钱战绩。真券商到位前这是个雷。
- ⚠️ 「没有卫冕者」那句话在 route 和引擎各有一份（措辞一致）。既有欠债。
- ⚠️ `fork_version` 不校验 `market`。
- 🔴 接真券商时必须补「成交回报回填台账」。
- ⚠️ 命名不一致（留给 04）：crypto live 块叫 `book_positions`，其余叫 `open_positions`。
- **任务 04 是 S3 的已知缺陷**（序列只含已实现盈亏 → 偏袒「亏了死扛」）。

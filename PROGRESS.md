# 交接备忘

<!-- 每轮结束时整份覆写。这是 /clear 之后唯一存活的记忆。 -->
<!-- 全部完成时，在下方单独起一行写 ALL_DONE（必须顶格），循环脚本靠它停机。 -->

## 当前

下一步：**02 `daily_returns` 支持股票策略**（从 `strategy_trades` 算日收益序列）

## 上一步做了什么

任务 01 完工：`strategy_pnl` 按 market 分叉。改了 5 个文件（commit 见 git log）：

- `crypto_strategy/performance.py` —— 新增股票分支 `_market_of` / `_stock_legs` /
  `_stock_window_start` / `_stock_block` / `_stock_pnl`；`strategy_pnl` 顶部分叉。
  **crypto 那半边一行没动**（新分支全靠 `lg.get("fee") is None` 与 `_market_of` 的
  crypto 兜底把它隔开）。
- `strategy_runtime/ledger.py` —— `strategy_fills` 加 `days: int | None`（None=全历史）
  与 `until`。
- `strategy_runtime/executor.py` —— `_record` 改记**券商回报的成交价/量/手续费**，
  且只有 `FILLED`/`PARTIAL_FILLED` 才写台账（详见 DECISIONS 第 3 条）。
- 两个测试文件各加一节（股票 pnl 9 条 / 台账留痕 4 条）。

审核：code-reviewer **PASS**，6 条 SUGGEST 采纳 5 条（状态闸、转换兜异常、补写侧测试、
缺费用信息计数、DECISIONS 记口径变更），第 6 条（股票不写 run 行）留到 02/03，见「未解决」。

验收：`pytest tests/crypto_strategy/test_performance.py tests/test_strategy_runtime.py -q`
→ 65 passed（含 test_stock_scheduler_and_ledger）；全量 `pytest tests/ -q` → **2077 passed**；
ruff 全过。

## 下一步需要知道的

**任务 02 的地基（01 已经铺好，直接用，别重造）：**

- `_stock_legs(strategy_id, market, until)` 已经把台账行翻成 `_pair` 认的腿形状，
  返回 `({mode: legs}, {未知mode: 笔数})`。02 的 `daily_returns` **应该复用它**，
  再喂给 `_paper_daily_pnl`（那个函数已经支持逐笔 `fee`）。
- 腿的 `at` 是 `market_day_bounds(market, trade_date)[0]`（该市场当地日零点的 naive UTC）。
  🔴 **日收益按日切时必须用 `market_day_of(at, market)` 换回来**，
  ⛔ 别用 `at.date()` —— 美股会整体偏一天。
- ⭐ 窗口下界统一走 `_stock_window_start()`：台账是**日粒度**，窗口必须整日切，
  否则 `since` 是下午三点时，当天上午的平仓会莫名其妙被排除。
- `daily_returns` 的分母 `_capital_basis()` 走 `get_total_capital()` —— 那是**账户总资金**，
  跟市场无关。股票和 crypto 会共用同一个分母，⚠️ 这在跨市场比较时是个已知的口径问题
  （A 股本金和币的本金根本不是一回事），02 做的时候要么按市场取本金、要么在 note 里
  把这件事说出口，⛔ 别默默沿用。

**01 定下的几个口径（改之前先想清楚为什么）：**

- `basis` **刻意复用 crypto 那套词表**（live_fills / paper_simulated / mixed / none）。
  下游（`proposal_outcome._closed_leg_pnls`、`arena`、`agents/tools/crypto_strategy_tools.py`）
  全按这四个值分支，**新造一个词等于让它们静默走进 else**。
- 股票 paper/live 落在**同一张表**（`strategy_trades.mode` 列），与 crypto
  「paper 压根不落台账」不同 —— 分桶全靠那个 filter，少一个就是拿模拟成绩给真钱背书。
- `mixed` 时顶层**刻意不放 `realized_pnl`**（那是「相加」的入口），有测试钉着。
- 返回值多了 `market` / `currency` 两个键；`_pnl_sentence` 的单位跟着 `currency` 走
  （crypto 不带这个键 → 兜底仍是 USDT，原样）。市场→币种映射 `_MARKET_UNIT` 目前是
  全项目唯一一份，**出现第二个消费方就提到 `common/market.py`**，别复制。

**流程上的坑（沿用第 0 轮，仍然有效）：**

- 测试从 `backend/` 跑：`cd backend && conda run -n quant python -m pytest ... -q`
- ⚠️ `pytest` 不认 `--timeout=`（没装 pytest-timeout）；`conda run` 会吞 stdout，
  调试加 `--no-capture-output`；`timeout` 命令在这台 mac 上不存在
- 改完后端要在 App 里生效必须 `bash restart.sh --backend`（App 无热更新）
- 🔴 push 走 HTTPS：`gh auth switch --user CCCjson && git push https://github.com/CCCjson/Fin.git feat/s5-stock-arena`
- 判据铁律：crypto 现有测试**一条都不许红**（S5 §5 的回归防线）

## 未解决

- 🔴 **股票策略不写 `crypto_strategy_runs` 行**（审核 SUGGEST 4，**计划外的一环**）。
  全项目只有 `crypto_strategy/engine.py` 写 run 行，`strategy_runtime` 那条链一行不写。
  后果：`strategy_health` 在 `if not runs:` 就提前返回 `ok=False` → **压根不会调
  `strategy_pnl`** → `standings` 给股票策略 `pnl: None` → `/arena` 页仍然空白。
  ⚠️ 也就是说 01 算得出数了，但**数还没走到屏幕上**。
  02/03 必须解决这一环（要么让股票 tick 也写 run 行，要么让 `strategy_health` 在
  「没有 run 但台账有成交」时不提前返回）。
  🔴 动 `strategy_health` 的返回形状要当心：它提前返回时**没有 `runs` 键**，
  前端类型写成必填，上次因此整个 App 变错误页（见 memory `trading-terminal-s7`）。
- 🔴 **接真券商时必须补「成交回报回填台账」**：现在只有 `FILLED`/`PARTIAL_FILLED`
  当场写台账，挂着的限价单后来成交了**不会自动补记** → 限价单战绩会系统性缺失。
- ⚠️ 命名不一致（01 没churn，留给 04 顺手统一）：crypto live 块叫 `book_positions`，
  crypto paper 与股票块叫 `open_positions`。任务 04 要用持仓算浮动盈亏，到时候别只认一个。
- **任务 03 有裁决冲突风险**：裁决 7 说「同一时期只有一条 live」，但股票和 crypto 共用
  `crypto_strategies` 表 —— 按字面执行会让股票策略和币策略抢同一个卫冕位。
  我的判断是应理解为「每个市场族内一条」，但**这是改裁决，做到 03 时必须停机问 Jason**。
- **任务 04 是 S3 的已知缺陷**（日收益序列只含已实现盈亏 → 偏袒「亏了死扛」）。
  01 沿用了这个有偏口径（`_pair` 只算平仓腿），02 也会沿用，属于**刻意的分步**。
- 真券商接入被明确排除在本轮之外（涉及密钥 + 真实下单，要单独立项）。

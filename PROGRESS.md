# 交接备忘

<!-- 每轮结束时整份覆写。这是 /clear 之后唯一存活的记忆。 -->
<!-- 全部完成时，在下方单独起一行写 ALL_DONE（必须顶格），循环脚本靠它停机。 -->

## 当前

下一步：**03b 交易终端加市场切换器**（前端活；后端已就绪）
或 **03c 总资金 = 各市场资金之和**（⚠️ 开工前先停机拆分，见 PLAN 要点）

## 上一步做了什么

任务 03 完工：竞技场按市场分族。**本轮开头按流程停机问了 Jason**，他拍板两件事
（已进 `DECISIONS.md` 最后两条）：

1. 裁决 7「同一时期只有一条 live」→ **「每个市场同期只有一条 live」**；
2. 总资金 = 各市场资金之和（方向已定，**实施单列为 03c**）。

改了 10 个文件：

- `arena.py` —— `evaluate_arena(market=CRYPTO)` / `current_champion(market)` /
  `_days_since_last_switch(market)` 分族；新增 `resolve_market()` + `UnknownMarketError`、
  `markets_with_strategies()`。
- `service.py` —— `_supersede_siblings` / `_current_live` / `_challenger_count` 分族。
- `engine.py` —— `_load_enabled` 按 market 筛（**审核抓出来的 BLOCKING**，见下）。
- `performance.py` —— `market_of(raw)` 成为「NULL = crypto」的实现，
  `_ARCHIVED_STATUSES` 提成公开的 `ARCHIVED_STATUSES`。
- `api/routes/arena.py` + agent 工具 —— 加 `market` 参数，认不出的市场 fail-closed。
- 三个测试文件加了 12 条。

审核 **三轮**才 PASS，两条 BLOCKING 都值得记住：

1. 🔴 **crypto 引擎 `_load_enabled` 从来没按 market 筛**（这洞在加股票调度时就在了，
   但「每市场一条 live」把两边同时在跑从异常态变成**常态**，它从此常驻）。
   三个后果**都发生在碰 symbol 之前**：账户级熔断拿币安的回撤把 A 股策略停掉 +
   `enabled=0`；`last_run_at` 与股票调度器抢同一个时隙 → 股票策略盘中静默跳过；
   `CryptoStrategyRun` 被写进股票策略的行。
2. 🔴 我修「/champion 也要带 markets_with_strategies」时，**自己造出了同名同键、
   口径不同的第二份实现** —— 分叉点恰好是第二个市场最早最长的状态（刚编译完的 draft）。
   已统一到 `markets_with_strategies()`，口径与 `standings()` 对齐。

验收：`pytest tests/crypto_strategy/ tests/test_arena_routes.py tests/test_crypto_strategy_routes.py
tests/test_crypto_strategy_engine.py tests/test_stock_scheduler_and_ledger.py -q` → **218 passed**；
全量 **2101+ passed**；ruff 全过。

## 下一步需要知道的

**03 定下的口径（改之前先想清楚为什么）：**

- **每个市场一个卫冕者**。判定层（arena）和**退位逻辑**（service）必须**一起**分族 ——
  只改判定层的话，arm 一条 A 股策略照样把正在跑真钱的币策略停掉，分族在数据层立不住。
- 🔴 **市场一律在 Python 里筛**（`market_of` 是「NULL = crypto」的实现），
  ⛔ 别在 SQL 里手写 `market == 'crypto' OR market IS NULL`。在跑的策略只有个位数。
  ⚠️ 但 `market_of` **不是全项目唯一一处**：`scheduler._enabled_stock_strategies` 的
  SQL `in_(("a_share","us_stock"))`（被测试用字面字符串锁死）、`spec_from_row` 的隐式
  pydantic 默认值，各是一份。三份目前口径一致。
- **认不出的市场 fail-closed**（`resolve_market` 抛 `UnknownMarketError`）：
  `normalize_market` 会把 `a_shre`、「股票」静默回落成 default，而 verdict 通篇不提
  市场名 —— AI 会把币的结论当成股票的答复报给 Jason。
- `evaluate_arena` **刻意保持「一个竞技场」的返回形状**（默认 crypto），前端零改动。
  「一堆竞技场」是 03b 的事。

**03b（终端市场切换器）要知道的：**

- 后端已就绪：`/arena/champion`、`/arena/verdict` 收 `?market=`，两者都返回
  `market` + `markets_with_strategies`。
- 🔴 **现在前端不传 market，只看得见 crypto**，而 `/standings` 是全市场混列的 ——
  两块屏会对不上。这就是加切换器的动因。
- 🔴 动 `ArenaVerdict` 的 TS 类型要当心：`challengers` / `days_since_last_switch` /
  `note` 是必填，后端任何提前返回的分支**都必须给全**（`strategy_health` 那次
  少一个 `runs` 键就让整个 App 变错误页，tsc 不会报）。本轮已经把
  `/verdict` 的错误分支补成完整骨架。

**流程上的坑（仍然有效）：**

- 测试从 `backend/` 跑；⚠️ `pytest` 不认 `--timeout=`；`conda run` 吞 stdout
  （调试加 `--no-capture-output`）；`timeout` 命令这台 mac 上没有。
- ⏱️ **全量现在要 4~5 分钟**，会撞 600s 的前台上限 → 用 `run_in_background` 重定向到
  文件再 tail。慢的是 `tests/test_strategy_engine.py` 三条（老 `SignalGenerator`
  全市场扫描，占 237s/271s），与本轮无关。
- ⭐ 造带日期的测试数据：`days_ago` 从 `market_today(market)` 起算，断绝对日期传
  `on=date(...)`；⛔ 不是 `utcnow().date()`。
- 🔴 push 走 HTTPS：`gh auth switch --user CCCjson && git push https://github.com/CCCjson/Fin.git feat/s5-stock-arena`

## 未解决

- 🔴 **股票策略不写 `crypto_strategy_runs` 行**（01 发现，02、03 都没解决）。
  `strategy_health` 在 `if not runs:` 提前返回 → **不会调 `strategy_pnl`** →
  `standings` 给股票策略 `pnl: None`。01+02 算得出数，**数还是没走到屏幕上**。
  ⚠️ 动 `strategy_health` 的返回形状要当心（同上，前端必填键）。
- 🔴 **股票 mode="live" 目前是假的**：`scheduler._adapter_for` 永远给 PaperBroker，
  于是 arm 一条股票策略到 live 会把 **PaperBroker 的成交记成 `mode="live"` 真钱战绩**，
  S4 的提案打分会把它当真钱证据。真券商到位前这是个雷。
  （03 让「股票上 live」变得可达，所以这条从理论问题变成了现实问题。）
- ⚠️ **`fork_version` 不校验 `market`**，同族理论上造得出跨市场的版本。本轮刻意不拦
  （那是 DSL/版本化那一层的口径，且「换市场该报错还是该开新 family」要拍板）。
  `_supersede_siblings` 在那种情形下的行为已验证是对的（同族无条件退位）。
- 🔴 接真券商时必须补「成交回报回填台账」（只有 FILLED/PARTIAL_FILLED 当场写，
  挂单后来成交不会补记 → 限价单战绩系统性缺失）。
- ⚠️ 命名不一致（留给 04）：crypto live 块叫 `book_positions`，crypto paper 与股票块叫
  `open_positions`。04 要用持仓算浮动盈亏，别只认一个。
- **任务 04 是 S3 的已知缺陷**（序列只含已实现盈亏 → 偏袒「亏了死扛」），01/02/03 都
  刻意沿用了这个有偏口径。

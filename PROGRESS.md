# 交接备忘

<!-- 每轮结束时整份覆写。这是 /clear 之后唯一存活的记忆。 -->
<!-- 全部完成时，在下方单独起一行写 ALL_DONE（必须顶格），循环脚本靠它停机。 -->

## 当前

下一步：**03c-2 迁策略侧三个消费方**（股票调度器 / crypto 引擎 `_capital` / `_capital_basis`）

## 上一步做了什么

任务 **03c-1（分市场本金地基）** 完工。⚠️ 本轮开头**按 PLAN 要求停机问了 Jason**，
他把原方案整个推翻了，三条新决策已记进 `DECISIONS.md` 最后一条：

1. 🔴 **「总资金」这个概念整个退役** —— ⛔ **不做**「总资金 = 各市场之和」的求和值
   （各市场是 CNY/HKD/USD/USDT，求和是混币种）。原 03c 那句话**已作废**。
2. **不自动迁移、强制手填** —— 现有全局 5000 不往任何市场摊、也不回落到它；
   未配置的市场 **fail-closed**（策略不跑）。
3. **分三轮**：本轮只做地基，**一个消费方都不迁**。

本轮改了 8 个文件：`adapter.py`（`get_market_capital` / `market_capitals` / `capital_key`）、
`common/market.py`（`MARKET_CURRENCIES` + `currency_of`）、`data.py`（四个空槽位 +
只读端点 `/data/market-capitals`）、`settings_tools.py`（四键进 AI 只读黑名单）、
前端 `CapitalCard.tsx` + `settingsService.ts` + `Settings.tsx`、
`tests/test_market_capital.py`（15 条）。

审核两轮 PASS。**两条 BLOCKING 都在文档上**，很值得记：
`PLAN.md` 里 03c 的标题还写着被否掉的「总资金 = 各市场之和」，`DECISIONS.md` 08-03
那条也没标已被推翻 —— 下一轮从 PLAN 选卡读到的就是被推翻的方案。
**这就是「悄悄推翻决策」的传送带**。已改：PLAN 拆成 03c-1/2/3，DECISIONS 加 🔄 标记。

验收：`pytest tests/test_market_capital.py -q` → **15 passed**；全量 → **2131 passed**；
`npx tsc -b` 干净。

## 下一步需要知道的

**03c-2 的地基已经铺好，直接用：**

- `adapter.get_market_capital(market) -> float | None`。
  🔴 **`None` = 未配置，`0` = 明确不投钱**，两件事必须分辨。
  ⛔ **不许回落**到 `get_total_capital()` —— 那个 5000 是**A 股口径的人民币**，
  拿去给美股/币算仓位是错的口径且不会报错（`crypto_strategy/engine.py` 里有实锤：
  口径混用曾让 **live 每笔 BUY 恒被 blocked_risk，一单也下不出来**）。
- `nan` / `inf` / 负数都已在读取层被判为**未配置**（`float()` 一个都不抛，
  而 `nan` 会让所有 `>` 比较恒 False = **风控静默放行**）。
- 三个待迁消费方：`strategy_runtime/scheduler.py:111`（`capital=`）、
  `crypto_strategy/engine.py:_capital`、`crypto_strategy/performance.py::_capital_basis`。
- ⚠️ `_capital_basis` 的 docstring 明写「所有策略共用同一个分母」是**刻意的**
  （否则跨策略收益率量级差一个倍数）。改成分市场后，**同市场内仍必须同分母**。
- 🔒 **有 AST 门禁盯着**：`tests/test_market_capital.py::test_nobody_sums_the_market_capitals`
  会扫全仓，「同一个函数体内既取了本金、又有 `sum(`/`+=`」就报。
  实测三种写法（内联 sum / 先赋值再 sum / `+=` 累加）全部会红。
  确属误报就在那行加 `# noqa: capital-sum`。

**03c-1 定下的口径：**

- 四个键 `capital_<market>` 存在 `UserSettings`（**不是 .env**）。三处必须同时有：
  `_DEFAULT_SETTINGS` 槽位（值留空）、`_RISK_READONLY_KEYS`、`currency_of` 有币种 ——
  有一条从 `CANONICAL_MARKETS` 推导的测试钉着，加第五个市场时会红。
- 市场元数据（中文名 / 币种）**只在 `common/market.py`**，⛔ route 和前端都别抄。
- 前端本金卡的**失败态与「未配置」必须分开**：都渲染成「未配置」的话，接口挂了
  会显示成四个市场都没配 —— 而分开这两类状态正是这张卡存在的全部意义。

**流程上的坑（仍然有效）：**

- 🔴 全量测试**必须 `cd backend` 再跑**；⚠️ 跑 `restart.sh` 期间别跑全量（C++ 服务会短暂不可用）。
- `pytest` 不认 `--timeout=`；`conda run` 吞 stdout（加 `--no-capture-output`）；
  全量 ~1-5 分钟，会撞 600s 前台上限 → 用 `run_in_background` 重定向到文件再 tail。
- ⚠️ 测试里改 `UserSettings` 要**快照/还原**，不是一删了事（隔壁
  `test_risk_total_position_floor.py` 的 fixture 恢复的是「原值」，会把你留下的脏值
  当原值恢复 → 顺序依赖的偶发红灯）。
- 🔴 push 走 HTTPS：`gh auth switch --user CCCjson && git push https://github.com/CCCjson/Fin.git feat/s5-stock-arena`

## 未解决

- 🔴 **股票策略不写 `crypto_strategy_runs` 行**（01 发现，02/03/03b/03c-1 都没解决）。
  `strategy_health` 提前返回 → 不调 `strategy_pnl` → 排行榜给股票 `pnl: None`。
  ⚠️ 动它的返回形状要当心前端必填键。
- 🔴 **股票 mode="live" 目前是假的**：`scheduler._adapter_for` 永远给 PaperBroker，
  arm 一条股票策略到 live 会把纸面成交记成真钱战绩。真券商到位前这是个雷。
- ⚠️ 「没有卫冕者」那句话在 route 和引擎各有一份（措辞目前一致）。按 route 模块自己的
  头注，它该由引擎给。既有欠债。
- ⚠️ `fork_version` 不校验 `market`，同族理论上造得出跨市场版本。
- 🔴 接真券商时必须补「成交回报回填台账」。
- ⚠️ 命名不一致（留给 04）：crypto live 块叫 `book_positions`，其余叫 `open_positions`。
- **任务 04 是 S3 的已知缺陷**（序列只含已实现盈亏 → 偏袒「亏了死扛」）。

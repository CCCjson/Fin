# 交接备忘

<!-- 每轮结束时整份覆写。这是 /clear 之后唯一存活的记忆。 -->
<!-- 全部完成时，在下方单独起一行写 ALL_DONE（必须顶格），循环脚本靠它停机。 -->

## 当前

**新会话从这里开始：先读 `PLAN.md` 的「零、硬规矩」，再做任务 09。**

下一步：**09 迁风控那批 + 删 `get_total_capital()`**

分支 `feat/s5-stock-arena`，最新 commit `5dea1a8`。工作区干净，没有 `BLOCKED.md`。

## 🔴 Jason 要先做一件事：去设置页填分市场本金

03c-2 让 fail-closed 生效了。**没填本金的市场，策略一单都不会下**：

- 加密：run 行 + warning 日志会说「加密还没配置本金 —— 去设置页填」
- 股票：warning 日志 + 体检报告直说本金没配
- 设置页「分市场本金」那张卡，未配置的是琥珀色「未配置 · 该市场策略不跑」

这是他 2026-08-04 亲自拍板的口径（强制手填、不自动迁移、⛔不回落），**不是 bug**。

## 已经做完的（01 → 03c-2，六轮，全部已推送）

| 轮次 | 做了什么 |
|---|---|
| 01 | `strategy_pnl` 按 market 分叉，股票走 `strategy_trades` |
| 02 | `daily_returns` 支持股票，序列按**交易日**补齐（不是自然日） |
| 03 | 竞技场按市场分族：**每个市场一条 live**（裁决 7 新读法，Jason 拍板） |
| 03b | 交易终端市场切换器；市场中文名收进 `common/market.py` 一处 |
| 03c-1 | 分市场本金地基：配置键 + 读取 API + 设置页 + AI 只读黑名单 |
| 03c-2 | 策略侧改用分市场本金，**未配置 fail-closed** |

全量测试 **2139 passed**。四条决策记在 `DECISIONS.md`（含两条被推翻的，有 🔄 标记）。

## 下一步需要知道的（做 09 之前必读）

- 🔴 **必读 memory `risk-total-position-floor`**：`max(总仓位上限, 单股上限)` 会
  **静默撤销 20% 现金保护**，那个坑被复制过三份，有 AST 门禁抓第四份。
- 🔴 **03c-2 留下的接缝**：`paper_broker.py` 的初始现金还是 `get_total_capital()`
  （全局 5000），而仓位换算已按分市场本金算 —— Jason 填了 `capital_a_share=100000`
  之后，股票 BUY 会成片「预算不足 / blocked_risk」。**别把它当策略 bug 查**，
  就是这道口径缝，09 收。
- `get_market_capital(market)` 返回 `float | None`：**`None` = 未配置（≠ 0）**，
  ⛔ 不许回落到任何全局值。`nan`/`inf`/负数在读取层已被判为未配置。
- 🔒 **有 AST 门禁**：`tests/test_market_capital.py::test_nobody_sums_the_market_capitals`
  扫全仓，「同一函数体内既取了本金、又有 `sum(`/`+=`」就报（三种写法都验过会红）。
  各市场是 CNY/HKD/USD/USDT，**求和是混币种**。
- 测试要跑得起来必须**播种本金**（两个 conftest 各一份 autouse fixture），
  ⚠️ 一律借 `trading_engine.risk.adapter.get_session` 播种 —— 用
  `from database import get_session` 会播进内存库、而 adapter 读文件库
  （模块级绑定，全项目 20+ 处这个坑）。

## 未解决（都已在 PLAN 里排成任务，别再当新发现）

- 🔴 **股票 `mode="live"` 是假的** → PLAN 任务 10
- 🔴 **股票不写 run 行**，体检分不出「跑了没开单」和「没跑」 → PLAN 任务 11
- 🔴 **日收益序列只含已实现盈亏**，偏袒「亏了死扛」 → PLAN 任务 12
- ⚠️ 命名不一致：crypto live 块叫 `book_positions`，其余叫 `open_positions` → 随 12 一起统一
- ⚠️ **「没有卫冕者」那句话在 route 和引擎各有一份**（措辞一致）。按 route 模块自己的
  头注「人话结论由引擎层产出」，它该由引擎给。**属于「更优雅」类，不急，别顺手做。**
- ⚠️ `fork_version` 不校验 `market`，同族理论上造得出跨市场版本。已确认
  `_supersede_siblings` 在那种情形下行为正确。要拦得先定「换市场该报错还是开新 family」。
- 🔴 **接真券商时必须补「成交回报回填台账」**（挂单后来成交不会补记）→ 范围之外，
  但接券商那一轮必须一起做。

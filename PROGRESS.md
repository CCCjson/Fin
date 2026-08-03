# 交接备忘

<!-- 每轮结束时整份覆写。这是 /clear 之后唯一存活的记忆。 -->
<!-- 全部完成时，在下方单独起一行写 ALL_DONE（必须顶格），循环脚本靠它停机。 -->

## 当前

下一步：**03c 总资金 = 各市场资金之和** —— ⚠️ **开工前先停机拆分**（见 PLAN 要点）

## 上一步做了什么

任务 03b 完工：交易终端加市场切换器。改了 12 个文件（后端 5 / 前端 5 / 测试 2）：

- 后端：`standings(market=)` + `/arena/standings?market=`（认不出的市场 fail-closed）；
  每行带 `market`；新增 `common/market.py::MARKET_LABELS` + `label_of()`。
- 前端：新组件 `MarketSwitcher.tsx`；`Automation.tsx` 加 market state 并按市场重取；
  `StandingsPanel` 加 `note`；**删掉了前端的市场中文名表**（改用后端给的）。
- agent 工具 `list_strategy_standings` 也加了 `market`。

审核两轮 PASS。这轮唯一的 BLOCKING 很值得记住：

🔴 **我只堵了闭包，没堵在途响应**。把 `market` 加进 effect 依赖只修掉「定时器读到旧值」，
修不掉**已经发出去的请求**：60 秒轮询以 crypto 发出 → Jason 切到 A 股 → A 股数据先回
→ 两秒后 crypto 的响应落地把它盖掉。表现是「切了，过一会儿又跳回去」，而面板上
一个字段都不显示市场 —— 屏幕上会是**顶着 A 股名字的 crypto 卫冕者**。
修法是 `marketRef` + 每个 `.then`/`.catch` 都先验 `m === marketRef.current`
（⚠️ catch 也要验：旧市场的**失败**同样会清掉新市场的数据）。

验收：`pytest tests/test_arena_routes.py tests/crypto_strategy/ -q` → 187 passed；
全量（**从 `backend/` 跑**）→ **2108 passed**；`npx tsc -b` 干净；
App 已重建，交易终端正常渲染；真环境实测三个端点的市场收窄/拒绝/中文名都对。

## 下一步需要知道的

**03c（总资金 = 各市场之和）开工前必读：**

- 🔴 **必读 memory `risk-total-position-floor`**：`max(总仓位上限, 单股上限)` 会**静默
  撤销 20% 现金保护**，那个坑被复制过三份，还加了 AST 结构性门禁抓第四份。
- `get_total_capital()`（`trading_engine/risk/adapter.py`）的消费方至少四处：风控规则、
  股票调度器的 `capital=`、crypto 引擎的 `_capital`、`performance._capital_basis`。
  涉及文件远超 5 个 → **先停机拆分**，别一轮闷头做完。
- ⚠️ `_capital_basis` 的 docstring 明写「所有策略共用同一个分母」是**刻意的**
  （否则跨策略的收益率量级会差一个倍数）。改成分市场时，**同市场内仍必须同分母**，
  这条别一起改掉。

**03b 定下的口径：**

- **市场中文名只有一处：后端 `common/market.py::MARKET_LABELS`**。
  `/arena/market-status` 每行带 `market_label`（⚠️ 与交易时段的 `label`「交易中/已收盘」
  是两回事），`/champion` 带 `market_labels`。前端那份表**已删**，⛔ 别加回来。
- 人话里不许出现 canonical 英文名（`a_share` / `crypto`）—— 全走 `label_of()`。
- `standings` 的 `market` 语义与 `/champion`、`/verdict` **不同**：前者不传 = 全市场混列
  （老行为 + agent 工具要的），后两者不传 = crypto。三处 docstring 都写明了，
  空串那条缝有测试钉着。
- 切换器**少于两个市场不渲染**。所以在只有 crypto 的库里它**看不见**是正常的；
  想肉眼验，在测试库建一条 `market="a_share"` 的 draft 就够（口径是「未归档都算」）。

**流程上的坑（新增两条）：**

- 🔴 **全量测试必须 `cd backend` 再跑**。我这轮从仓库根跑 `pytest backend/tests/`，
  报了 10 条失败，全是环境问题（`test_crypto_portfolio_backtest` 要连 :8002，而
  restart.sh 正在重启它）—— 从 `backend/` 重跑立刻 2108 全绿。⛔ 别被这种失败带偏。
- ⚠️ **跑 `restart.sh` 期间别跑全量测试**：C++ 订单簿/回测服务会短暂不可用。
- 全量耗时波动很大（45s ~ 271s），慢的是 `test_strategy_engine.py` 三条老
  `SignalGenerator` 全市场扫描，与本轮无关。

## 未解决

- 🔴 **股票策略不写 `crypto_strategy_runs` 行**（01 发现，02/03/03b 都没解决）。
  `strategy_health` 在 `if not runs:` 提前返回 → **不会调 `strategy_pnl`** →
  `standings` 给股票策略 `pnl: None`。⚠️ 动它的返回形状要当心前端必填键。
- 🔴 **股票 mode="live" 目前是假的**：`scheduler._adapter_for` 永远给 PaperBroker，
  arm 一条股票策略到 live 会把 PaperBroker 的成交记成 `mode="live"` 真钱战绩，
  S4 的提案打分会把它当真钱证据。真券商到位前这是个雷。
- ⚠️ **「没有卫冕者」那句话有两份**（`api/routes/arena.py` 与 `crypto_strategy/arena.py`
  各一份，措辞目前一致）。按 route 模块自己的头注「人话结论由引擎层产出」，
  它该由引擎给（比如 `current_champion` 返回 None 时配一个 reason）。既有欠债。
- ⚠️ `fork_version` 不校验 `market`，同族理论上造得出跨市场版本（03 已确认
  `_supersede_siblings` 在那种情形下行为正确）。要拦的话得先定「换市场该报错还是
  开新 family」，是个口径题。
- 🔴 接真券商时必须补「成交回报回填台账」（挂单后来成交不会补记）。
- ⚠️ 命名不一致（留给 04）：crypto live 块叫 `book_positions`，其余叫 `open_positions`。
- **任务 04 是 S3 的已知缺陷**（序列只含已实现盈亏 → 偏袒「亏了死扛」）。

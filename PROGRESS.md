# 交接备忘

<!-- 每轮结束时整份覆写。这是 /clear 之后唯一存活的记忆。 -->
<!-- 全部完成时，在下方单独起一行写 ALL_DONE（必须顶格），循环脚本靠它停机。 -->

## 当前

**新会话从这里开始：先读 `PLAN.md` 的「零、硬规矩」。**

下一步：**11 股票 tick 写运行记录** —— 不依赖任何未决事项，可以直接开工。

🔴 **09b 仍卡着**，开工前必须先拿到 Jason 的答复（见下方「等 Jason 拍板的一个问题」）。
任务 10 已完工（本轮）。

分支 `feat/s5-stock-arena`。工作区干净，没有 `BLOCKED.md`。

## 🔴 Jason 要先做一件事：去设置页填分市场本金

03c-2 起，**没填本金的市场，策略一单都不会下**（fail-closed）。09a 之后
**纸面账户本身也不发了** —— MoneyBill 里下模拟单会直接回一句「A股还没配置本金」。

这是他 2026-08-04 亲自拍板的口径（强制手填、不自动迁移、⛔不回落），**不是 bug**。

## 等 Jason 拍板的一个问题（09b 的前提）

> 分市场之后，「总持仓 ≤ 80% / 必须留 20% 现金」是
> **(a) 每个市场各留自己本金的 20%**，还是 **(b) 仍按某种全局口径判**？

倾向 (a)：与「每市场独立本金、不折算汇率」一致，而 (b) 需要一个跨币种总量 ——
那个概念 2026-08-04 已被拍板退役。但这是硬风控红线的**作用域变更**，得他点头。

## 已经做完的（01 → 10，八轮，全部已推送）

| 轮次 | 做了什么 |
|---|---|
| 01 | `strategy_pnl` 按 market 分叉，股票走 `strategy_trades` |
| 02 | `daily_returns` 支持股票，序列按**交易日**补齐（不是自然日） |
| 03 | 竞技场按市场分族：**每个市场一条 live**（裁决 7 新读法，Jason 拍板） |
| 03b | 交易终端市场切换器；市场中文名收进 `common/market.py` 一处 |
| 03c-1 | 分市场本金地基：配置键 + 读取 API + 设置页 + AI 只读黑名单 |
| 03c-2 | 策略侧改用分市场本金，**未配置 fail-closed** |
| 09a | **纸面账户按市场分池**（原任务 09 已拆成 09a/09b/09c） |
| 10 | **股票没接真券商时不许跑 live**（arm 闸 + 运行时闸，同一判据） |

全量 **2152 passed / 4 skipped**。决策记在 `DECISIONS.md`（含三条被推翻/改写的，有 🔄 标记）。

## 下一步需要知道的（做 09b 之前必读）

- 🔴 **必读 memory `risk-total-position-floor`**：`max(总仓位上限, 单股上限)` 会
  **静默撤销 20% 现金保护**，那个坑被复制过三份，有 AST 门禁抓第四份。

- 🔴 **「股票 BUY 成片 blocked_risk」的真凶在 09b，不在 09a —— 老 PLAN 归错因了。**
  `strategy_runtime/stock_adapter.py::broker_info()` 调的是**无参** `build_broker_info()`
  → 全局 5000。执行器按分市场本金（如 100000）算出的单，会在风控闸拿 5000 当分母
  判超限。09a 修的是另一件事（broker 自己的现金池是 A股+美股混币种共用），
  必要但**不充分**。⛔ 别再把这个症状当 09a 没生效。

- 🔴 **09b 会撞上一条更深的既有裂缝：两套持仓。** 风控闸读的持仓来自 `ManualTrade`
  （`PortfolioCalculator`，且**完全不分市场**），而策略的纸面持仓在 `PaperBroker` 里。
  按市场过滤持仓要新写（`common.market.infer_market_from_symbol` 现成）。
  发现要动这个的结构就**停机单列**，别顺手改。

- ⚠️ `position_sizing._risk_managers` 按 `pct` 缓存 `RiskManager`，而
  `manager.py:63` 的 `max_daily_loss = capital × 3%` 是**建实例时烤进去的** ——
  分市场后缓存键必须变成 `(pct, market)`，否则第一个市场的限额会被第二个市场
  **静默复用**。

- `get_market_capital(market)` 返回 `float | None`：**`None` = 未配置（≠ 0）**，
  ⛔ 不许回落到任何全局值。`nan`/`inf`/负数在读取层已被判为未配置。
  ⚠️ **`0` 是合法本金**（这个市场不投钱），判据一律写 `is None` 不写 falsy ——
  09a 的审核就是在这儿抓到一个 `ZeroDivisionError`（见下）。

- 🔒 **有 AST 门禁**：`tests/test_market_capital.py::test_nobody_sums_the_market_capitals`
  扫全仓，「同一函数体内既取了本金、又有 `sum(`/`+=`」就报。各市场是 CNY/HKD/USD/USDT，
  **求和是混币种**。

- 测试要跑得起来必须**播种本金**（`tests/crypto_strategy/conftest.py` 有 autouse
  fixture；`tests/` 根目录**没有**，那边要自己 `_set`）。
  ⚠️ 一律借 `trading_engine.risk.adapter.get_session` 播种 —— 用
  `from database import get_session` 会播进内存库、而 adapter 读文件库
  （模块级绑定，全项目 20+ 处这个坑）。

- ⚠️ **改 `PaperBroker` 相关测试时记得清注册表**：`_paper_brokers` 是**进程级**的，
  `tests/test_market_capital.py` 的 `fresh_brokers` fixture 负责清。不清的话
  「本金改了、账户没跟着改」这类回归测不出来。

## 09a 具体做了什么（改动 4 文件：3 源码 + 1 测试）

- `trading_engine/brokers/paper_broker.py`
  - `get_paper_broker(market)`（**签名变了，原来无参**）：按 canonical 市场名各开一个
    实例存进 `_paper_brokers`，初始现金 = `get_market_capital(market)`；
    未配置抛 `MarketCapitalNotConfiguredError`（fail-closed，⛔不回落）。
  - `get_account_info()` 的 `return_pct` 在 `initial_cash <= 0` 时返回 **None**
    （原来无条件除 `initial_cash` → 本金填 0 时 `ZeroDivisionError`）。
- `strategy_runtime/scheduler.py::_adapter_for` → `get_paper_broker(spec.market)`
- `agents/tools/trading_tools.py` → 显式传 `A_SHARE`（该文件整体是 A 股口径：
  成交日走 `market_today(A_SHARE)`、金额用 ¥、名称查 `StockInfo`），
  `preview_order` / `place_order` 两个入口接住新异常说人话
- `tests/test_market_capital.py` +5 条

⭐ **顺带修掉的**：日切 `settle_new_day()` 以前打在共享 broker 上 ——
任一市场跨日会把**所有市场**的 T+1 持仓一起解冻。分池后自然隔离。

## 任务 10 具体做了什么（改动 5 文件：3 源码 + 2 测试）

守的是**数据真伪，不是资金安全**：调度器永远发 `PaperBroker`，所以一条 arm 到 live 的
股票策略，成交是纸面的、却会以 `mode="live"` 落进 `strategy_trades` ——
S4 的提案打分会拿它当**真钱证据**，竞技场也会拿它当卫冕者战绩。

- `strategy_runtime/stock_adapter.py`：新增 `live_broker_for(market) -> Any | None`，
  现在恒返回 `None`。🔒 **唯一开关点**，真券商到位时只改它。
- `strategy_runtime/scheduler.py`：两处
  - `_adapter_for`：`spec.mode == "live"` **且**有真券商才用真券商，否则 PaperBroker。
    🔴 **那个 mode 判据不许删** —— 没有它，真券商上线当天所有纸面策略会一起拿真钱
    下单（`paper`/`live` 的区别全在 broker）。这是审核抓到的，比原 bug 更严重。
  - `tick_one`：运行时闸，`row.mode == "live"` 且没真券商 → 跳过 + warning。
    ⭐ arm 那道闸只管**新的**上线动作，管不到已经是 live 的**存量行**。
- `crypto_strategy/service.py`：`_assert_live_broker(row)`，在 `arm()` 里**所有写动作
  之前**调用。⚠️ 市场判定走 `strategy_market()`（「NULL = crypto」的唯一实现），
  ⛔ 别裸读 `row.market` —— 存量 crypto 行那一列是空的，会被误伤。
- 测试：`test_versioning.py` +5、`test_market_capital.py` +3；
  既有的 `test_arena.py::test_arming_in_one_market_does_not_demote_another` 前提被
  推翻（它要 arm 一条 A 股到 live），改成 monkeypatch 注入假券商还原场景 ——
  ⛔ 别改成 `enable_paper` 绕过去，`_supersede_siblings` 只有 `target_mode="live"`
  那条分支才有跨家族的 `or_`。

⚠️ **测这道闸的顺序不能靠查库**：`arm` 的 `finally: session.close()` 之前没 commit，
ORM 变更会整个回滚 —— 闸挪到退位之后，查库式断言照样绿。要用 monkeypatch 让退位
动作一被调用就炸（见 `test_the_gate_fires_before_any_write`）。

## 未解决

### 09a 审核留下的（都属于「不在本轮范围」，已确认不影响正确性）

- ⚠️ **`_paper_brokers` 永不失效，而本金是运行时可改的**（设置页 / `update_setting`）。
  改大本金后：仓位换算按新值、现金池还是旧值 → 单会以「资金不足」被
  `StockAdapter.submit` 抛掉（记成 `order_failed`，原因看不出来），要重启才一致。
  建议 09b/09c 顺手加 `reset_paper_brokers(market=None)` 并在 `capital_*` 被写时调用
  （顺带给测试一个公开入口，现在是直接改私有变量）。
  同处还有个低概率并发：字典 check-then-set 无锁，两个线程首次同时取同一市场会各建
  一个池子，后写的赢 —— 与改之前的全局单例同构，**不是 09a 引入的**。
- 🔴 **PaperBroker 的手续费是 A 股写死的**：`stamp_tax_rate = 0.001` 对**所有市场
  每一笔 SELL** 都收，佣金也是全局一个 0.03%。以前一个混用 broker 没法修，
  现在分开了就能按市场取（`common/trading_rules.py` 已有分市场规则表）。
  🔴 港美股纸面战绩会一直背着一笔并不存在的 A 股印花税，而「新策略先 paper 跑一段
  再上实盘」这条护栏正是靠纸面成绩判的。**建议单开一条 PLAN 任务**（需 Jason 批）。
- 🔴 **PaperBroker 只活在内存里，没有从 `strategy_trades` 回放重建的通道**。
  这项目改一行 Python 就要 `restart.sh` → 每次重启纸面账户清零回满仓现金、持仓凭空
  消失，而台账里那笔买入还在 → 下一 tick 认为自己空仓再买一遍。
  与 09b 的「两套持仓」是同一家族，**建议一起排**（需 Jason 批）。
- （更优雅，别做）`normalize_market(market, default=market)` 认不出时拿原样字符串当 key，
  `"Foo"` / `"foo"` 会占两个槽位。两者都拿不到本金、都 fail-closed，只是不整洁。

### 任务 10 审核留下的

- 🔴 **股票竞技场从现在起「结构上无结论」** —— `arena.evaluate_arena` 判卫冕者的
  依据是 `mode == "live"`（`arena.py:523`），而股票已经 arm 不到 live 了，
  所以 a_share / us_stock **永远返回 `champion=None`**。
  ⚠️ 这是任务 10 的**正确代价**（假卫冕者比没有卫冕者糟得多），真券商到位自动解开。
  ⛔ 别当成回归去查。
- ⚠️ `arena.py:599` 股票没有卫冕者时，verdict 会说「先 arm 一条上 live」——
  而这条路对股票现在**走不通**，话术把人指向一堵墙。改 1 行即可
  （按 market 是不是 crypto 给不同的下一步），但会变成第 4 个源码文件，超本轮预算。
- （更优雅，别做）`service.py` 里 `label_of` 是函数内 import，而文件顶部已经
  `from common.market import CRYPTO`，没有循环依赖，可以提上去。
- （更优雅，别做）`scheduler.py` 那句「最多每 interval_minutes 一条」注释**对从没跑过的
  策略不准**：被闸拦下不会走到 `_touch` → `last_run_at` 恒 `None` → `_is_due` 恒真 →
  开市时段每 60s 一条 warning（A 股每天约 240 行）。03c-2 的本金闸就有这个行为，
  新闸只是继承，不影响正确性。
- （更优雅，别做）`scheduler.py` 里「没配本金就不跑」那段注释被新的券商闸和三行
  import 隔开了，读起来容易挂错对象。纯排版。

### 更早就在的（都已在 PLAN 里排成任务，别再当新发现）

- 🔴 **股票不写 run 行**，体检分不出「跑了没开单」和「没跑」 → PLAN 任务 11
- 🔴 **日收益序列只含已实现盈亏**，偏袒「亏了死扛」 → PLAN 任务 12
- ⚠️ 命名不一致：crypto live 块叫 `book_positions`，其余叫 `open_positions` → 随 12 一起统一
- ⚠️ **「没有卫冕者」那句话在 route 和引擎各有一份**（措辞一致）。按 route 模块自己的
  头注「人话结论由引擎层产出」，它该由引擎给。**属于「更优雅」类，不急，别顺手做。**
- ⚠️ `fork_version` 不校验 `market`，同族理论上造得出跨市场版本。已确认
  `_supersede_siblings` 在那种情形下行为正确。要拦得先定「换市场该报错还是开新 family」。
- 🔴 **接真券商时必须补「成交回报回填台账」**（挂单后来成交不会补记）→ 范围之外，
  但接券商那一轮必须一起做。

# 实现计划 — doc15 S5 收尾 + 零碎欠债

> 来源：`docs/15.策略竞技场/00-PLAN.md` §5 与 `S5-股票地基.md` §3c，
> 外加 `docs/14.具体实现计划/00-PLAN.md` 的 P1-6 A 段。
> **开工前先读对应卡片**，别只看这里的一行标题。

每条任务的粒度：一轮能做完，有一条能跑的验收命令，改动不超过 3 到 5 个文件。

⚠️ 所有 Python 命令必须走 `conda run -n quant`，且在 `backend/` 目录下跑 pytest。
⚠️ 改完后端要生效必须 `bash restart.sh --backend`（App 无热更新）。

## 任务

### A. 股票竞技场（S5 §3c 欠债 —— 台账已建好，但上层不认它）

- [x] 01 `strategy_pnl` 按 market 分叉：股票走 `strategy_trades`，crypto 保持读 `crypto_trades` 不动 —
      验收：`cd backend && conda run -n quant python -m pytest tests/crypto_strategy/test_performance.py tests/test_strategy_runtime.py -q`
      要点：paper/live 分桶不合并（S4 教训）；crypto 分支**一行都不许改行为**，回归全绿是判据。

- [x] 02 `daily_returns` 支持股票策略：从 `strategy_trades` 算日收益序列，窗口按**市场当地日**切 —
      验收：`cd backend && conda run -n quant python -m pytest tests/crypto_strategy/ -q`
      要点：这是四道门槛门槛②的输入，序列口径错了整个竞技场就是错的；
      ⚠️ 已知缺陷「序列只含已实现盈亏 → 偏袒亏了死扛」**本轮不修**（见任务 04）。

- [x] 03 `arena.py` 按市场分族评比：股票策略不该和 crypto 抢同一个卫冕位 —
      验收：`cd backend && conda run -n quant python -m pytest tests/crypto_strategy/test_arena.py tests/test_arena_routes.py -q`
      要点：裁决 7「同一时期只有一条 live」应理解为**每个市场族内**一条；
      ⚠️ 若这与裁决 7 原文冲突不可自行拍板 → 写 BLOCKED.md 停机问 Jason。

- [x] 03b 交易终端加市场切换器（03 的刻意延后） —
      验收：`cd backend && conda run -n quant python -m pytest tests/test_arena_routes.py -q`
      并跑 `bash restart.sh` 在 App 里肉眼确认
      要点：后端已就绪（`/arena/champion`、`/arena/verdict` 收 `market` 参数，
      返回带 `markets_with_strategies`）。03 刻意没改 `evaluate_arena` 的返回形状
      （「一个竞技场」→「一堆竞技场」会掀翻 `arenaService.ts` 和终端页）——
      🔴 现在前端不传 market，**只看得见 crypto**，而 `/standings` 是全市场混列的，
      两块屏会对不上。

### A2. 分市场本金（03c，**已拆成三轮**）

> 🔄 原 03c「总资金 = 各市场资金之和」**已被推翻**（Jason 2026-08-04）：
> 各市场是 CNY/HKD/USD/USDT，求和是混币种。⛔ 别再做那个求和值。
> 新口径 = **「总资金」这个概念整个退役，只留分市场本金**，见 `DECISIONS.md` 最后一条。

- [x] 03c-1 地基：分市场本金配置键 + 读取 API + 设置页 + AI 只读黑名单 —
      验收：`cd backend && conda run -n quant python -m pytest tests/test_market_capital.py -q`
      要点：**一个消费方都不迁**，`get_total_capital()` 行为必须一行不变。
      未配置 = `None` ≠ 0，⛔ 不回落到任何全局值。

- [x] 03c-2 迁策略侧三个消费方：股票调度器 / crypto 引擎 `_capital` / `_capital_basis` —
      验收：`cd backend && conda run -n quant python -m pytest tests/test_market_capital.py tests/crypto_strategy/ tests/test_strategy_runtime.py tests/test_stock_scheduler_and_ledger.py -q`
      要点：🔴 **未配置的市场 fail-closed**（策略不跑），⛔ 不许回落。
      ⚠️ `_capital_basis` 的 docstring 明写「所有策略共用同一个分母」是刻意的 ——
      改成分市场后，**同市场内仍必须同分母**，那条别一起改掉。

- [ ] 03c-3 迁风控那批：风控规则 / 仓位换算 / PaperBroker 初始资金 —
      验收：`cd backend && conda run -n quant python -m pytest tests/ -q -k "risk or capital or position"`
      要点：🔴 **必读 memory `risk-total-position-floor`**（`max(总仓位上限, 单股上限)`
      会静默撤销 20% 现金保护，那个坑被复制过三份 + 有 AST 门禁抓第四份）。
      ⚠️ 迁完才能删 `get_total_capital()`，删之前它必须一个消费方都不剩。
      🔴 **03c-2 留下的接缝，这一轮必须收**：`trading_engine/brokers/paper_broker.py`
      的初始现金还是 `get_total_capital()`（全局 5000），而仓位换算已经按分市场本金算了 ——
      Jason 一填 `capital_a_share=100000`（fail-closed 逼他必须填），股票 BUY 就会成片
      「预算不足 / blocked_risk」。**别把它当策略 bug 查**，是这道口径缝。

- [ ] 04 日收益序列纳入浮动盈亏，修掉「偏袒亏了死扛」的系统性偏差 —
      验收：`cd backend && conda run -n quant python -m pytest tests/crypto_strategy/test_performance.py tests/crypto_strategy/test_arena.py -q`
      要点：需要每日持仓市值快照；取不到价时**留 None 不用 0 顶替**（S6 教训）。

### B. 零碎欠债（小而独立，插空做）

- [ ] 05 P1-6 A 段：让 cockpit 的 ML 维度缺席**显式披露**，不再被静默摊给其他四维 —
      验收：`cd backend && conda run -n quant python -m pytest tests/ -q -k "cockpit or scoring or dimension"`
      要点：真实生效权重是 技术40/基本面26.7/情感20/持仓13.3，**纸面 25% 从没产出过一个数**；
      只做「披露缺席」，⛔ 不碰模型重建（那是 B 段，绑投资组合模块）。

- [ ] 06 电平触发 → 边沿触发（Jason 亲提，欠了最久） —
      验收：`cd backend && conda run -n quant python -m pytest tests/ -q -k "alert or trigger or monitor"`
      要点：`docs/14.具体实现计划/P1-4-invalidation.md` §84 明说这跟失效条件盯盘是**同一个问题**，
      要一起解决别各修一遍。

### C. 大件（每件开工前先停机对齐，别直接闷头做）

- [ ] 07 组合策略 DSL：`CryptoStrategySpec` 加子策略 + 权重字段（S8 后半） —
      验收：`cd backend && conda run -n quant python -m pytest tests/test_crypto_strategy_dsl.py tests/test_crypto_portfolio_backtest.py -q`
      要点：动 schema = 大改动，**先写 BLOCKED.md 停机**确认字段形状与迁移方式。

- [ ] 08 crypto 引擎迁移到共用执行器（消灭两个执行器） —
      验收：`cd backend && conda run -n quant python -m pytest tests/test_crypto_strategy_engine.py tests/test_strategy_engine.py tests/test_strategy_runtime.py -q`
      要点：这条链路**正在跑真钱**，涉及文件远超 5 个 → 必然停机拆分后再做。

## 范围之外

- **真券商接入**（S5 §3c）：涉及密钥、对外网络请求、真实下单 —— 按改动分级属于大改动，
  且需要 Jason 先定券商与账户。**不进本轮 PLAN**，要做单独立项。
- **doc14 的 P1-1/P1-2/P1-3/P1-4/P2-*/P3-***：路径全是大重构前的，未核实，不在本轮范围。
- **投资组合模块**：未开工，排在 doc14 卡片之后。
- **第 14 步 mypy 强检扩域**：与本轮无关。
- ⛔ 顺手「改进」crypto 现有行为 —— S5 §5 明说那会让回归判定失去意义。

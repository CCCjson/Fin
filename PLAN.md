# 实现计划 — doc15 S5 收尾 + 零碎欠债

> 来源：`docs/15.策略竞技场/00-PLAN.md` §5 与 `S5-股票地基.md` §3c，
> 外加 `docs/14.具体实现计划/00-PLAN.md` 的 P1-6 A 段。
> **开工前先读对应卡片**，别只看这里的一行标题。

每条任务的粒度：一轮能做完，有一条能跑的验收命令，改动不超过 3 到 5 个文件。

⚠️ 所有 Python 命令必须走 `conda run -n quant`，且在 `backend/` 目录下跑 pytest。
⚠️ 改完后端要生效必须 `bash restart.sh --backend`（App 无热更新）。

## 任务

### A. 股票竞技场（S5 §3c 欠债 —— 台账已建好，但上层不认它）

- [ ] 01 `strategy_pnl` 按 market 分叉：股票走 `strategy_trades`，crypto 保持读 `crypto_trades` 不动 —
      验收：`cd backend && conda run -n quant python -m pytest tests/crypto_strategy/test_performance.py tests/test_strategy_runtime.py -q`
      要点：paper/live 分桶不合并（S4 教训）；crypto 分支**一行都不许改行为**，回归全绿是判据。

- [ ] 02 `daily_returns` 支持股票策略：从 `strategy_trades` 算日收益序列，窗口按**市场当地日**切 —
      验收：`cd backend && conda run -n quant python -m pytest tests/crypto_strategy/ -q`
      要点：这是四道门槛门槛②的输入，序列口径错了整个竞技场就是错的；
      ⚠️ 已知缺陷「序列只含已实现盈亏 → 偏袒亏了死扛」**本轮不修**（见任务 04）。

- [ ] 03 `arena.py` 按市场分族评比：股票策略不该和 crypto 抢同一个卫冕位 —
      验收：`cd backend && conda run -n quant python -m pytest tests/crypto_strategy/test_arena.py tests/test_arena_routes.py -q`
      要点：裁决 7「同一时期只有一条 live」应理解为**每个市场族内**一条；
      ⚠️ 若这与裁决 7 原文冲突不可自行拍板 → 写 BLOCKED.md 停机问 Jason。

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

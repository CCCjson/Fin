# 交接备忘

<!-- 每轮结束时整份覆写。这是 /clear 之后唯一存活的记忆。 -->
<!-- 全部完成时，在下方单独起一行写 ALL_DONE（必须顶格），循环脚本靠它停机。 -->

## 当前

下一步：**01 `strategy_pnl` 按 market 分叉**（股票走 `strategy_trades`，crypto 保持不动）

## 上一步做了什么

第 0 轮，只做了脚手架，**没有碰任何生产代码**：

- 跑 `.claude/skills/step-loop/scripts/init.sh` → 建 `PLAN.md` / `PROGRESS.md` /
  `DECISIONS.md` / `.claude/agents/code-reviewer.md`（后者被 gitignore，不入库）
- 建分支 `feat/s5-stock-arena`，Jason 拍板不直推 master（见 DECISIONS 第 1 条）
- 把 doc15 S5 §3c 的欠债 + doc14 P1-6 A 段翻译成 8 条带验收命令的任务

## 下一步需要知道的

**任务 01 的现场勘察结论（已核实到行，别重查）：**

- `crypto_strategy/performance.py:295 strategy_pnl()` 是入口；它下面两条腿：
  - live → `_live_block()` → `_live_order_ids():114` 查 `CryptoTrade.source_kind/source_ref`
  - paper → `_paper_legs():202` 从 `crypto_strategy_runs.decision_detail` 回放，
    **paper 刻意不写 `CryptoTrade`**（S4 的隔离设计，⛔ 别改）
- 股票侧台账已就绪：表 `strategy_trades`（模型 `data_engine/storage/models.py:1377`），
  写入 `strategy_runtime/ledger.py::record_fill`，读取 `::strategy_fills`。
  它**从第一天就按 market 分叉**且 paper/live 都写（有 `mode` 列）—— 跟 crypto 那条
  「paper 不落台账」的路子**不一样**，01 的分叉逻辑要照顾到这个差异。
- ⚠️ `StrategyTrade.quantity` 是 `Integer`（股票按股/整手）。crypto 将来迁过来要改 Float，
  模型 docstring 里写了这笔账。
- `standings():679` **已经遍历所有策略不分 market**，所以股票策略其实已经在排行榜里，
  只是 `strategy_health` → `strategy_pnl` 算不出数 → 显示为空。**01 修的就是这一环。**
- 策略表是共用的：股票策略也存在 `crypto_strategies`，靠 `market` 列区分
  （`crypto_strategy/service.py:37-39`，那里有一段「漏了 market 会静默永不触发」的血泪注释）。

**流程上的坑：**

- 测试从 `backend/` 跑（`pyproject.toml:403 testpaths=["tests"]`），命令一律
  `cd backend && conda run -n quant python -m pytest ... -q`
- ⚠️ `conda run` 会吞 stdout，调试时加 `--no-capture-output`
- 改完后端要在 App 里生效必须 `bash restart.sh --backend`（App 无热更新）
- 判据铁律：crypto 现有测试**一条都不许红**，那是公共层抽取的回归防线（S5 §5）
- 🔴 **push 必须走 HTTPS，裸 `git push` 一定失败**（SSH key 全废）。已验证可行的命令：
  `gh auth switch --user CCCjson && git push https://github.com/CCCjson/Fin.git feat/s5-stock-arena`
  （第 0 轮已跑通，upstream 已设成 HTTPS URL，之后裸 `git push` 应该就能用了）
- ⚠️ `timeout` 命令在这台 mac 上不存在，别用它包 git 命令

## 未解决

- **code-reviewer subagent 本 session 加载不到**（subagent 在 session 启动时加载，
  它是这一轮刚创建的）→ Jason 已确认**重启 session 后再跑第一步**，所以第 0 轮没有审核环节
  （也没有生产代码可审）。下一轮起审核正常。
- **任务 03 有裁决冲突风险**：裁决 7 说「同一时期只有一条 live」，但股票和 crypto 共用
  `crypto_strategies` 表 —— 按字面执行会让股票策略和币策略抢同一个卫冕位。
  我的判断是应理解为「每个市场族内一条」，但**这是改裁决，做到 03 时必须停机问 Jason**。
- **任务 04 是 S3 的已知缺陷**（日收益序列只含已实现盈亏 → 偏袒「亏了死扛」），
  01/02 会沿用这个有偏口径，属于**刻意的分步**，别在 01/02 里顺手修。
- 真券商接入被明确排除在本轮之外（涉及密钥 + 真实下单，要单独立项）。

# 交接备忘

<!-- 每轮结束时整份覆写。这是 /clear 之后唯一存活的记忆。 -->
<!-- 全部完成时，在下方单独起一行写 ALL_DONE（必须顶格），循环脚本靠它停机。 -->

## 当前

下一步：**03 `arena.py` 按市场分族评比** —— ⚠️ **这一轮开头就要停机问 Jason**（见「未解决」第一条）

## 上一步做了什么

任务 02 完工：`daily_returns` 支持股票策略。改了 5 个文件：

- `crypto_strategy/performance.py` —— 新增 `_stock_daily_returns` / `_series_days`；
  `_paper_daily_pnl` 加 `market=` 参数（默认 CRYPTO，crypto 行为不变）；
  `_market_of` 重命名为公开的 `strategy_market`。
- `crypto_strategy/arena.py` —— 新增公开出口 `snapshot_of()`；`_snapshot` 带上
  `market` / `calendar_confidence` / `heuristic_days`；「口径可比」门槛加卡同市场。
- `crypto_strategy/service.py` —— `_gates_snapshot` 改走 `snapshot_of`（不再手拼），
  留痕 JSON 加记 `verdict`。
- 两个测试文件各加一节（股票序列 9 条 / 跨市场门槛 3 条）。

审核：**三轮**才 PASS。前两轮各一条 BLOCKING，都不是小事：
1. 我的测试用 `utcnow().date()` 算 `days_ago`，而造数据用市场当地日 →
   UTC≥16:00（北京 0-8 点）整体偏一天，**每天红 8 小时**，而且偏偏是「按当地日切」
   那两条钉子在自欺。已改成显式 `on=date(...)`，并用「时钟 +10h」的 pytest 插件实证。
2. `same_market` 在 `_gates_snapshot` 那条路径上**静默失效**（手拼的 dict 没有
   `market` 键 → `None == None` → 判过），而那是切换留痕的唯一依据。
   已改成走同一个出口 + fail-closed，并实证改前 True/True → 改后 False/False。

验收：`pytest tests/crypto_strategy/ -q` → 153 passed；全量 → **2090 passed**；ruff 全过。

## 下一步需要知道的

**任务 03 开工前必须先停机问 Jason 的那件事（材料已备齐，直接用）：**

裁决 7 说「同一时期只有一条 live」。股票和 crypto **共用 `crypto_strategies` 表**，
按字面执行 = 股票策略和币策略抢同一个卫冕位。我的判断是应理解为「每个市场族内一条」，
但这是**改裁决**，必须 Jason 拍板。停机时要一起端上去的两处实现细节：

- `crypto_strategy/service.py::_demote_others` 的条件是 `CryptoStrategy.mode == "live"`，
  **不分市场** —— arm 一条 A 股策略会顺手把正在跑真钱的 crypto 策略 `superseded +
  enabled=0`。🔴 判定层改了分族、这处退位逻辑不改的话，「每市场一个卫冕者」在**数据层
  根本立不住**。
- `arena.current_champion()` / `evaluate_arena()` 仍然只有**一个全局卫冕者**。
  02 加的 `same_market` 门槛是**止血**：一旦有股票策略 arm 成 live，crypto 侧挑战者
  会集体卡在 comparable、整个竞技场「无结论」。这是诚实降级，03 分族后自然解开。
- `arena._arena_verdict` 的措辞（现在会让 Jason 读成「挑战者都不行」而不是「这个市场
  没有卫冕者」）**刻意留到 03 一起改** —— 先写一句「等分族」等于替 03 预设了裁决 7 的读法。

**02 定下的口径（改之前先想清楚为什么）：**

- 股票序列按**该市场的交易日**补齐（crypto 仍是自然日）。`calendar_confidence` 是**三态**：
  `certain` / `partial`（日历滞后于收盘，**常态**）/ `suspected`（整段是工作日启发式）。
  🔴 两态的话这个标记会**恒亮**（日历靠收盘后的指数 bar 自举，永远比今天晚）——
  一个永远亮着的警告灯等于没有警告灯。
- ⛔ **日历内部的空洞刻意不补**：「工作日集合 − 日历集合」会把**国庆七天**判成七个缺口。
  中等空洞（90 天丢 5-8 天）属于 `gap_engine` 的责任面，不在这一层解决。
- 🔴 **成交是事实、日历是推断**：台账上有成交但日历里没有那天 → 那天照样进序列
  （`off_calendar_days` 报出来）。反过来做就是把真金白银赚的钱悄悄删掉。
- 分母是**全局**总资金设置（`UserSettings.total_capital`，默认 5000），不分市场 ——
  所以跨市场收益率**不可比大小**，这正是 03 要分族的原因。
- `arena.snapshot_of()` 是 snapshot 的**唯一出口**。⛔ 别再手拼第二份 dict：
  门槛全靠 `dict.get()` 读，漏一个键不报错、只静默放行。这个坑已经踩过一次。

**流程上的坑（仍然有效）：**

- 测试从 `backend/` 跑：`cd backend && conda run -n quant python -m pytest ... -q`
- ⚠️ `pytest` 不认 `--timeout=`；`conda run` 吞 stdout（调试加 `--no-capture-output`）；
  `timeout` 命令在这台 mac 上不存在
- ⭐ **造带日期的测试数据时，`days_ago` 一律从 `market_today(market)` 起算**，
  ⛔ 不是 `utcnow().date()` —— 断绝对日期就直接传 `on=date(...)`。
  验证时钟依赖的办法：写个 pytest 插件把 `common.market_time.utc_now`/`market_now`
  整体 +10h 再跑（这轮用过，有效）。
- 改完后端要在 App 里生效必须 `bash restart.sh --backend`（App 无热更新）
- 🔴 push 走 HTTPS：`gh auth switch --user CCCjson && git push https://github.com/CCCjson/Fin.git feat/s5-stock-arena`
- 判据铁律：crypto 现有测试**一条都不许红**

## 未解决

- 🛑 **任务 03 必须先停机**（材料见上）：裁决 7 的读法 + `_demote_others` 跨市场退位。
- 🔴 **股票策略不写 `crypto_strategy_runs` 行**（01 就发现的，02 没解决）。
  `strategy_health` 在 `if not runs:` 提前返回 `ok=False` → **不会调 `strategy_pnl`** →
  `standings` 给股票策略 `pnl: None` → `/arena` 页仍空白。
  ⚠️ 也就是说 01+02 算得出数了，**数还是没走到屏幕上**。03 要么让股票 tick 也写 run 行，
  要么让 `strategy_health` 在「没有 run 但台账有成交」时不提前返回。
  🔴 动 `strategy_health` 的返回形状要当心：它提前返回时**没有 `runs` 键**，
  前端类型写成必填，上次因此整个 App 变错误页（memory `trading-terminal-s7`）。
- 🔴 **接真券商时必须补「成交回报回填台账」**：现在只有 `FILLED`/`PARTIAL_FILLED`
  当场写台账，挂着的限价单后来成交了不会自动补记 → 限价单战绩系统性缺失。
- ⚠️ 命名不一致（留给 04 顺手统一）：crypto live 块叫 `book_positions`，
  crypto paper 与股票块叫 `open_positions`。04 要用持仓算浮动盈亏，别只认一个。
- **任务 04 是 S3 的已知缺陷**（序列只含已实现盈亏 → 偏袒「亏了死扛」）。
  01/02 都刻意沿用了这个有偏口径。
- 真券商接入被明确排除在本轮之外（涉及密钥 + 真实下单，要单独立项）。

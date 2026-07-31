# 16. 数据积累与监控重构 — 总实现方案

> 立项：2026-07-27 ｜ **B1–B5 + F1–F4 全部完工（2026-07-27），2026-07-31 实跑复盘修了 3 个 bug**
> 前置阅读：`docs/ARCHITECTURE.md`、`docs/GOTCHAS.md`、本文件 §五（红线）、§七（实施偏离）

---

## 完工状态速查

| 期 | 内容 | 状态 |
|---|---|---|
| B1 | 注册表 `registry.py` + 事件契约 `events.py` + 日历 `trading_calendar.py` | ✅ |
| B2 | `data_gaps` 表 + `gap_engine.py` + `gap_fill.py` + `gap_job.py` | ✅ |
| B3 | `orchestrator.py` + `update_all_job.py` + `asset_status.py` + 9 个新端点 | ✅ |
| B4 | 主链改 registry 驱动 + 缺口 job 挂载 + 4 调度器统一开关 | ✅ |
| B5 | 实时快照 count 恒为 0、新闻 8 小时时差 | ✅ |
| F1–F4 | service 重写 + 7 个 section 组件，`DataMonitor.tsx` 1028 → 250 行 | ✅ |

**新增文件**：`data_engine/{registry,events,trading_calendar,gap_engine,gap_fill,gap_job,orchestrator,update_all_job,asset_status,scheduler_registry}.py`、`components/datamonitor/sections/{shared,ControlBar,RunProgress,GapPanel,AssetMatrix,SchedulerPanel,IngestDrawer,RunHistory}.*`
**新增门禁**：`tests/data_engine/{test_gap_engine,test_asset_status_gate}.py`（17 条）

---

## 零、这份方案要解决什么

Jason 的四条需求，逐条对应到现状：

| # | 需求 | 现状 | 缺口 |
|---|------|------|------|
| 1 | 定时运行，多市场数据更新、积累 | 4 个互不知情的 scheduler，只有 A 股主链有开关 | 港美股/crypto/新闻 job 界面上看不见也关不掉；财报/行业/深历史无定时通道 |
| 2 | 手动一次性更新全部 | **不存在**。7 个按钮各点各的 | 无统一入口、无任务队列、无总进度、关页面就断 |
| 3 | 已有数据不再重复更新 | **做得最好**，每个 updater 内部都有增量判定 | 口径各写各的、命名不一、UI 上讲不清"跳过了多少" |
| 4 | 监控界面该有的布局 | 1028 行单文件，17 个区块竖着堆一列 | 层级平坦、三套告警打架、看数据和跑任务混在一起 |

**Jason 补充裁决（2026-07-27）**：
- 🔒 **不上 launchd/系统 cron** —— 后端开着才自动跑，这是接受的边界
- 🔴 **中间空缺的天数要自动补齐** —— 这是本次新增的核心能力，现有补跑逮不住中间的洞

---

## 一、五个核心架构决定

### 决定 1：一张注册表驱动四件事

病根是「调度、增量判定、事件格式、界面卡片」四处各写各的。新增**数据资产注册表** `data_engine/registry.py`，一张表同时驱动：

```
DataAsset  ──┬──> 定时调度（遍历注册表挂 job）
             ├──> 一键全量（拓扑排序依次跑）
             ├──> 缺口扫描（遍历调 gap_scan）
             └──> 前端资产矩阵（直接映射成格子）
```

一个「资产 × 市场」= 一个 `DataAsset`，例如 `daily.a_share`、`daily.hk_stock`、`financial.a_share`、`kline.crypto`。

### 决定 2：交易日历用「基准指数自证 + 尝试结果反证」

**这是「自动补齐空缺天数」的技术前提，也是最大的难点。**

项目里**没有交易日历模块**（`daily_pipeline_scheduler.py` 多处注释确认），而判缺口必须知道"哪天本该有数据"。方案分三级：

1. **首选 · 基准指数自证（确定性，零误报）**
   A 股用上证指数、港股用恒生指数、美股用标普 500、crypto 用自然日。
   **指数在某天有 bar ⟺ 那天是交易日**。指数只有 1 只票，拉起来几秒钟。
   → 注册为优先级最高的资产 `calendar.{market}`，每次 gap 扫描前先自己刷新。
   → 🎁 顺带填掉 memory 里记的「港美股基准指数缺口」这个已知洞。

2. **兜底 · 启发式（只报不补）**
   指数拉不到时，退回「工作日集合 − 已达标日集合」。**标记为 `suspected` 只展示，绝不自动烧 IP** —— 港美股误判一次要白打 10-15 分钟 Yahoo，代价不对称（见 `_OVERSEAS_CATCHUP_STALE_DAYS` 的注释）。

3. **反证 · 用尝试结果确认假期**
   `data_gap` 表里补了 N 次仍无数据的日子标 `permanent` 不再重试。
   **这实际上就是实测出来的交易日历** —— 不用引第三方日历库，也不会陷入"每次启动都重试同一个补不上的洞"。

### 决定 3：缺口是**落库的一等公民**，不是算出来就扔

新增 `data_gap` 表。理由：前端要展示"缺哪几天"、`permanent` 状态要持久化、补齐进度要可续。

### 决定 4：长任务走 `BaseSingletonJob`，不走 HTTP 流

现在手动更新是 HTTP NDJSON 流（`/data/update-daily/stream`），**关掉页面就断**。一键全量可能跑 30 分钟以上，必须改成「后台任务 + 轮询查进度」。

现成框架就在 `data_engine/base_job.py`（`BaseSingletonJob`），已被 4 个 job 用着，自带单例线程 + 三层 self-heal。**直接复用，不新造轮子。**

> ⛔ 复用它还有一层意义：它用的是裸 `threading.Thread(daemon=True)`，符合 memory 里「⛔ ThreadPoolExecutor 的 `__exit__`/atexit 都会 join 卡死 worker」那条禁令。

### 决定 5：保留 4 个 scheduler 的物理隔离，只统一执行入口

不合并 scheduler —— 它们的 trigger 语义真的不同（工作日 cron vs 7×24 interval vs 15 分钟）。
但**新增 `data_engine/orchestrator.py` 作为唯一执行入口**：定时触发、手动全量、缺口补齐，三条路都调它。scheduler 退化成"到点了喊一声"。

---

## 二、后端实现（先做，分 5 期）

### B1 — 契约与注册表

**交付**
- `data_engine/registry.py` — `DataAsset` 定义 + 全部资产登记
- `data_engine/events.py` — 统一事件契约
- `common/trading_calendar.py` — 交易日历真源（基准指数自证）

**`DataAsset` 形状**
```python
@dataclass(frozen=True)
class DataAsset:
    key: str                       # "daily.a_share"
    label: str                     # "A股日线"
    group: Literal["calendar", "quote", "fundamental", "sentiment", "derived"]
    market: str | None             # canonical market，跨市场资产为 None
    cadence: Literal["daily_after_close", "interval", "on_demand"]
    depends_on: tuple[str, ...]    # 拓扑排序用："signals" 依赖 "daily.a_share"
    in_update_all: bool            # 是否纳入「一键更新全部」
    supports_gap_fill: bool        # 是否支持中间洞回补
    runner: Callable[..., Iterator[dict]]   # 执行体，yield 统一事件
    gap_scan: Callable | None      # 缺口扫描
    enabled_env: str | None        # env 门控键
```

**首批登记的资产**

| key | 归属 | 纳入一键全量 | 支持补洞 | 现有实现 |
|---|---|---|---|---|
| `calendar.a_share` / `.hk_stock` / `.us_stock` | calendar | ✅ | — | **新建**（基准指数） |
| `daily.a_share` | quote | ✅ | ✅ | `DailyUpdater` |
| `daily.hk_stock` / `daily.us_stock` | quote | ✅ | ✅ | `OverseasDailyUpdater` |
| `kline.crypto` | quote | ✅ | ✅ | `CryptoUpdater` |
| `financial.a_share` | fundamental | ✅ | ❌（按报告期不按天） | `financial_updater` |
| `valuation.a_share` | fundamental | ✅ | ❌（**只能从当天续，历史不可回填**） | `scripts/backfill_valuation` |
| `industry.a_share` | fundamental | ✅ | ❌ | `industry_updater` |
| `news.*` | sentiment | ✅ | ❌ | `news_engine` |
| `limit_up.a_share` | derived | ✅ | ✅ | `limit_up_engine` |
| `signals` / `tracking` / `decision_outcome` | derived | ✅ | ✅ | 主链现有 3 步 |
| `deep_history.*` / `knowledge.*` / `research_report` / `cninfo` / `arxiv` | on_demand | ❌ | — | 各 `BaseSingletonJob` |

> **`in_update_all=False` 的边界（我的判断，Jason 可推翻）**：深历史/知识库/研报/巨潮/arXiv 是**小时级**任务，且深历史和港美股日线**抢同一把 Yahoo 锁**（`yahoo_job_lock`）会互相阻塞。混进"一键全部"会让人误点后卡住半天。它们仍在注册表里登记（矩阵能看到状态），只是不参与全量。

**统一事件契约**（所有 runner 必须 yield 这个形状）
```jsonc
{
  "event": "start|progress|complete|error|skipped",
  "asset": "daily.a_share",
  "market": "a_share",
  "total": 5201,        // 本次待更新数（不含已最新跳过的）
  "current": 1203,
  "fresh_skipped": 5100,// 已最新、主动跳过
  "updated": 100,
  "failed": 1,
  "records": 100,
  "note": "人读的当前状态"
}
```
> ⚠️ 现在三个 updater 分别 yield `skipped_fresh` / `skipped_fresh` / `skipped`，命名不一。**归一到 `fresh_skipped`**，用适配函数包住老 runner，不改动 updater 内部逻辑（降低回归风险）。

**验收**：`pytest` 门禁测试遍历注册表，断言每个资产的 runner 产出的事件都符合 schema。

---

### B2 — 缺口引擎（本次的核心新能力）

**交付**
- `data_engine/storage/models.py` 新增 `DataGap` 表
- `data_engine/gap_engine.py` — 扫描 + 补齐
- `data_engine/gap_job.py` — `BaseSingletonJob` 子类，可停止的补洞任务

**表结构**
```python
class DataGap(Base):
    __tablename__ = "data_gaps"
    id, asset, market
    gap_date        # Date，缺的那一天
    status          # open / filling / filled / permanent / suspected
    confidence      # "certain"(指数自证) / "suspected"(启发式)
    attempts        # 尝试次数
    detected_at, last_attempt_at, filled_at
    note
    __table_args__ = (UniqueConstraint("asset", "gap_date"),)
```

**扫描逻辑**
```
1. 刷新 calendar.{market}（基准指数）→ 得到确定的交易日集合 T
2. 查该市场 DailyQuote 中「覆盖数达标」的日期集合 H
   （复用 common/market_freshness.reference_trading_date 的中位数基线，
     ⛔ 绝不能用 max(date) —— 见 GOTCHAS「一个数看着没问题，其实什么都没检查」）
3. gap = T - H，落库 status=open, confidence=certain
4. 指数拉不到 → gap = 工作日 - H，落库 status=suspected（只报不补）
5. attempts >= MAX_GAP_ATTEMPTS(默认3) 且仍无数据 → status=permanent
```

**补齐逻辑**
- 连续 gap_date 合并成区间，减少请求数
- **A 股**：走 `DailyUpdater` 慢路径按 `[start, end]` 区间拉（5200 只 × N 天，重，必须可中断 + 走代理铁律）
- **港美股**：`OverseasDailyUpdater._start_for()` 已支持任意起点，yfinance 一次拉区间，代价可控
- **crypto**：`CryptoUpdater.backfill_klines(lookback_days)` 现成
- 写入一律 upsert / `INSERT OR REPLACE`，**不会重复插**

**触发时机**
| 时机 | 行为 |
|---|---|
| 后端启动后 `GAP_SCAN_DELAY`（默认 300s，排在现有 180s 补跑之后） | 扫描 + 自动补 `certain` 缺口 |
| 每日主链跑完后 | 扫描 + 自动补 |
| 前端点「补齐」 | 手动补指定缺口 |

> ⛔ **不删现有 `_catchup_job`**。它管的是「尾部落后」（最近该有的那天没有），gap 引擎管的是「中间的洞」，两者互补。等 gap 引擎稳定跑一段时间后再评估合并。

**验收**：造一个「07-10~07-15 空、07-16 有数据」的库，扫描能报出 4 个交易日缺口（不含周末），补齐后 `filled`；造一个假期日，补 3 次后自动 `permanent`。

---

### B3 — 编排器与运行时

**交付**
- `data_engine/orchestrator.py` — `run_assets(keys, mode)` 唯一执行入口
- `data_engine/update_all_job.py` — `BaseSingletonJob` 子类，「一键更新全部」的载体
- 统一写 `DataUpdateLog`

**编排器职责**
1. 按 `depends_on` 拓扑排序（日线 → 信号 → 追踪 → 决策后验）
2. 依次调 runner，收集统一事件，更新 job 的 `snapshot()`
3. **每个资产跑完写一行 `DataUpdateLog`** —— 现在只有部分 updater 写，所以"最近更新日志"是残缺的
4. 单个资产失败只记 warning，不中断后续（沿用主链现有语义）
5. 港美股共抢一次 `yahoo_job_lock`（沿用 `market_refresh._refresh_overseas`）

**新 API（挂在 `/data-monitor` 下，把动作端点收回来）**
```
POST /data-monitor/runs              发起运行 {scope:"all"|[keys], mode:"incremental"|"gap_fill"}
GET  /data-monitor/runs/current      当前运行的进度快照（含每个资产的子进度）
POST /data-monitor/runs/stop         停止
GET  /data-monitor/runs/history      最近 N 次运行
GET  /data-monitor/gaps              缺口列表
POST /data-monitor/gaps/fill         补指定缺口
GET  /data-monitor/assets            注册表快照（资产矩阵的数据源）
GET  /data-monitor/schedulers        4 个调度器的统一状态
POST /data-monitor/schedulers/{id}/toggle   逐个开关
```

> 现有 `/data/update-daily/stream`、`/data/refresh/stream` **保留不删**（有别处在用），新面板走新端点。

**验收**：点一次全量，关掉页面，重开还能看到进度；`DataUpdateLog` 每个资产都有行。

---

### B4 — 调度接线

**交付**：`daily_pipeline_scheduler` 从「硬编码 6 步」改成「registry 驱动」；新增 gap 扫描 job；`/data-monitor/schedulers` 统一暴露 4 个调度器。

改动要点：
- `_run_pipeline_sync()` 的 6 步硬编码 → `orchestrator.run_assets(cadence="daily_after_close", market="a_share")`
- `scheduler/toggle` 从只管 `JOB_ID` → 按 scheduler id 逐个开关（A股主链 / 港美股 / crypto / 新闻）
- 前端 TS 类型补上后端早就返回却没人用的 `overseas_*` 字段

---

### B5 — 顺手修的口径 bug

1. 🔴 **`_asset_news` 的 8 小时时差**（`api/routes/data_monitor.py:100`）
   `week_ago = datetime.now()` 是本地 UTC+8，但 `NewsArticle.published_at` 存的是 naive UTC（`news_engine/fetcher.py` 用 `utc_now()`）→ 近 7 天条数系统性少算 8 小时的量。
   改成 `utc_now()`。属于时区统一工程的漏网，见 memory `timezone-unification`。
2. **`assets` 全是 A 股口径** —— 财报/估值/新闻/涨停四张卡只统计 A 股，crypto 在资产网格里完全缺席。改成注册表驱动后自然按「资产 × 市场」分开报。

---

## 三、前端实现（后端稳定后做）

### 布局设计

```
┌─ 总控条（sticky）───────────────────────────────────────────────┐
│ 🛰️ 数据监控          [🔄 更新全部]  [⚙️ 自动更新 ●开]           │
│ 🇨🇳07-25 ✅  🇭🇰07-25 ✅  🇺🇸07-23 ⚠️  🪙07-27 ✅               │
│ 下次自动 15:35 · 服务器 07-27 14:02 · 空闲慢刷 60s              │
└──────────────────────────────────────────────────────────────────┘

┌─ 当前运行（有 run 才出现，置顶）───────────────────────────────┐
│ ████████████░░░░░░  62%   已用 4:32  预计还需 2:40   [⏹ 停止]  │
│  ✅ 上证指数日历   1/1                                          │
│  ✅ A股日线       101/101  · 已最新跳过 5100                    │
│  ⏳ 港股日线      1203/2800                                     │
│  ⏸ 美股日线      排队中                                         │
│  ⏸ 财报 · 估值 · 新闻 · 涨停  排队中                            │
└──────────────────────────────────────────────────────────────────┘

┌─ ⚠️ 数据缺口（有洞才出现）─────────────────────────────────────┐
│ 🇨🇳 A股   07-10 ~ 07-15   4 个交易日   已确认  [补齐]          │
│ 🇺🇸 美股  07-11            1 个交易日   已确认  [补齐]          │
│ 🇭🇰 港股  07-03            1 天  疑似（无日历，需确认） [忽略]  │
│                                            [全部补齐]           │
└──────────────────────────────────────────────────────────────────┘

┌─ 数据资产矩阵 ────────────────────────────────────────────────┐
│           🇨🇳A股      🇭🇰港股     🇺🇸美股    🪙Crypto           │
│ 📈 日线   ✅ 07-25   ✅ 07-25   ⚠️ 07-23   ✅ 07-27           │
│           99.2%      98.1%      94.0%      100%               │
│ 📊 财报   ✅ 78%      ─          ─          ─                  │
│ 💹 估值   ✅ 07-25    ─          ─          ─                  │
│ 📰 新闻   ✅ 2h前    ✅ 2h前    ✅ 2h前    ✅ 30m前            │
│ 🔥 涨停   ✅ 07-25    ─          ─          ─                  │
│ 🏭 行业   ✅ 100%     ─          ─          ─                  │
│           每格可点 → 抽屉展开该资产详情 + 单独「更新这一格」    │
└──────────────────────────────────────────────────────────────────┘

┌─ ⏰ 定时任务（4 个调度器，各自开关）──────────────────────────┐
│ A股主链      15:35 周一~五   下次 明天15:35   ●开  上次✅      │
│ 港美股日线   16:30 周一~五   下次 今天16:30   ●开  上次✅      │
│ Crypto 7×24  每 30 分钟      下次 14:20       ●开  上次✅      │
│ 新闻抓取     每 15 分钟      下次 14:10       ●开  上次✅      │
└──────────────────────────────────────────────────────────────────┘

┌─ ▸ 摄入任务（默认收起）───────────────────────────────────────┐
│   深历史回补 / 巨潮财报 / 东财研报 / arXiv / 新闻任务 /        │
│   抓取监控 / 知识库                                             │
└──────────────────────────────────────────────────────────────────┘

┌─ 📋 运行历史 ─────────────────────────────────────────────────┐
```

### 设计要点

1. **资产矩阵是核心改动** —— 二维（资产 × 市场）替代现在一维的 7 张卡。一眼看出"美股日线落后了"，而不用逐张卡读。
2. **三套告警合并** —— 现在 `catch_up` 横幅 + 顶部告警横幅 + 每卡角标口径重叠。合并成：总控条上的 4 个市场胶囊（健康度）+ 缺口区（具体缺什么）。
3. **看 / 跑 分区** —— 上半屏是"数据健康状态"（只读为主），摄入任务全部塞进折叠区。
4. **进度区置顶且持久** —— 后台任务 + 轮询，关页面回来还在。
5. **每格可下钻** —— 点矩阵里的格子开抽屉，看该资产的详细统计 + 单独更新按钮，避免一屏塞太多。

### 分期

- **F1** — `dataMonitorService.ts` 重写：新端点 + 类型（含后端早就有却没声明的 `overseas_*`）
- **F2** — 总控条 + 当前运行进度区（`sections/ControlBar.tsx`、`sections/RunProgress.tsx`）
- **F3** — 资产矩阵 + 下钻抽屉（`sections/AssetMatrix.tsx`）
- **F4** — 缺口区 + 定时任务区 + 摄入任务折叠 + 运行历史

`DataMonitor.tsx` 从 1028 行拆到 ~150 行的组装壳 + `components/datamonitor/sections/` 下 6 个组件。现有 9 个摄入面板**原样搬进折叠区，不改内部实现**。

---

## 四、风险与红线

| # | 风险 | 对策 |
|---|---|---|
| 1 | 🔴 **交易日历判错 → 误报缺口 → 大量空拉** | 分三级：指数自证(certain,自动补) / 启发式(suspected,只报不补) / 尝试反证(permanent,不再试)。港美股误判一次要白打 10-15 分钟 Yahoo |
| 2 | 🔴 **A 股中间洞回补很重**（5200 只 × N 天走慢路径） | 独立 `BaseSingletonJob`、可停止、限并发、**必须走代理铁律**（⛔ 任何场景不许降级直连，见 `CODING_STANDARDS.md` §8.2） |
| 3 | 平滑替换现有补跑 | `_catchup_job` **不删**，gap 引擎并行跑一段时间再评估 |
| 4 | SQLite 并发（后台任务 + API 抢库） | 沿用现有 `get_session()` per-thread 模式，不引新连接池 |
| 5 | 覆盖率统计漏 market 过滤 | ⛔ 所有跨市场聚合必须带 `market ==` 过滤，见 GOTCHAS「日线覆盖率 300% bug」 |
| 6 | 判新鲜度用 `max(date)` | ⛔ 绝对禁止，用中位数基线。见 `_coverage_on` 的注释 |
| 7 | 后台线程 join 卡死 | ⛔ 不许用 `ThreadPoolExecutor`，复用 `BaseSingletonJob` 的裸 daemon Thread |

**不碰的东西**：风控模块、`agents/confirm_gate.py`、策略层、交易执行层 —— 本次纯数据层 + 展示层，与它们零交集。

---

## 五、不做什么

- ❌ **不上 launchd / 系统 cron**（Jason 拍板：后端开着才跑）
- ❌ **不合并 4 个 scheduler**（trigger 语义真的不同，只统一执行入口和界面展示）
- ❌ **不引第三方交易日历库**（用基准指数自证 + 尝试反证，零依赖）
- ❌ **不删老端点**（`/data/update-daily/stream` 等保留，新面板走新端点）
- ❌ **不改 9 个摄入面板的内部实现**（原样搬进折叠区）
- ❌ **深历史/知识库不纳入「一键全部」**（小时级 + 抢 Yahoo 锁，会让人误点后卡半天）

---

## 七、实施偏离与实跑复盘（**改这块代码前必读**）

### 与方案不同的三处决定

1. **交易日历放 `data_engine/` 不是 `common/`**。`common/` 的分层约定是无 DB、无出网
   （`market_freshness.py` 是纯逻辑，DB 查询留在 `health.py`）。日历天然要查库 + 出网。
2. **`signals` 移出通用缺口扫描**。它自带 `detect_signal_gaps`（「有行情但无信号的
   交易日」），比通用的覆盖数比对精确，lookback 也已按落后天数自适应。两套判据会打架。
3. **`gap_mode` 分 coverage / presence 两种口径**（方案里没有）。涨停家数天天剧烈波动
   （30~300 家），拿「0.8×中位数」卡它会把每个弱势日都误报成缺口。日线才适合覆盖率口径。

### 2026-07-27 首跑实测

- **A 股日历零网络请求建成**：`000001.SH` 早就在 `daily_quotes` 里，自举出 4021 个交易日
  （2010-01-04 起）。港股 ^HSI 245 天 / 美股 ^GSPC 250 天，联网各拉一次几秒钟。
- ⭐ **日历自证的零误报得到实证**：`us_stock` 2026-07-03 库里只有 5 行（正常 11000+），
  看起来像个大缺口 —— 但 ^GSPC 那天没有 bar，因为**7/4 独立日落在周六、周五补休**。
  日历正确地没把它算成缺口。那 5 行是 OTC 粉单股票的杂数据。
- **实际补上 7 天涨停池**（07-10/13/14/15/16/17/23），补完缺口清零。

### 🔴 2026-07-31 实跑复盘：后台自己跑了几天，暴露 3 个问题

**① `_dict_runner` 的键冲突（我引入的，已修 + 门禁）**
被调函数返回的 dict 里有 `market` 键时，`**payload` 撞成
`make_event() got multiple values for argument 'market'`。`refresh_calendar()` 正好是
这个形状 → **三个 calendar 资产每次定时跑都当场失败，连着三天没人发现**。

为什么没人发现：编排器「单资产失败只记 warning 不中断」（这是对的），而矩阵那一格
读的是**表里的数据**不是本次运行结果，所以照样显示绿的。
→ 教训：`_dict_runner` 里 `event`/`asset`/`market` 三个保留键必须先剥掉。

**② 🔴 陈旧的日历掩盖陈旧的数据（设计缺陷，已修 + 门禁）**

港股日线停在 07-27、今天 07-31（落后 4 个工作日），矩阵却显示 **ok，behind=0**。
因为 ^HSI 也拉不到 → 日历自己停在 07-27 → 「latest 之后还有几个交易日」= 0。

**判据和被判对象来自同一条坏掉的链路，一起坏就一起「正常」** —— 这正是整套设计
要消灭的那类病（同 `_coverage_on` 那句「一个数看着没问题，其实什么都没检查」）。

修法两条，缺一不可：
- 判「日历停没停更」必须看**全局最新日**，不能看查询区间 `[latest, today]` 内的最大值
  （两者一起卡住时区间内最大值就是那个卡住的点，永远自洽）
- 日历不可用时**退回 `market_freshness.is_stale`** 这条独立判据，⛔ 绝不能落到 ok

**③ 🔴 港美股停更的真实根因：`load_dotenv(override=True)` 让死代理复活（已修 + 门禁）**

> 初判是「Clash 没开，环境问题」。**那个判断是错的** —— 深挖之后发现是代码问题。

`api/main.py:17-18` 在所有 import 之前调 `apply_proxy_env()` 清掉残留死代理
（`.env` 里 `HTTP_PROXY=http://127.0.0.1:7897`，Clash 关时指向死端口）。
**但紧接着的 `from api.routes import ...` 把它踩回来了** —— 那条 import 链拉起
`acquisition/config.py`，那里有 `load_dotenv(override=True)`。

于是整个后端进程带着一个指向死端口的代理跑，所有读 env 的 HTTP 库
（yfinance 的 curl_cffi、requests、akshare…）默认都往那儿发。

**最难查的部分是它一点都不像代理问题**：yfinance 报的是
`'NoneType' object is not subscriptable`（内部 `data['chart']['result']` 拿到空响应），
排查时全往「Yahoo 限速 / 网络不通」方向想。裸 curl 测出的 429 更是把人带偏了 ——
那只是没走 crumb 流程，清掉代理变量后 yfinance 直连**完全正常**。

与 `tests/conftest.py` 里记的 DATABASE_URL 泄漏是**同一个机理**（那份文档也明写
「这样的 `override` 全项目有六七处」）。

修法：`startup_event` 里**再调一次** `apply_proxy_env()`。两次都必须有 ——
文件头那次赶在 transformers import 之前（HF 校验要用），startup 那次收拾被
override 踩回来的。门禁 `tests/net/test_proxy_env_not_resurrected.py`。
⛔ 不违反代理铁律：`apply_proxy_env` 只在 `resolve_proxy()` 判直连时清，而它是
自适应的（Clash 活着就返 Clash URL）；国内抓取走快代理显式传 `proxies=`，不读 env。

修完实测：港股 3168 只 / 美股 11603 只跑通，27.5 分钟，港股追到 07-31、美股 07-30。

⚠️ 顺带暴露一个既有行为（未处理）：07-29 那次 `daily.us_stock` 白打了 **922 秒**
（yfinance 内部对失败自己退避重试，`SINGLE_FETCH_TIMEOUT` 没兜住）。属 `overseas_job` 既有问题。

**④ 🔴 回查基线的自证循环（已修 + 门禁）**

`fill_asset_gaps` 补完之后要回查「这几天到底补上没有」，原来是
`gap_probe(min(gap_dates), max(gap_dates))` —— **只探缺口那几天**。
而那个区间恰恰就是数据不全的区间，于是中位数基线被缺口自己拉低：

    扫描时  90 天窗口 → baseline=11324, 阈值 9059 → 5343 < 9059 → 报缺口 ✓
    回查时  只看那 2 天 → baseline= 5339, 阈值 4271 → 5343 > 4271 → 判已补 ✗

两个后果都很难看：**无限循环**（下次扫描又报，每 6 小时白补 8 分钟，日志上还一切正常）
+ **`permanent` 反证永久失效**（判 filled 就不累加 attempts）。

与「陈旧日历掩盖陈旧数据」是同一类病：**判据取自被判对象自身**。
修法：回查也用 `SCAN_LOOKBACK_DAYS` 完整窗口算基线，只是判定只看那几天。

**⑤ 🔴 `permanent` 会把「限速拉不全」误判成「那天是假期」（已修 + 门禁）**

`permanent` 的语义是「数据源那天根本没有数据」= 那天不是交易日。但「限速导致只拉到
一半」会走到**同一个终点**（都是补 N 次仍不达标）→ 那些日子会被**永久销号，再也不补**。

判据很干净：**假期不可能有任何 bar**。所以 `permanent` 只在 `observed == 0` 时才标。
实测现场美股 07-27 有 5343 只有数据（正常 11000+）—— 有数据就证明那天是交易日，
只是没拉全，该继续重试。

> 📌 遗留观察（不是 bug）：美股 07-27 之后 universe 从 ~10400 掉到 ~5340，缺失票按
> 字母序成片（'A','AA','AAA'…），是**批次级拉取失败**不是类型问题。`updated` 只数
> 有 records 返回的票，所以 `11603 - 5467updated - 900failed = 5236` 那批是
> 「拉到了但 df 为空」，既不算成功也不算失败 —— 静默。等 Yahoo 恢复后再补即可，
> 缺口表会一直挂着这几天不销号。

---

## 六、开工顺序

```
B1 契约与注册表  →  B2 缺口引擎  →  B3 编排器与运行时  →  B4 调度接线  →  B5 口径修复
                                                                              ↓
                          F1 service  →  F2 总控条+进度  →  F3 资产矩阵  →  F4 其余分区
```

每期结束跑一次 `restart.sh --backend` 验证，前端期结束跑全量 `restart.sh`（⛔ App 端没有热更新）。

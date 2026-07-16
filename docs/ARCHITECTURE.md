# 项目架构现状文档

> 最后更新：2026-07-07（架构收敛后）。本文档描述**代码当前的真实结构**，不是最初设想的理想化设计——那份历史设计见 `docs/archive/`。

## 0. 核心范式（改代码前必读）

2026-07 架构收敛后，功能分两条入口线：

- **页面驱动**：8 个前端工作台页面 → `api/routes/*.py` → engine/service → 数据库
- **对话驱动**：MoneyBill 聊天 → `agents/tools/*.py` → **同一批** engine/service（进程内直接 `import`，不经 HTTP）→ 数据库

大量能力（信号、复盘、自选、报告、顾问、交易记录……）已经**只有对话入口**，没有页面/route 了。很多下沉后的 service 层被两条入口共享复用——**改这类 service 时，两条入口都要验证**。

新增能力的铁律：**只做「引擎 + MoneyBill 工具」两层，不要再开独立页面/route**，除非是重可视化需求（K线、曲线级别）。

## 1. 系统定位

个人量化交易平台，服务 Jason 本人的 A股/港股/美股手动实盘决策，Python FastAPI 后端 + React 前端，MoneyBill 是唯一交互主入口（聊天式，非传统多页面 SaaS 布局）。

## 2. 后端顶层目录速查表

| 目录 | 职责 |
|---|---|
| `api/` | FastAPI 入口、路由、鉴权（JWT）、Pydantic schema |
| `agents/` | MoneyBill 多智能体编排层（详见第5节） |
| `data_engine/` | 行情/财务数据抓取、清洗、存储、ORM models、DB session |
| `analysis_engine/` | 技术指标、K线形态、信号检测 |
| `backtest_engine/` | **Python 回测引擎（遗留）**——只因 `alpha_lab` 依赖它才保留，见 GOTCHAS |
| `/backtest_cpp/`（仓库根，非 backend 下）| **C++ 正版回测引擎**，独立服务跑在 `localhost:8002` |
| `trading_engine/` | 券商对接（brokers：paper/easytrader）、风控（risk）、监控（monitor）|
| `automation/` | 待确认订单、持仓哨兵、价格预警、调度器、WS 推送 |
| `report_engine/` | 投研报告的五个章节成稿（分片采集/打分/联网搜索/章节成稿）|
| `knowledge_engine/` | 外置金融大脑：文档摄入、向量检索(RAG)、alpha idea 挖掘、逆向 API 爬取 |
| `advisor_engine/` | AI 投资顾问会话服务（被 `deep_stock` subagent 调用） |
| `cockpit_engine/` | 决策驾驶舱：技术/基本面/情感/ML/持仓五维打分 |
| `recommend_engine/` | 选股推荐：候选双源合并 + 三重闸门 BUY 判定 |
| `screener_engine/` | 基本面多因子选股 |
| `limit_up_engine/` | 涨停池分析 + 次日涨停候选预测 |
| `news_engine/` | 新闻抓取 + BERT情感分析 + LLM深度解读 |
| `prediction_engine/` | 股价涨跌预测（含远程 GPU 训练对接） |
| `alpha_lab/` | AI 自动生成/迭代策略（代码生成+沙箱执行+评估） |
| `portfolio/` | 持仓计算、已平仓交易、持仓对账、风险分析 |
| `review/` | 每日复盘服务 |
| `orderbook/` | 订单簿模拟器 + 做市商 Bot |
| `strategy/` | Python 策略/信号生成器，供 analysis_engine/回测/推荐引擎复用 |
| `services/` | 下沉的公共 service 层（见下）|
| `common/` | 跨引擎公共基础设施（`market.py` 市场命名单一真源、`sandbox_ast.py`）|
| `finetune/` | 本地/远程微调 pipeline |
| `remote/` | SSH 远程执行桥（GPU 训练机等） |
| `net/` | 代理/网络环境管理（境内外分流，见 GOTCHAS） |
| `notify/` | Mac 系统通知 |
| `scripts/` | 一次性/定期数据回补脚本 |
| `tests/` | pytest 测试 |

**从 route 下沉出来、被"页面 route"和"agent 工具"两条入口共同复用的 service**（改动务必两条入口都验证）：
- `screener_engine/service.py`（选股核心）
- `data_engine/stock_pools.py`（股票池/行业成分缓存）
- `services/backtest_cpp_client.py`（C++ 回测客户端代理+落库）
- `services/settings_service.py`（设置字段白名单）
- `portfolio/trade_recorder.py`（手动交易录入：卖出校验+平仓生成+风控警告）
- `data_engine/fetchers/realtime.py` 的 `compute_statistics`（涨跌统计）

## 3. 调用关系图

```
【页面驱动】
前端8个工作台 → api/routes/*.py → (可选下沉 services/*.py) → xxx_engine → data_engine.storage → market.db

【对话驱动】
MoneyBill(FloatingChat/ChatThread) → api/routes/agent.py（NDJSON流）
    → agents/orchestrator.MonitorOrchestrator
    → agents/tool_dispatch.ToolDispatcher
    → agents/tools/*.py（瘦适配器，run_in_executor 把同步引擎函数适配成 async）
    → 直接 import 同一批 xxx_engine/service（进程内直调，不走HTTP）
    → data_engine.storage / knowledge_engine.database（各自独立DB session）

【C++ 回测，独立进程】
backtest_cpp/（端口 8002，独立编译的 C++ 服务）
    ← services/backtest_cpp_client.py
    ← api/routes/backtest_cpp.py（页面入口）
    ← agents/tools/backtest_tools.py（对话入口，两个入口共用同一个 client）
```

已下线但引擎仍在的能力域（曾经的 route，现在只能通过 MoneyBill 对话触发）：
`advisor` `alpha_lab` `cockpit` `pipeline` `portfolio` `report` `review` `signal_generation` `tracking` `trading` `watchlist` —— 对应引擎目录都还在（`advisor_engine/`、`alpha_lab/`、`cockpit_engine/`、`portfolio/`、`report_engine/`、`review/`），入口改成了 `agents/tools/*.py`。想找这些功能，**不要去 `api/routes/` 找同名文件（已删），去 `agents/tools/` 找**。

## 4. 核心引擎详细路径

### 数据引擎 `backend/data_engine/`
- `engine.py` — `DataEngine` 门面类
- `fetchers/` — `a_share.py`(AkShare) `hk_stock.py`(yfinance) `us_stock.py`(yfinance) `realtime.py`(东财批量) `china_batch_quotes.py`(腾讯/新浪备源) `yf_batch.py`(港美股批量) `pytdx_fetcher.py`(通达信分钟线) `financial.py` `limit_up.py` `factory.py`(`FetcherFactory`，按symbol后缀路由市场)
- `processors/` — `cleaner.py` `normalizer.py` `validator.py`
- `storage/` — **数据库核心**：`database.py`(SQLAlchemy engine/session，`sqlite:///backend/data/market.db`，`DATABASE_URL`可覆盖) `models.py`(**主库全部30+张ORM表**) `repository.py` `history_repository.py`
- `deep_history/` — `a_share_job.py`(增量回补，复用daily_updater的EastMoneyCrawler)、`overseas_job.py`(yfinance整段拉取)
- `daily_pipeline_scheduler.py`、`daily_updater.py`、`financial_updater.py`、`health.py`(覆盖率/新鲜度检测)、`stock_pools.py`

### 分析引擎 `backend/analysis_engine/`
`engine.py`(`AnalysisEngine`)、`indicators/`(trend/momentum/volatility/volume/intraday)、`patterns/candlestick.py`、`signals/detector.py`、`signal_tracker.py`、`market_mood.py`

### 回测引擎（双版本，务必分清楚，详见 GOTCHAS）
- **Python 版**（遗留）`backend/backtest_engine/`：`engine.py` `backtest_executor.py` `strategies/`(ma_cross/macd/rsi/kdj/signal_strategy) `portfolio/`(order/position/portfolio) `metrics/`(calculator/report)
- **C++ 版**（正版）`/backtest_cpp/`：`src/engine.cpp` `src/portfolio.cpp` `src/risk_manager.cpp` `src/metrics.cpp` `src/server.cpp`(HTTP服务，端口8002) `src/strategies/`
  - backend代理层：`backend/services/backtest_cpp_client.py`
  - HTTP入口：`backend/api/routes/backtest_cpp.py`
  - 对话入口：`backend/agents/tools/backtest_tools.py`
  - 改完 C++ 需 `cd backtest_cpp/build && cmake --build .` 重建并**重启常驻的 backtest_server 进程**（不会自动更新）

### 交易引擎 `backend/trading_engine/`
`brokers/`(base/easytrader_broker/paper_broker)、`risk/`(manager.py/rules.py/adapter.py)、`monitor/`(alerts/logger/performance/tracker)、`mac_automation/`(explore_ths.py等，较冷门)、`position_sizing.py`、`config.py`

### report_engine `backend/report_engine/`
**13.2 已把整篇周报拆成五个可单独调用的章节**（`market` / `news` / `positions` / `strategy` / `picks`），
全量报告路径（`generator.py` / `planner.py` / `pdf_exporter.py`）已删除，只存在于 git 历史。

`data_collector.py`（五个 `collect_*` 分片 + 共享 `collect_common` TTL 缓存）
→ `stock_analyzer.py`/`scorer.py` → `web_searcher.py`
→ `prompt_builder.py`（`build_section_calls` 展开成 1~N 次 LLM 调用）
→ `section_writer.py`（成稿，走 `llm_client.stream_text`，认 cancel_event）

- `chapter_utils.py`：剥 Ch8 重复标题 / Ch8 覆盖度校验 / 提炼个股操作结论（纯文本）
- `picks_log.py`：买入推荐写 `DecisionLog`，下次「上期回顾」从那里回读
- ⚠️ `web_searcher.py` 与 `stock_analyzer.py` 是**共享件**：news_engine / advisor_engine /
  cockpit / `api/routes/news.py` 六处依赖，不要跟着报告一起删
- 「Ch1 纵览 & 操作计划」没有对应工具——由 MoneyBill 主 agent 看着五章摘要亲自撰写

### knowledge_engine `backend/knowledge_engine/`
独立库 `knowledge.db`（`config.py`的`KNOWLEDGE_DB_PATH`，默认`backend/data/knowledge.db`，与主库分离）
`models.py`(`KnowledgeDocument`/`KnowledgeChunk`/`AlphaIdea`)、`chunker.py`、`embedding.py`(bge-m3懒加载)、`vector_store.py`(vec0)、`retriever.py`、`ingest/`、`websearch/`、`browser/`、`idea_miner.py`、`reverse_api.py`、`scheduler.py`、`store.py`、`database.py`(`init_knowledge_db()`)

### 其余引擎一览
| 引擎 | 核心文件 |
|---|---|
| 决策驾驶舱 | `cockpit_engine/{aggregator,scorer,prompt_builder}.py` |
| 基本面选股器 | `screener_engine/service.py` |
| 涨停引擎 | `limit_up_engine/{candidate_pool,ingest,limit_rules,metrics,scoring,service}.py` |
| 选股推荐 | `recommend_engine/{candidates,engine,scoring,session}.py` |
| AI顾问 | `advisor_engine/{context_collector,prompt_builder,service}.py` |
| 持仓/对账 | `portfolio/{calculator,closed_trade_service,reconciliation_service,risk_analyzer,trade_recorder}.py` |
| 每日复盘 | `review/service.py` |
| 价格预测 | `prediction_engine/{engine,ensemble,features,remote_predict,validator}.py` |
| AI策略生成 | `alpha_lab/{code_generator,engine,evaluator,sandbox,session_manager,strategy_store}.py` |
| 新闻情绪 | `news_engine/{analyzer,fetcher,news_scheduler,prompts,realtime,sentiment}.py` |
| 模型微调 | `finetune/{pipeline,remote_train}.py` |
| 自动化常驻监控 | `automation/{limit_up_scanner,pending_order_manager,position_guardian,price_alert_monitor,scheduler,websocket_manager}.py` |
| 决策留痕 | `decision_log.py`、`business_events.py` |

## 5. MoneyBill / agents 多智能体编排层

```
agents/
├── orchestrator.py       # MonitorOrchestrator：Reason→Act→Observe 主循环骨架
├── turn_setup.py         # 每轮开局：系统提示/tool_groups/page_context/历史压缩/TurnMonitor重建
├── confirm_gate.py        # 下单等敏感操作的人工确认门
├── tool_dispatch.py       # ToolDispatcher：路由 tool_call 到 meta工具/subagent/普通工具
├── tool_groups.py         # 工具分组（12个CORE常驻 + 10组按需 load_toolgroup），控制token地板
├── registry.py            # @tool 装饰器 + 全局 REGISTRY
├── tool_envelope.py        # 统一 ToolEnvelope/ErrorCode 返回结构
├── context.py              # AgentSession, STORE（会话状态，含落盘/复活）
├── executor.py              # run_tool / validate_tool_args（真正调用工具函数）
├── policy_checks.py          # 安全策略校验
├── events.py                 # EV 事件类型 + emit（NDJSON流协议）
├── history.py                 # 会话历史持久化 + 自动压缩
├── trace.py / turn_monitor.py  # 调用链追踪 + token/verdict 记账
├── usage.py, widgets.py, skills_loader.py
├── skills/                     # deep_stock.md, monitor.md（MoneyBill的人格/工具分组提示词）
├── tools/                      # ~20个工具文件，每个瘦封装若干 engine 函数
│   ├── data_tools.py analysis_tools.py backtest_tools.py signal_tools.py
│   ├── screener_tools.py pool_tools.py watchlist_tools.py alert_tools.py
│   ├── portfolio_tools.py trading_tools.py decision_tools.py review_tools.py
│   ├── news_tools.py knowledge_tools.py monitor_tools.py settings_tools.py
│   ├── recommend_tools.py limit_up_tools.py market_tools.py intraday_tools.py
│   └── nav_tools.py（agent驱动前端导航）
└── subagents/                  # 复杂任务子代理（都是「贵一点的工具」，会流式吐正文）
    ├── deep_stock.py（个股深度研判，委托 advisor_engine）
    ├── news.py（新闻深度解读，委托 news_engine）
    ├── alpha_lab.py（策略研发，委托 alpha_lab）
    └── report_sections.py（五个投研报告章节，委托 report_engine.section_writer）
```

调用方向：`agents/tools/*.py` 直接 `import` 对应 `xxx_engine`/`service`，**不经过 HTTP**，用 `run_in_executor` 把同步引擎函数适配成 async。工具本身是"瘦适配器"，不应包含业务逻辑（曾经 `recommend_tools.py` 有 513 行业务逻辑写在工具层，2026-07 已下沉回 `recommend_engine/`，只剩 57 行——**新工具开发要避免重蹈覆辙**）。

HTTP 入口：`backend/api/routes/agent.py`（NDJSON流，内部桥接 `MonitorOrchestrator` 的同步生成器）。

前端对应：`frontend/src/components/moneybill/` + `frontend/src/services/agentService.ts` + `frontend/src/store/agentChatStore.ts` + `frontend/src/hooks/useAgentChat.ts`。

## 6. 前端架构

### 8 个工作台路由（唯一真源见下）

路由表来源：`frontend/src/components/shell/MainStage.tsx` 的 `PAGES` 常量 + `frontend/src/components/shell/ToolsDrawer.tsx` 的 `GROUPS` 白名单 —— **两处必须同步维护，新增/删除工作台要同时改这两个文件**（还有 `agents/nav_tools.py` 的 `ALLOWED_PATHS`/`RETIRED_PATHS`，agent 驱动导航也要同步）。

| # | 路由 path | 文件 | 职责 |
|---|---|---|---|
| 0 | `/` 或 `/app`（默认） | `components/moneybill/ChatThread.tsx` | MoneyBill 聊天主界面，唯一主入口，常驻不卸载 |
| 1 | `/app/market` | `pages/Market.tsx` | K线分析工作台 |
| 2 | `/app/backtest` | `pages/Backtest.tsx` | 策略回测工作台（内嵌 CppBacktestPanel） |
| 3 | `/app/orderbook` | `pages/OrderBook.tsx` | 订单簿模拟器 |
| 4 | `/app/prediction` | `pages/Prediction.tsx` | 股价预测（训练/预测/验证3个Tab） |
| 5 | `/app/data-monitor` | `pages/DataMonitor.tsx` | 数据监控看板 |
| 6 | `/trading` | `pages/Automation.tsx` | 自动化交易中心（5个Tab） |
| 7 | `/fine-tune` | `pages/FineTune.tsx` | 模型微调实验室 |

匹配不到 `PAGES` 白名单的路径会静默回退到聊天层（`MainStage.tsx` 的 `showChat = isChat || !activeTool`）——这是不报错但"看起来什么都没变"的死链兜底行为，排查导航问题时留意。登录门在 `App.tsx`（非路由分支），UI 在 `pages/LoginPage.tsx`。

**已退役页面是硬删除，没有归档目录**，只存在于 git 历史（关键提交 `0ccbb6b` 的父提交）：`Advisor.tsx` `AlphaLab.tsx` `Cockpit.tsx` `Dashboard.tsx` `DataPipeline.tsx` `Home.tsx` `Knowledge.tsx` `MoneyBill.tsx`(旧版) `News.tsx` `Portfolio.tsx` `Realtime.tsx` `Reports.tsx` `Review.tsx` `Screener.tsx` `SignalTracking.tsx` `Signals.tsx` `Trading.tsx` `Watchlist.tsx`，及对应的 `services/*.ts`。**这些功能现在只能通过 MoneyBill 对话触发**，去 `backend/agents/tools/*.py` 找。

### 目录结构

```
frontend/src/
├── App.tsx / main.tsx
├── pages/                # 8个工作台页面文件 + LoginPage.tsx
├── components/
│   ├── shell/            # AppShell/MainStage/Sidebar/ToolsDrawer/MonitorPanel/TokenMonitor
│   ├── moneybill/        # MoneyBill聊天核心组件（见下）
│   ├── charts/           # CandlestickChart.tsx, IndicatorPanel.tsx
│   ├── backtest/         # 回测报告全部组件（14个，含C++回测对接）
│   ├── trading-center/   # Automation页的5个Tab组件
│   ├── datamonitor/      # DataMonitor页的各摄入/监控子面板（9个）
│   ├── automation/       # PendingOrderCard
│   └── common/           # 通用UI（Card/Button/Toast/Skeleton/MarkdownView/StockSymbolInput）
├── hooks/                # useAgentChat/useClickOutside/useImeGuard/useStockNames
├── services/             # 每业务域一个REST封装 + api.ts(axios) + websocketService.ts
├── store/ + stores/      # zustand：agentChatStore/monitorStore/usageStore/pageContextStore/automationStore
├── utils/                # authFetch/authToken/agentNavigate/pageContext/indicators/marketDetect/signalDetector
└── types/
```

### MoneyBill 聊天组件（`frontend/src/components/moneybill/`）
- `ChatThread.tsx` — 全屏聊天主体
- `FloatingChat.tsx` — 离开聊天页时右下角浮窗，带"当前页面上下文"采集（`utils/pageContext.ts`）
- `WidgetRenderer.tsx` — 把 agent 返回的结构化 widget（cockpit_score/position_table/prediction/price_quote/price_sparkline/recommendation_board/limit_up_pool/knowledge_sources/metric_cards）分发渲染成卡片
- `ConfirmDialog.tsx` — 高危操作二次确认弹窗
- `SessionIdBadge.tsx`、`statusCopy.ts`

### 前后端通信方式
- **REST**：`services/api.ts`(axios，自动带JWT，解包`response.data`) + `utils/authFetch.ts`(原生fetch，供流式接口用，axios不便读ReadableStream)；各业务域独立 `services/*.ts` 文件 1:1 对应 `api/routes/*.py`
- **对话流式**：`services/agentService.ts` 基于 `fetch()` + `response.body.getReader()` 手动解析SSE（不是EventSource/WebSocket），事件类型见`AgentEventType`（chunk/tool_call/tool_result/widget/navigate/confirm_required/usage/monitor/done/error），由 `hooks/useAgentChat.ts` 消费
- **WebSocket**（纯推送，非对话）：`services/websocketService.ts` 单例，自动重连+心跳，URL query string带JWT；用途：`AppShell.tsx`监听`price_alert`、`stores/automationStore.ts`订阅自动化事件；对应后端 `api/routes/ws.py` + `automation/websocket_manager.py`

## 7. 数据存储

- **主库**：SQLite，`backend/data/market.db`（`DATABASE_URL`可覆盖，支持切Postgres），配置在 `data_engine/storage/database.py`，ORM在 `data_engine/storage/models.py`（30+表：行情/信号/回测/订单/成交/新闻/持仓对账/AlphaLab/自选股/预警/估值/决策留痕等）
- **知识库独立库**：`backend/data/knowledge.db`（`KNOWLEDGE_DB_PATH`可覆盖，含vec0向量表），models在 `knowledge_engine/models.py`，初始化 `knowledge_engine/database.py::init_knowledge_db()`
- 两库刻意分离，`api/main.py` 启动时分别调用两个 init 函数

## 8. "我想改 XX，该去哪找" 速查索引

| 想改什么 | 去哪找 |
|---|---|
| K线图/指标面板 | `frontend/src/components/charts/CandlestickChart.tsx` / `IndicatorPanel.tsx`（类型 `src/types/chart.ts`） |
| 某个技术指标算法 | `backend/analysis_engine/indicators/{trend,oscillator,volatility,volume}.py` |
| 回测参数/新增策略 | 优先改 **C++版** `/backtest_cpp/src/strategies/`（正版，8个编译内置策略）；只有 alpha_lab 生成的任意代码才落在 Python版 `backend/backtest_engine/strategies/` |
| MoneyBill某个工具能力（如"改选股推荐逻辑"）| `agents/tools/recommend_tools.py`（瘦适配器）→ 实际逻辑在 `recommend_engine/` |
| 风控硬规则 | `trading_engine/risk/rules.py` + `trading_engine/config.py` 的 `RISK_CONFIG` |
| 新增/删除一个工作台页面 | 同步改 `MainStage.tsx`的`PAGES` + `ToolsDrawer.tsx`的`GROUPS` + `agents/nav_tools.py`的`ALLOWED_PATHS/RETIRED_PATHS` |
| 数据源/新增备用数据源 | `data_engine/fetchers/factory.py`（`FetcherFactory`）+ 对应市场的fetcher文件 |
| "已退役的旧页面"逻辑（信号/自选/复盘/报告/顾问/持仓等）| 不要找 `pages/`（已硬删除），去 `backend/agents/tools/*.py` 找同名工具文件 |
| MoneyBill人格/沟通风格/工具分组铁律 | `backend/agents/skills/monitor.md` |
| 某个API endpoint对应哪个功能域 | `docs/FEATURES.md` 第1节的功能全景表 |
| 市场命名/canonical写法 | `backend/common/market.py`，改前务必看 `docs/GOTCHAS.md` 里的市场命名坑 |

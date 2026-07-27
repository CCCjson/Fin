# 项目功能设计文档

> 最后更新：2026-07-07。列出当前**真实存在**的功能域、入口方式（页面 or 对话）、核心文件路径。架构层面的目录/调用关系见 `docs/ARCHITECTURE.md`。

## 1. 功能全景表

### 1.1 存活的 HTTP 路由域（20个文件，`backend/api/routes/`，除 `auth.py` 外全部 JWT 鉴权）

| 功能域 | 路由文件 | 主要 Endpoint |
|---|---|---|
| 鉴权 | `auth.py` | `POST /auth/login`、`GET /auth/verify` |
| MoneyBill 对话 | `agent.py` | `POST /agent/chat`（NDJSON流式）、`GET /agent/usage`、`GET /agent/session/{session_id}` |
| 行情数据 | `data.py` | `POST /data/daily`、`GET /data/stocks`、`POST /data/update`、`GET /data/info/{symbol}`、`POST /data/update-daily/stream`、`POST /data/financial/backfill/stream`、`GET /data/stocks/search`、`GET/PUT /data/settings*` |
| 实时行情 | `realtime.py` | `GET /realtime/quotes`、`GET /realtime/quotes/stream`、`GET /realtime/indices`、`GET /realtime/ticker` |
| 深度历史回补 | `deep_history.py` | `POST/GET /deep_history/a-share/{start,status,stop}`、`POST/GET /deep_history/overseas/{start,status,stop}` |
| 数据健康监控 | `data_monitor.py` | `GET /data-monitor/overview`、`GET /data-monitor/events`、`POST /data-monitor/scheduler/toggle`、`GET/POST /data-monitor/limit-up/*` |
| 技术分析 | `analysis.py` | `POST /analysis/indicators`、`POST /analysis/signals`、`POST /analysis/patterns`、`GET /analysis/indicators/list` |
| 历史查询 | `history.py` | `GET /history/signals(/statistics)`、`POST/GET/DELETE /history/backtests*`、`GET/POST /history/backtests/compare*`、`GET /history/orders(/statistics)`、`GET /history/trades(/statistics)` |
| C++回测代理 | `backtest_cpp.py` | `GET /backtest_cpp/strategies`、`POST /backtest_cpp/run`、`POST /backtest_cpp/run_and_save`、`POST /backtest_cpp/batch`、`GET /backtest_cpp/batch/{id}`、`GET /backtest_cpp/batches` |
| Walk-Forward优化 | `walk_forward.py` | `POST /walk_forward/run` |
| 订单簿模拟 | `orderbook.py` | `POST /orderbook/sessions`、depth/orders/fills/stats/seed、`WS /orderbook/sessions/{id}/ws`、做市商 start/stop/status/config |
| 价格预测 | `prediction.py` | `POST /prediction/train(/stop)`、`GET /prediction/train/status`、`GET /prediction/models`、`POST /prediction/predict`、`GET /prediction/history`、`POST /prediction/backfill`、`GET /prediction/performance` |
| 基本面选股 | `screener.py` | `GET /screener/fields`、`POST /screener/run`、`POST /screener/refresh-valuation` |
| 股票池/行业成分 | `stock_pools.py` | `GET /stock_pools/pools(/{id})`、`GET /stock_pools/industries(/{name})` |
| 新闻与情绪 | `news.py` | `POST /news/fetch`、`GET /news/articles`、`POST /news/analyze`、`POST /news/morning-briefing`、`POST /news/report`、`GET/POST /news/job/*` |
| 知识库（RAG） | `knowledge.py` | `GET /knowledge/stats(/documents)`、`POST /knowledge/search`、`POST /knowledge/ingest/{papers,arxiv,web}`、`POST/GET /knowledge/ingest/cninfo/*`、`POST/GET /knowledge/ingest/research_report/*`、`GET/POST /knowledge/ideas*`、`POST /knowledge/scrape/stream` |
| 自动化交易/风控 | `automation.py` | 待审批订单CRUD、批量确认/拒绝、`configs`CRUD、`scheduler/{start,stop,status,trigger}`、`logs`、`statistics`、`broker-status`、`broker-positions`、`execution-history` |
| Paper Trading监控 | `monitor.py` | `POST /monitor/init`、`GET /monitor/trades`、`GET /monitor/orders/status`、`GET /monitor/performance`、`GET /monitor/alerts`、`POST /monitor/reset` |
| 模型微调 | `fine_tune.py` | `POST /fine_tune/start`（流式）、`GET /fine_tune/reconnect`、`POST /fine_tune/stop`、`GET /fine_tune/status`、`GET /fine_tune/data-stats` |
| WebSocket推送 | `ws.py` | `WS /ws/automation`（业务事件总线推送） |

### 1.2 已下线但引擎仍在的能力域（入口是 MoneyBill 工具，非 HTTP）

`advisor` `alpha_lab` `cockpit` `pipeline` `portfolio` `report` `review` `signal_generation` `tracking` `trading` `watchlist` —— 对应引擎代码仍在 `advisor_engine/`、`alpha_lab/`、`cockpit_engine/`、`portfolio/`、`report_engine/`、`review/`。**改这些功能，去 `backend/agents/tools/*.py` 找工具入口，而不是找 route。**

## 2. MoneyBill 工具矩阵

工具分组配置：`backend/agents/tool_groups.py` —— 12个 CORE 常驻工具 + 10组按需 `load_toolgroup`（模型自助扩容，session内粘滞）。页面路由会预载对应 PAGE_GROUPS。

MoneyBill 人格/沟通风格/工作方式铁律：`backend/agents/skills/monitor.md`（front-matter `enabled_tools: [signals, screener, news]` 是默认预加载组）。核心纪律：
- **"四分流"铁律**：推荐 / 查询 / 筛选 / 涨停候选预测，四类请求要分流到不同工具，不能混用
- 选股推荐纪律、涨停候选池预测纪律、subagent 收尾规则、安全红线均写在 monitor.md 里

深度任务子代理（`agents/subagents/`）：`deep_stock.py`（个股深度研判，委托 `advisor_engine`，输出结论/关键证据/操作计划/失效条件4段≤1200字，必须给精确入场/止损/止盈价，prompt见`agents/skills/deep_stock.md`）、`news.py`（新闻深度解读，委托 `news_engine`）、`alpha_lab.py`（策略研发，委托 `alpha_lab`）、`report_sections.py`（**五个投研报告章节**：`report_market` / `report_news` / `report_positions` / `report_strategy` / `report_picks`，委托 `report_engine.section_writer`）。

20个工具文件（`agents/tools/`）：`data_tools` `analysis_tools` `backtest_tools` `signal_tools` `screener_tools` `pool_tools` `watchlist_tools` `alert_tools` `portfolio_tools` `trading_tools` `decision_tools` `review_tools` `news_tools` `knowledge_tools` `monitor_tools` `settings_tools` `recommend_tools` `limit_up_tools` `market_tools` `intraday_tools` `nav_tools`。**新增工具必须同步归组到 `tool_groups.py`，否则首次会话就会抛 RuntimeError（`_validate` 断言 CORE∪组=REGISTRY 全集）。**

## 3. 补充能力域清单（README 未详述）

| 功能域 | 核心文件 | 简述 | 触发方式 |
|---|---|---|---|
| 决策驾驶舱 | `cockpit_engine/{aggregator,scorer,prompt_builder}.py` | 技术/基本面/情感/ML/持仓五维体检聚合打分 | 对话（`get_cockpit_score`工具），聊天内`cockpit_score` widget渲染 |
| 涨停引擎 | `limit_up_engine/{candidate_pool,ingest,limit_rules,metrics,scoring,service}.py` | 涨停候选池/打分/盘中扫描全链路 | 对话 + `/data-monitor/limit-up/*` |
| 选股推荐 | `recommend_engine/{candidates,engine,scoring,session}.py` | 持仓分流SELL/HOLD、非持仓三重闸门推BUY | 对话，聊天内`recommendation_board` widget |
| AI顾问 | `advisor_engine/{context_collector,prompt_builder,service}.py` | 个性化投研建议生成，单次流式completion无循环 | 对话（`deep_stock` subagent委托） |
| 持仓/对账 | `portfolio/{calculator,closed_trade_service,reconciliation_service,risk_analyzer,trade_recorder}.py` | 系统持仓vs券商真实持仓对账、风险体检 | 页面（Automation持仓Tab）+ 对话，聊天内`position_table` widget |
| 每日复盘 | `review/service.py` | AI交易打分/复盘 | 对话 |
| 投研报告 | `report_engine/{data_collector,section_writer,prompt_builder,chapter_utils,picks_log,scorer,stock_analyzer,web_searcher}.py` | **五个可单独调用的章节**（大盘板块/新闻舆情/持仓诊断/回顾策略/买入推荐），各自分片采集 + 独立成稿、认 cancel；「纵览 & 操作计划」由 MoneyBill 主 agent 撰写。全量报告与 PDF 导出已于 13.2 退役 | 对话（五个 `report_*` subagent） |
| 知识库/RAG | `knowledge_engine/{chunker,config,database,embedding,idea_miner,models,retriever,reverse_api,scheduler,store,vector_store}.py` | 本地bge-m3向量化 + 逆向爬虫摄入 + alpha idea挖掘 | 页面（DataMonitor知识库面板）+ 对话 |
| 价格预测 | `prediction_engine/{engine,ensemble,features,remote_predict,validator}.py` | LSTM/XGBoost/Ensemble多模型集成 + 远程GPU训练 | 页面（Prediction工作台） |
| AI策略生成 | `alpha_lab/{code_generator,engine,evaluator,sandbox,session_manager,strategy_store}.py` | LLM生成策略代码 + AST沙盒执行 + 评分迭代 | 对话（`run_alpha_lab` subagent委托） |
| 新闻情绪 | `news_engine/{analyzer,fetcher,news_scheduler,prompts,realtime,sentiment}.py` | 定时抓取+BERT/LLM情绪分析 | 页面（DataMonitor新闻任务面板）+ 对话 |
| 模型微调 | `finetune/{pipeline,remote_train}.py` | 本地/远程GPU微调流水线 | 页面（FineTune工作台） |
| 自动化常驻监控 | `automation/{limit_up_scanner,pending_order_manager,position_guardian,price_alert_monitor,scheduler,websocket_manager}.py` | 涨停扫描/待审批订单/止损守护/价格预警，开机自启 | 页面（Automation工作台）+ WebSocket推送 |
| 交易执行 | `trading_engine/brokers/{qmt_broker,paper_broker,easytrader_broker,openctp_broker,base}.py` | 多broker适配（模拟盘/QMT/东财/OpenCTP实盘） | 页面（Automation）+ 对话下单确认流 |
| crypto 半自动策略 | `crypto_strategy/{engine,pending,guardrails,backtest_gate,performance}.py`、`common/trade_source.py` | 人话→DSL 编译 + 逐 tick 评估 + 排待确认单（**引擎永不自动成交**）+ **策略战绩体检**（触发/拦截分布/归因盈亏）。成交按 `CryptoTrade.source_kind/source_ref` 归因到策略 | 对话（`compile_crypto_strategy` / `get_strategy_performance` / `list_strategy_standings`） |
| 决策留痕 | `decision_log.py`、`common/decision_kind.py`、`common/decision_source.py`、`business_events.py` | AI建议(advisor/cockpit/moneybill)持久化归因 + 业务事件总线→WS推送。表里三类行靠 `entry_kind` 区分（`advice` 才进胜率，回执/操作不进）；来源 8 个由 `decision_source` 建表 | 系统内部，各功能调用时自动记录；查询唯一出口 = `get_decision_history` 工具（`entry_kind`/`verbose`/`exec_state`） |

## 4. 市场与数据源覆盖表

单一命名真源：`backend/common/market.py`（`a_share`/`hk_stock`/`us_stock`，`normalize_market()`/`infer_market_from_symbol()`）。市场命名有历史坑，见 `docs/GOTCHAS.md`。

| 市场 | 日线获取器 | 数据源 | 已知限制 |
|---|---|---|---|
| A股 | `fetchers/a_share.py` | AkShare（`stock_zh_a_hist`），走 `net.domestic_akshare` | symbol需转6位代码 |
| 港股 | `fetchers/hk_stock.py` | yfinance | 项目内部存5位代码（`00700.HK`），喂给yfinance前需转4位（`0700.HK`），否则报"possibly delisted" |
| 美股 | `fetchers/us_stock.py` | yfinance | 代码原样传入 |
| A股分钟线（盘中扫描） | `fetchers/pytdx_fetcher.py` | pytdx通达信 | 支持1/5/15/30/60分钟K线 |
| A股实时行情（全市场批量） | `fetchers/realtime.py` | 东方财富（主源） | 并发分页拉5000+只，多代理IP并行 |
| A股实时行情备源 | `fetchers/china_batch_quotes.py` | 腾讯`qt.gtimg.cn`（主备）/新浪 | 东财失败时兜底 |
| 港股/美股批量实时行情 | `fetchers/yf_batch.py` | yfinance批量下载 | 替代逐只`ticker.info`降低请求数 |
| 财务数据 | `fetchers/financial.py` + `financial_updater.py` | AkShare等 | 单独补齐流程 |
| A股深历史回补 | `deep_history/a_share_job.py` | 复用daily_updater的EastMoneyCrawler+ProxyPool | 增量回补（从旧到新） |
| 港股/美股深历史回补 | `deep_history/overseas_job.py` | yfinance | 整段拉取（从0到有） |
| 涨停行情 | `fetchers/limit_up.py` | AkShare | 涨停池专用 |

代理/网络层：`backend/net/`（`proxy_pool.py`/`proxy_manager.py`/`domestic.py`/`overseas.py`/`clash.py`/`session.py`/`env.py`）—— 国内（快代理）与海外代理池分离。详细坑见 `docs/GOTCHAS.md`。

## 5. 风控规则清单

**设计选择：风控硬规则写死在代码里，非配置文件驱动，模型/策略不可覆盖。**

代码位置：`backend/trading_engine/risk/rules.py`（规则类定义）+ `backend/trading_engine/config.py` 的 `RISK_CONFIG` 字典。

五条硬规则：
1. 单股最大仓位 ≤ 总资金 20%
2. 单日最大亏损 ≤ 总资金 3%，触发自动停止交易
3. 总持仓不超过 80%，保留 20% 现金
4. 每笔交易必须设置止损（默认 -5%），止盈默认 +15%
5. 连续亏损 3 次后暂停交易 1 天

模型/agent 对这些风控键**只读不可写**：`agents/tools/settings_tools.py` 的 `_RISK_READONLY_KEYS` 黑名单强制执行。

实盘对接配置（`OPENCTP_CONFIG`）：凭证走 `.env`（`OPENCTP_USER_ID`/`PASSWORD`等）。

## 6. 逆向爬虫站点配置（`backend/configs/scrapers/`）

每站一对 `.json`(接口结构)+`.md`(可读说明)：`xueqiu.com`（雪球）、`guba.eastmoney.com`/`mguba.eastmoney.com`/`gbapi.eastmoney.com`（东财股吧系列）、`capitaliq.com`/`capitaliq.spglobal.com`（标普Capital IQ）。供 `knowledge_engine/reverse_api.py` 和 MoneyBill 的 scrape 工具复用，避免重复侦查站点结构。

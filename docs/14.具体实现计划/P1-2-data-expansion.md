---
id: P1-2
title: 数据扩展（港美股财务 / 宏观 / 舆情）
size: 中
depends: 无
paths_verified: 2026-07-07 ⚠️ 待核实
---

# P1-2 数据扩展

> ⚠️ **路径是 2026-07-07 的，早于 13.x 大重构（9 域）。开工第一步 grep 确认；对不上先报告 Jason。**
> ⚠️ **所有出网代码必须落 `backend/acquisition/` 下**——`data_engine/fetchers/*` 的旧落点需先确认 13.x 重构后出网层归属，否则 `tests/net/test_egress_single_entry.py` 会咬。

> **三个缺口**：财务/公告/研报**港美股完全缺失**；**宏观数据为零**（所谓「宏观分析」实为 LLM 对新闻标题的解读）；**社交媒体舆情为零**（所谓「情绪」是新闻 BERT 打分，不是舆情）。

**建议拆三次 PR，别一把梭。三块互不依赖，舆情性价比最高（爬虫配置已侦查好）。**

## 🔍 探查前置（2026-07-17 Jason 拍板：OpenBB 可以看一下）

> **规矩**：探完更新本卡再写代码；结论写回对应段落，本表行标 ✅ + 日期。**与现有方案冲突 → 先报告 Jason。**
> **只卡 B（宏观）和 C（港美股财务）**。A（舆情）走的是自家逆向爬虫栈，与 OpenBB 无关，**不等探查可直接开工**。

| 档 | 对象 | 带着这个问题去 | 卡住 | 状态 |
|---|---|---|---|---|
| **T2** | **OpenBB**（开源投研平台） | ① **真正的价值可能不在抽象层，在源清单**：港美股财务和宏观**它到底接了哪些 provider**？我们现在打算「yfinance + akshare 一把梭」，它的清单能不能让我们少踩一轮坑（哪个源缺字段、哪个限频、哪个要 key）？② 它的 provider 抽象层长什么样，值不值得给 `FetcherFactory` 抄？③ ⚠️ **「OpenBB 的边际价值」本身就是要探的东西之一**——`docs/14` §10 已证明 daily_stock_analysis 的**能力矩阵 + 熔断**更贴我们需求。**如果探完发现它只是「又一个 fetcher 抽象」，就把这行连同 §7 里的条目一起删掉，别留着占位。** | B、C | ❌ 未探 |

**注意别被它带偏**：OpenBB 是**通用投研平台**（多用户、全球市场、插件生态），我们是**Jason 一个人的 A/HK/US 实盘工具**。它的抽象层大概率是为「支持任意 provider」设计的，而我们的 `FetcherFactory` 只要支持**我们真在用的那几个**。**抄源清单是实的，抄抽象层要先问一句「我们有那么多 provider 要支持吗」。**

---

## A. 社交舆情（性价比最高，先做）

| 类型 | 文件 | 要做什么 |
|---|---|---|
| **现成资产** | `backend/configs/scrapers/xueqiu.com.json`、`guba.eastmoney.com.json`、`gbapi.eastmoney.com.json`（含 m 站） | **站点接口结构已侦查完，直接用** |
| 抄的模式 | `backend/knowledge_engine/ingest/reverse_api_source.py`（`ingest_xueqiu_events`） | 逆向 API → IngestPipeline 落知识库的接线方式 |
| 新增 | `backend/knowledge_engine/ingest/guba_sentiment_source.py`（+ 对应 `_job.py`，抄 `cninfo_job.py` 的后台任务结构） | 股吧/雪球讨论抓取 + 情感打分入库 |
| 复用 | `backend/news_engine/sentiment.py` | BERT 情感打分直接复用 |
| 蓝本（外部） | `TradingAgents-main/tradingagents/agents/analysts/sentiment_analyst.py:121-183` | 情绪分析方法论 prompt（多空比阅读 / 跨源背离信号 / 按 engagement 加权 / 区分观点 vs 事件），翻译成雪球/股吧版 |
| 修改 | `backend/knowledge_engine/scheduler.py` | 挂定时（**现在只摄入 arxiv**）；进度面板按铁律接 `frontend/src/components/datamonitor/` |

> ⚠️ 与 **B-3（逆向爬虫合规边界）** 相关。自用无碍，商业化前需 Jason 拍板。

---

## B. 宏观数据

akshare 接口**全部现成，只是没人接**。

| 类型 | 文件 | 要做什么 |
|---|---|---|
| 新增 | `backend/data_engine/fetchers/macro.py` | akshare `macro_china_cpi` / `macro_china_pmi` / `macro_china_lpr` / `macro_usa_cpi` 等 |
| 修改 | `backend/data_engine/storage/models.py` | 加 `macro_indicators` 表 |
| 修改 | `backend/data_engine/daily_pipeline_scheduler.py` | 挂每日链 |
| 修改 | `backend/agents/tools/market_tools.py` + `tool_groups.py` | 暴露 `get_macro_snapshot` 工具（归 core 或 market_sentiment 组） |

---

## C. 港美股财务

| 类型 | 文件 | 要做什么 |
|---|---|---|
| 抄的模式 | `backend/data_engine/fetchers/financial.py` + `backend/data_engine/financial_updater.py` | A股财务的 fetcher→updater→`financial_data` 表模式，**表结构大部分字段可复用** |
| 新增 | `backend/data_engine/fetchers/financial_overseas.py` | yfinance 的 `income_stmt` / `balance_sheet` / `cashflow` |
| 修改 | `backend/data_engine/storage/models.py`（`FinancialData`，:867 附近） | 确认 market 维度字段。**⚠️ 留意「日线覆盖率 300% bug」——统计分母必须带 market 过滤** |
| 修改 | `backend/data_engine/fetchers/factory.py` | `FetcherFactory` 按市场路由财务 fetcher |

---

## 顺手补的定时化（三块任一做完都可以顺手）

`cninfo_job.py` / `research_report_job.py` / 财务补齐目前**均为手动触发**，可一并挂进 `daily_pipeline_scheduler.py` 或 `knowledge_engine/scheduler.py`（前者已有五步串行链，直接加步骤）。

## 本卡不做（记录以免遗漏）

- **SEC/港交所**：`sec_search` 是「检索即弃」设计（只检索标题+链接，不落库），没走 IngestPipeline → 美股监管文件无法沉淀复用。修法：结果经 `read_url` → IngestPipeline 落知识库（source_type 加 `filing`）。港交所披露易有公开 API（`www1.hkexnews.hk` 的 titlesearch 接口），可用 discover_api 侦查后接入。**另开卡。**

---
id: P2-2
title: 验证层（跨源抽检 / health 扩域 / 数据血缘）
size: 中
depends: 无 —— **与 P0-2 状态机合并施工**
paths_verified: 2026-07-07 ⚠️ 待核实
---

# P2-2 验证层

> ⚠️ **路径是 2026-07-07 的，早于 13.x 大重构。开工第一步 grep 确认。**
> 🔗 **ToolEnvelope 血缘这块与 P0-2 是同一件事，两卡合并施工，别做两遍。**

> **根因**：架构是「信任式」的——工具返回什么 LLM 就信什么，用户就看什么。**验证从来没被当成一个独立层来设计**，它散落在 `validator.py`（入库前）、`health.py`（仅A股日线）、`retriever.py`（RAG）三处，**互不相通**。
>
> **且无跨源交叉验证**：新浪→akshare 是**同源兜底回落，不是校验**；行情、财务全部单源直取，**源错即错**。

## 三块

### A. 跨源抽检

| 类型 | 文件 | 要做什么 |
|---|---|---|
| 现成资产 | `backend/data_engine/fetchers/realtime.py`（东财主源）vs `china_batch_quotes.py`（腾讯/新浪备源） | **备源本来就有，只是从没拿来比对** |
| 新增 | `backend/data_engine/cross_check.py` | 每日收盘后随机抽 N 只股票，比对东财 vs 腾讯收盘价、新浪 vs akshare 财务关键字段；差异超阈值发 `business_events` 告警。挂 `daily_pipeline_scheduler.py` |

### B. health.py 扩域

| 类型 | 文件 | 要做什么 |
|---|---|---|
| 修改 | `backend/data_engine/health.py` | 覆盖率/新鲜度从「A股日线」扩到财务/新闻/知识库/港美股。**⚠️ 先看「日线覆盖率跨市场 300% bug」——分母必须带 market 过滤** |
| 修改 | `backend/agents/tools/monitor_tools.py`（`get_system_pulse`）+ `frontend/src/components/datamonitor/` | 扩域后的健康度输出进系统脉搏工具和数据监控页（**面板体系是现成的**，铁律 3） |

### C. ToolEnvelope 数据血缘（= P0-2 的原料层）

| 类型 | 文件 | 要做什么 |
|---|---|---|
| 修改 | `backend/agents/tool_envelope.py` + `backend/agents/executor.py` | 统一附 `meta: {source, as_of, freshness, status}`。**ToolEnvelope 结构已存在，是加字段不是造轮子** |

## 外部蓝本

| 文件 | 抄什么 |
|---|---|
| `daily_stock_analysis/src/data_provider/base.py` + `services/run_diagnostics.py` | **`fallback_to` 埋点**：每次 fetch 记 `record_provider_run(provider, success, latency_ms, error_type, fallback_to=下一个源)`。**光这一个字段就能还原「东财挂→切腾讯→也挂→切新浪成功」的完整链路**。本项目现在靠读日志猜 |
| `daily_stock_analysis/src/data_provider/base.py:615` | **显式能力矩阵**：一张表声明「谁支持哪些市场」。本项目的「谁能干什么」是**隐式散落**在 factory + 各 router 里 |
| `TradingAgents-main/tradingagents/dataflows/interface.py:168-199` | **vendor 显式链路由，绝不静默 fallback**——路由表结构参考（与本项目代理铁律同精神） |

## 参考

**Great Expectations**（开源数据质量框架）：「expectation suite + 每日校验报告」模式可简化后用于跨源抽检。⚠️ 未做过源码级核实。

---

## 🪙 crypto 实况补记（2026-07-27 重设计｜**分类 ④：B/C 已完工但 crypto 未验；A 段要加 crypto 分支**）

### B 段（health 扩域）—— crypto 部分已有

`common/market_freshness.py` 已含 crypto 覆盖（07-27 grep 命中 5 处），且新鲜度按市场分开算参考日、木桶取短板（见 [[daily-chain-silent-day-loss]]）。

⚠️ **但 crypto 的「新鲜度」定义与股票根本不同**：股票按「上一个交易日」判，**crypto 7×24 没有交易日概念** —— 数据落后 1 天在 A 股周一早上是正常的，在 crypto 上就是**故障**。开工扩域时确认这条已经分开处理，别套用 `STALE_AFTER_WEEKDAYS`。

### A 段（跨源抽检）—— crypto 要加分支，但**优先级更高**

本卡 A 段的现成资产（东财 vs 腾讯 vs 新浪）**全是 A 股源**，crypto 没有对应物。

**但 crypto 反而更需要跨源校验**：币安是**唯一源**，没有备源可比。一旦币安返回异常数据（限流后的残缺响应、维护期的陈旧价），**当前没有任何机制能发现**。

可行的 crypto 抽检方向（**未验证，待评估**）：拿 CoinGecko 的价格做交叉比对。⚠️ 但 CoinGecko 是**滚动窗口封禁**（见 [[crypto-decision-sources-v2]]），抽检频率必须一天一次，**不能像股票侧那样随便调**。

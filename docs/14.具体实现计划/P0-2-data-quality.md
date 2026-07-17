---
id: P0-2
title: 数据质量状态机 + 置信度硬传导
size: 中
depends: 无（与 P2-2 ToolEnvelope 血缘合并施工）
blocked_by: B-1（落点未定，见 00-PLAN §5）
paths_verified: 2026-07-17
---

# P0-2 数据质量状态机 + 置信度硬传导

> **一句话**：MoneyBill 跟你说「腾讯买入，置信度：高」。你不知道的是——**这次港股实时行情抓失败了，用的是三天前的收盘价**。它照样说「高」，因为缺失字段传给它就是个 `None`，**它看不出区别**。

## ⚠️ 这一项换过形态，别走回头路

原方案是「**结论强制带来源（citation）**」。**已推翻。**

**为什么**：「MA5 = 1620.3」你要怎么给它挂 URL？新闻能挂链接让人点回原文核对，**行情/指标数据挂不了**——它能挂的只有「这个数是怎么来的」。**citation 是文本溯源范式，套到数值数据上是错配。**

**实证**：对照项目有 57.5k★、有整套契约文档，**却根本没有 citation 机制**——`data_sources` 只是自由文本字段，完整性检查压根不查它。它真正做的是「结论挂数据质量状态」。

**分治裁决**：
- **文本证据**（新闻/研报/财报/RAG）→ 挂来源，**软约束层**（prompt 契约 + policy_checks 纠偏）。
- **行情/指标类数值** → 挂「质量状态」，**硬约束层**（Python 代码事后强制打下置信度）。

## 验收标准

1. 核心块（quote / daily_bars / technical）任一是 `stale`/`fallback`/`fetch_failed` 时，**`confidence` 不可能是「高」**——不是 prompt 求 LLM 诚实，是 **LLM 输出完之后代码强行打下来**。
2. 限制说明被强制写进 `data_limitations`。
3. `fetch_failed`（这次抓挂了，是**故障**）与 `not_supported`（这个市场压根没这数据，是**正常**）在系统里是两个值。**现在项目大概率把两者都表达成「没数据」，导致要么误报要么漏报。**
4. 硬传导有独立纯规则测试（不依赖 LLM）。

## 三步

1. **贴状态**：给每个进 prompt 的字段贴八态之一 —— `available` / `stale` / `fallback` / `estimated` / `partial` / `missing` / `fetch_failed` / `not_supported`。
2. **汇总质量分**：核心块（quote/daily_bars/technical）权重高，基本面权重低。
3. **Python 硬卡结论**：核心块降级 → 强制打下 `confidence` + 注入限制文案。

## 落点

| 类型 | 文件 | 要做什么 |
|---|---|---|
| **新增** | `context_quality.py` — **落点待定，见 B-1，开工前问 Jason** | 状态枚举 + 字段/块封装 + 质量分。**`fetch_failed` 与 `not_supported` 必须分开** |
| **修改** | `backend/agents/tool_envelope.py` + `backend/agents/executor.py` | ToolEnvelope 统一附 `meta: {source, as_of, freshness, status}`。**这是状态机的原料来源，与 P2-2 的数据血缘是同一件事，合并施工**。ToolEnvelope 结构已存在，是加字段不是造轮子 |
| **修改** | `backend/acquisition/markets/*`、`backend/data_engine/fetchers/*` | 让 fetcher 层**如实上报 status**（现在失败与不支持都表达成空/None） |
| 修改 | `backend/agents/skills/deep_stock.md`、`backend/agents/skills/monitor.md` | prompt 契约加铁律：关键数字必须标注（来源，日期）。**降级为软约束层**——对新闻/研报这类文本证据仍然对路 |
| 修改 | `backend/advisor_engine/prompt_builder.py` | 同上（**注意 :16 处刻意去免责的设计，别误伤**） |
| 修改 | `backend/agents/policy_checks.py` | 加第三条规则：正文含百分比/金额而无来源标注 → 注入纠偏提示（复用现有「每 turn 最多纠偏一次」机制，见 `orchestrator.py:188` 附近）。软约束层 |
| 前端 | widget 显示数据时间戳 | 血缘落到 UI |
| 测试 | `backend/tests/agents/test_policy_compliance.py` | 软约束规则用例加这里；**状态机的硬传导另起纯规则测试**（参考 `test_turn_monitor.py` 写法） |

## 外部蓝本

| 文件 | 抄什么 |
|---|---|
| `daily_stock_analysis/src/schemas/analysis_context_pack.py` + `docs/analysis-context-pack.md` | 八态 `ContextFieldStatus` + 字段级 `{status, value, source, timestamp, fallback_from, missing_reason}` + block 级聚合 + `DataQuality{overall_score, level, block_scores, limitations}`。**它有整套专题文档 + 4 个测试文件，是本项最完整的蓝本** |
| `daily_stock_analysis/src/phase_decision_guardrail.py`（450 行） | **硬传导的实现**：核心块降级 → 代码强制打下 `confidence_level` + 注入限制文案。含**否定检测**（避免「不建议立即买入」被误判成「立即买入」） |
| `TradingAgents-main/tradingagents/dataflows/market_data_validator.py` | **确定性 ground-truth 快照**：分析开局注入一份本地库直查的数字快照（截止分析日 OHLCV + 固定指标集，不经 LLM），强制作为全部数字的唯一真源，冲突须标注而非编造。配套 look-ahead 断言与 `resolve_instrument_identity`（分析前锁定标的身份）。**与状态机互补**：快照解决「数字从哪来」，状态机解决「这数可不可信」 |

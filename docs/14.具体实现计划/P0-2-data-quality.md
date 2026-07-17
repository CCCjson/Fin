---
id: P0-2
title: 数据质量状态机 + 置信度硬传导
size: 中
depends: 无（与 P2-2 ToolEnvelope 血缘合并施工）
blocked_by: B-1 已解 → 落点 backend/common/（Jason 拍板）
paths_verified: 2026-07-17
status: ✅ 已完工 2026-07-17（d216c4e / 1c7b3c0 / b2ba21a / 030cdd3，四笔）
---

# P0-2 数据质量状态机 + 置信度硬传导

> ## ✅ 已完工（2026-07-17）—— 下方是原始计划，实施偏离见本框
>
> **四条验收标准全部达成**，实测：港股（财务恒缺）data_quality=100/good **不被系统性降级**；
> 日线陈旧→composite 72→60、BUY→HOLD、raw_composite 留痕。代码承载物：
> - 内核 `common/context_quality.py`（八态+块聚合+质量分+`clamp_confidence`，纯逻辑/零DB）
> - 新鲜度 `common/market_freshness.py`（市场参考交易日=最近覆盖达标日，治 max(date) 病）
> - 查库桥 `data_engine/quality_probe.py`；`health.get_freshness` 重写（按市场+木桶取短板）
> - 血缘 `tool_envelope.py`（第三个正交维度 quality，警告前置绕过截断+message优先两坑）
> - 硬钳层1 `cockpit_engine/scorer.py`（SCORER_VERSION v1→v2，+dimension_coverage/raw_composite）
> - 硬钳层2 `agents/quality_guard.py`（收尾追加更正 chunk，堵 `_finalize` 绕过校验的洞）
> - 数据层 `quote_router._canonical` 干掉 else 0 兜底 + 加 source/as_of；engine 按市场分 try
>
> ### 实施时对本卡的六处偏离（都已跟 Jason 确认，别改回去）
>
> 1. **B-1 落点 = `backend/common/`**（分层铁律，Jason 拍板）。内核落 `context_quality.py`，
>    新鲜度判定另拆 `market_freshness.py`（两件事：一个管整市场断更，一个管单票跟不跟得上）。
> 2. **`not_supported` 不参与扣分**（权重剔除后重归一化），**与蓝本刻意分歧**。蓝本给它
>    70 分导致它自己的港美股 capital_flow 系统性降级。本项目 FinancialData 港美股各 0 只，
>    照抄会在 Jason 刚接通港美股时把它们全线打死。4 条门禁盯着，完整照抄蓝本会一起红（已实测）。
> 3. **否定检测整段不抄**。蓝本否定词表末位裸「不」→「不得不立即买入」被判否定→护栏漏放，
>    且 grep 确认蓝本自己一个测试都没覆盖那段。我们的 action 结构化，只匹配正面「高把握」措辞。
> 4. **硬传导用「追加更正 chunk」不用 nudge 重跑**。文本边流边发收不回，nudge 会让前端
>    （append-only）同时挂两段文本。且顺带堵上 `_finalize`（熔断/保险丝）绕过校验的既有洞。
> 5. **compute_quality 加 `scope` 参数**（本卡没写）。接 get_daily_data 时踩到：只查日线的
>    工具被「没有实时行情块」扣分（健康茅台判 51/poor）。scope=本次该看哪些块，**必须显式传
>    不许从 blocks 键自动推断**（否则漏填=静默豁免）。「不看」≠「该看却没有」，两条门禁钉住。
> 6. **confirm_gate 不编 confidence**（本卡原写「补 confidence 0-100」）。交易确认没有结构化
>    置信度，凭空塞是撒谎。改记「这笔交易在什么数据质量下确认的」，给 P0-3 与后验评估用。
>
> ### 顺带修的真问题（不在本卡范围，已解决）
> - `quote_router` 三个源的 `as_of` 此前恒为 `now()` —— 实测 17:49 抓到的行新浪自报 15:34，
>   凭空把收盘价说新两小时。新浪 [30]+[31] / 腾讯 [30] 字段实抓核实后接上真实报价时刻。
> - `engine.get_realtime_quotes` 整个多市场循环包一个 try：港股 yfinance 抛异常→A股已拿到的
>   数据全丢。拆成按市场分 try + `_with_status`（区分 fetch_failed / proxy_exhausted）。
> - `portfolio_tools` 的 `except → "eod"`：盘中实时抓失败伪装成收盘价 → 改标 `eod_fetch_failed`。
>
> ### 遗留（不在本卡范围，想做时再说）
> - **软约束层第三条 policy_checks 规则（百分比/金额无来源标注→纠偏）没做**：会在几乎每条
>   MoneyBill 回答上误触发（它们都在引用工具返回的真实数字），假阳性噪声比价值大。
>   monitor.md 的数据质量契约（软约束层的 prompt 那半）已做。
> - **technical 块状态机没接**：目前只接了 quote / daily_bars 两个核心块。technical 走的是
>   daily_bars 派生（日线陈旧则技术面必然陈旧），暂由 daily_bars 代表；要独立态另说。
> - **P2-2 的 A（跨源抽检）另开卡**：回填期做抽检噪声淹没信号，等港美股数据齐了再做。

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

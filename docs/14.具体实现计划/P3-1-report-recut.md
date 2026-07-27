---
id: P3-1
title: report_* 按能力重切 + 关键假设/跟踪指标章节 + Audit 收尾
size: 中
depends: P0 / P1
blocked_by: B-2（成稿长文 vs 快查是否合并，见 00-PLAN §5）
paths_verified: 2026-07-07 ⚠️ 待核实（但下方四条硬约束已于 07-17 实探核实）
---

# P3-1 report_* 按能力重切

## ⛔ 开工前先读这两条，否则会走回头路

1. **不需要 LangGraph，不需要 planner，不需要 DAG。** 「我要什么他给什么」正是 ReAct 的强项——调一个工具、看到重大利空、立刻改道深挖；**图是「先定计划再执行」，反而是降级**。**病在工具粒度，不在编排层。**
2. **`ReportPlanner` / `CHAPTER_DEPS` / `planner.py` / `subagents/report.py` / `pdf_exporter.py` 已于 13.2-6 硬删。** 任何写着「ReportPlanner 已有雏形」的旧文档都不成立。**PDF 导出已经没有了。**

## 方向

报告作为「产物形态」**退役**——不再是「先有八章模子、往里填内容」，而是**「我需要什么，他输出什么」**。

**判据：一个工具 = 一个你会单独问出口的问题。** 你从不会只问「市场总览 + 板块轮动」这个组合，它们就不该捆在一个工具里。

| | |
|---|---|
| **现状粒度**（报告章节形状） | `report_market`=[Ch2 市场总览, Ch4 板块热点与北向]、`report_strategy`=[Ch6 上期回顾, Ch7 信号与策略表现]、`report_news`=[Ch3]、`report_positions`=[Ch5]、`report_picks`=[Ch8] → **要看北向也得搭送总览一起成稿** |
| **目标粒度**（按会问出口的问题重切） | 市场总览/情绪、板块轮动、北向资金、持仓诊断、上期推荐回顾、信号与策略表现、买入候选、新闻舆情 |

**「来一份完整周报」自动降级为普通用例**：MoneyBill 调 N 块积木、自己写纵览，**不需要任何特例代码**，也不需要 DAG 并发（并发的前提是「所有章都要跑」，而按需取用根本不会全跑）。

## 🔒 四条硬约束（迁移中最易**静默破掉**，已逐条实探核实）

1. **`【本章涉及标的】` 是 `policy_checks._backed_symbols` 的输入数据，不是日志**（`agents/subagents/report_sections.py::_summary_prefix`）。正文只截 600 字进 context，纵览引用的代码很容易被截掉；**丢了这行，纵览里每个代码都会被判「凭记忆瞎报」→ 强制重跑一轮。**
2. **`_SUBAGENT_NAMES` 必须继续排除 `report_*`**（`agents/policy_checks.py:29-32`），否则「子任务收尾只能一句话」的 **150 字上限会把纵览压死**。
3. **picks 的 Ch8 批次链式不可拆并行**：`ch8_accumulated` 把前批已推荐标的注入后批 prompt 去重（`report_engine/section_writer.py:89-90, 109-114`）。**拆并行 = 退回旧 `generator.py` Bug#6（重复推同一只股）。**
4. **prompt golden 要同步**：`tests/report/test_section_prompt_parity.py` 打在纯函数层（`build_section_calls`）、拿退役前冻结的 golden 钉死 prompt 逐字不变。**重切工具边界必然改动 `SECTION_CHAPTERS`——要连同更新 golden 并说明改了什么。**

## 落点

| 类型 | 文件 | 要做什么 |
|---|---|---|
| **真源** | `backend/report_engine/prompt_builder.py`（`SECTION_CHAPTERS` 积木映射） | **重切的真源就在这**。⚠️ 该目录被 `.gitignore`，只在本地 |
| 修改 | `backend/agents/subagents/report_sections.py` | 五个章节 subagent + `SECTION_SUBAGENTS` |
| 修改 | `backend/agents/subagents/__init__.py` | 注册 / `_SECTION_DESCRIPTIONS` 工具描述 |
| 修改 | `backend/agents/tool_groups.py` | 新工具**必须归组** |
| 测试 | `backend/tests/report/test_section_prompt_parity.py` | **golden 要同步** |

## 连带 A：三个结构化章节坑位（便宜，同卡做）

**报告要素清单缺口**：证据引用 ✅｜主要风险 ✅｜反方观点 ⚠️ 仅定性提法｜失效条件 ⚠️ 仅 advisor 有｜置信度 ⚠️ 周报日报无｜**关键假设 ❌｜后续跟踪指标 ❌**

`prompt_builder` 的**共享积木机制现成**（人设在 `_persona()`、通用规则是模块级常量）——加三块积木进 `build_multi`：

1. 「本报告关键假设」
2. 「跟踪指标清单（指标 + 当前值 + 触发阈值）」→ **落库接 P1-4 的 price_alert_monitor**；非价格类（如「毛利率下滑破 X%」）挂季度财务更新后检查
3. 「整体置信度」

**格式直接抄卖方研报**：投资要点/估值/关键假设/风险提示/敏感性表。任选一份大行首次覆盖报告即可。

## 🪙 crypto 兼容（2026-07-27 重设计｜**分类 ②：要加 crypto 分支**）

> 先读 `00-PLAN.md` §4b 通用口径。

**本卡的重切判据「一个工具 = 一个你会单独问出口的问题」对 crypto 天然成立**，crypto 兼容不需要改判据，只需要**加一组 crypto 专属的积木**。

### 现有八块积木的 crypto 对照

| 目标粒度（股票） | crypto 上 |
|---|---|
| 市场总览/情绪 | ✅ 有对应物（regime 维 + 恐惧贪婪指数 + BTC 主导度） |
| **板块轮动** | ⚠️ **无标准行业分类**（同 P2-3 B 段）。对应物勉强是「L1/DeFi/Meme 板块」但无权威成分 |
| **北向资金** | ❌ **概念不存在**。对应物 = 稳定币供应变化 + 交易所净流（后者免费档拿不到） |
| 持仓诊断 | ✅ crypto 持仓已有（`cost_basis.py` 用 myTrades 回放重建） |
| 上期推荐回顾 | ✅ 通用（但见下方 ⚠️） |
| 信号与策略表现 | ✅ crypto 有自己的策略引擎（`crypto_strategy`） |
| 买入候选 | ✅ 通用 |
| 新闻舆情 | ✅ crypto 新闻链已有 |

**crypto 独有、股票侧没有对应物的积木**（要新增）：**解锁日历**（未来 30 天大额解锁）、**资金费率/衍生品持仓**（deriv 维）、**排雷结果**（空壳/硬否决事件）。

### ⚠️ 「上期推荐回顾」在 crypto 上会**先撞到 P0-4**

这块积木读的是 `DecisionLog`，而 [[P0-4]] 实测：**`decision_logs` 里 54% 是 crypto，其中 45 条是订单执行回执不是建议，另有 39 条 `entry_price=100.0` 的假价格**。

**本卡的「上期推荐回顾」若在 P0-4 之前接 crypto，会把执行回执当成推荐念出来，还会念出 +64900% 的收益率。** → **本卡的 crypto 分支硬依赖 P0-4 先完工。**

### 落点增量

| 类型 | 文件 | 要做什么 |
|---|---|---|
| 修改 | `backend/report_engine/prompt_builder.py`（`SECTION_CHAPTERS`） | 加 crypto 积木组；**⚠️ 该目录被 `.gitignore`，只在本地** |
| 修改 | `backend/agents/subagents/report_sections.py` | crypto 章节 subagent；`【本章涉及标的】` 的 symbol 格式要能容纳 `.BN` 后缀（🔒 硬约束 1 —— **丢了这行 crypto 代码会被判「凭记忆瞎报」** ） |
| 测试 | `tests/report/test_section_prompt_parity.py` | golden 同步（🔒 硬约束 4） |

## 连带 B：Audit 收尾环节

**不必是完整 agent**——在 report/deep_stock 收尾时加**一次廉价 LLM 调用**（或扩展 `policy_checks` 规则），检查：引用是否存在、数字是否与工具结果一致、结论与证据是否矛盾。

落点：`backend/agents/policy_checks.py` 扩规则，或 `backend/agents/orchestrator.py` 收尾处。

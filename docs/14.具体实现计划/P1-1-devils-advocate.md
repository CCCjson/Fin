---
id: P1-1
title: 反方 subagent run_devils_advocate
size: 中
depends: 无
paths_verified: 2026-07-07 ⚠️ 待核实
---

# P1-1 反方 subagent `run_devils_advocate`

> ⚠️ **路径是 2026-07-07 的，早于 13.x 大重构（9 域）。开工第一步 grep 确认文件仍在、行号仍对；对不上先报告 Jason，不要猜。**

> **一句话**：项目所有输出都是**单边**的——grep `debate`/`critic`/`反方`/`audit` 零命中。**没有任何 agent 消费并挑战另一个 agent 的输出。** 这是十项能力里投入产出比最高的一项。

## 🔍 探查前置（2026-07-17 Jason 拍板：FinRobot 有探索价值）

> **规矩**：探完更新本卡再写代码；结论写回本卡，本表行标 ✅ + 日期。**与现有方案冲突 → 先报告 Jason。**

| 档 | 对象 | 带着这个问题去 | 状态 |
|---|---|---|---|
| **T2** | **ai-hedge-fund**（virattt，开源） | ⚠️ **本卡唯一的实现级空白**：TradingAgents 的辩论实现**已判不抄**（字符串拼 history 撑爆 context），**于是我们手上只剩一个被否决的样本**——反方 subagent 要照着谁写？具体问题：① 它的多分析师（价值/成长/情绪/技术）+ risk manager + portfolio manager 是**怎么互相消费输出**的？② **反方/风控角色拿到的是什么形状的输入**（全文？摘要？结构化对象？）——这直接决定我们 `devils_advocate.py` 的入参契约。③「工程复杂度与本项目相当」这个说法**是印象、没有依据**，顺便验掉 | ❌ 未探 |
| **T2**<br/>（触发式） | **FinRobot**（AI4Finance，开源） | Perception / Brain / Action 的**分层与职责划分粒度**。**探它不是为了这第 5 个 subagent，是为了第 8 个、第 10 个。** 具体问题：① 它按什么维度切 agent（数据源？分析类型？决策阶段？）② 到了 N 个 agent 时，**「谁调谁」是怎么管的**——我们现在是主 agent 平铺调所有 subagent，这个形状能撑到几个？③ 它有没有踩过「agent 加多了反而互相打架」的坑？ | ❌ 未探 |

**Jason 的原话（2026-07-17）**：「**只是目前的架构上不需要这么多的 subagent，但不代表后续随功能增多而需要引入。**」

**触发条件说明**：

- 本卡只加**第 5 个** subagent，`00-PLAN.md` 的现有形状完全够用——**探 FinRobot 不是本卡的开工前提，不做也能写代码。**
- 但本卡是 **subagent 阵容第一次扩张**，是个自然的探查窗口：**趁现在只有 5 个、还改得动的时候，看清楚 10 个时长什么样。**
- ⛔ **C1 裁决依然有效**：不上 planner、不补白皮书那 9 个角色名（见 `docs/14` §12.2）。**探它 ≠ 要照它改架构**——探完的产出应该是「**未来加到 N 个时的分层预案**」，写进 `00-PLAN.md` 的 Backlog，**不是现在动 `agents/` 的结构**。

## 为什么现在没有

架构收敛的**有意后果**：2026-07 把一切归拢到「一个主 agent + function-calling」，为的是 token 成本和维护性。单主循环范式下 subagent 是「贵一点的工具」，天然不产生 agent 之间的横向对话。

**缺的不是九个 agent 的名字，是「对抗性」这一类协作模式。** （Planner 已裁决**暂不上**，别顺手加。）

## 验收标准

1. 新第 5 个 subagent，输入 deep_stock 的结论文本，职责唯一——**反驳它**：反方证据 / 失效场景 / 被忽略的风险。
2. deep_stock 给出 BUY 结论后，主 agent 会追加反方审视。
3. 辩论 **1-2 轮封顶**。

## 落点

| 类型 | 文件 | 要做什么 |
|---|---|---|
| 抄的模式 | `backend/agents/subagents/deep_stock.py` | v2 的隔离 mini-AgentSession + 窄工具集（:30-33 的 6 只读工具清单）+ 复用 `MonitorOrchestrator._loop`，**整个骨架照抄** |
| 抄的模式 | `backend/agents/subagents/base.py` | `emit_subagent_done` 收尾契约（**必须以 subagent_done 事件收尾，返回 ToolEnvelope**） |
| 新增 | `backend/agents/subagents/devils_advocate.py` | 输入 deep_stock 结论文本，只反驳 |
| 新增 | `backend/agents/skills/devils_advocate.md` | 反方人格 prompt（参考 `deep_stock.md` 的四段输出契约格式） |
| 修改 | `backend/agents/subagents/__init__.py` | 注册（`is_subagent=True, group="deep_agents"`） |
| 修改 | `backend/agents/tool_groups.py` | **必须归组**（deep_agents 组），否则 `_validate` 断言直接 RuntimeError |
| 修改 | `backend/agents/skills/monitor.md` | 加纪律：deep_stock 给出 BUY 结论后追加反方审视 |
| 可选 | `backend/agents/widgets.py` + `frontend/src/components/moneybill/WidgetRenderer.tsx` | 新 widget：主结论 vs 反方意见并排双栏（WidgetRenderer 分发机制现成） |
| 测试 | `backend/tests/agents/`（参考 `test_subagent_contract.py` 系列） | subagent 契约测试 |

## 外部蓝本

`TradingAgents-main/tradingagents/agents/researchers/` + `agents/risk_mgmt/` + `graph/conditional_logic.py`

- **抄**：bull/bear 研究员与激进/保守/中性三方风控的**角色 prompt，直接翻译**；轮次控制思路可参考。
- **⛔ 实现勿抄**：它靠**字符串无限拼 history** 轮流喂 prompt，长辩论撑爆 context 且成本线性涨。仍用 deep_stock v2 mini-session 骨架，**1-2 轮封顶**。

## 连带（做这项时顺手，别单开）

**结构化 bull/bear**：定义 `{bull_case: [{claim, evidence, source}], bear_case: [...]}` schema，deep_stock 输出契约升级 + widget 渲染对称双栏。现在 bull/bear **只在 prompt 层**，无结构化对称对象。

---

## 🪙 crypto 兼容（2026-07-27 重设计｜**分类 ②：要加 crypto 分支**）

> 先读 `00-PLAN.md` §4b 通用口径。

### 🔴 核心问题：crypto **没有 deep_stock 可反驳**

验收标准 1 写的是「**输入 deep_stock 的结论文本**」。查代码：

| 检查 | 结果 |
|---|---|
| `agents/subagents/deep_stock.py` 的 crypto 命中 | **0** |
| `agents/skills/deep_stock.md` 的 crypto 命中 | **0** |
| `_DEEP_STOCK_TOOLS` 六只工具 | `get_daily_data` / `get_realtime_quote` / `get_cockpit_score` / `get_news_sentiment` / `get_stock_signals` / `search_knowledge` —— **全部是股票工具**（`get_cockpit_score` 是股票 cockpit，crypto 走独立的 `analyze_crypto`） |

**crypto 侧的「深度分析」是一个工具（`analyze_crypto`）不是 subagent**，产出是结构化 `ToolEnvelope`（含 dimensions / weights_used / screen / data_quality），不是自由文本结论。

### 于是本卡的入参契约要重新设计（**这是 crypto 分支的全部重点**）

反方 subagent 会拿到**两种形状完全不同的输入**：

| 来源 | 形状 | 反方能拿到什么 |
|---|---|---|
| `run_deep_stock` | **自由文本**结论（≤1500 字摘要） | 论点靠 LLM 从文本里读 |
| `analyze_crypto` | **结构化 dict**（五维分数 + 排雷结果 + 数据质量） | 论点是现成字段，**反方其实更好写** |

**两个方向，倾向 B**：

- **A**：反方只接文本 → crypto 侧先把 `analyze_crypto` 结果渲染成文本再喂。**简单，但把结构化信息降级成文本，白瞎了**
- **B（倾向）**：入参契约定义成 `{conclusion_text, structured?}`，两边都能喂，反方 prompt 按有无 `structured` 分支。**crypto 的排雷维本来就是天然的反方论据**

⚠️ **这个选择要在探查 ai-hedge-fund 时一并回答**（本卡开头探查前置的问题 ②「反方角色拿到的是什么形状的输入」）—— **它现在有了第二个必须回答的理由**。

### crypto 的反方论据来源（股票侧没有的）

crypto 有一整套**结构性看空论据**，是股票分析里没有对应物的，反方 prompt 应该显式要求检查：

| 论据 | 数据源（已有） |
|---|---|
| 代币解锁砸盘 | 真解锁表（[[crypto-decision-sources-v2]]） |
| 链上资金流出 / 交易所储备异动 | flow 维 |
| 代币经济学缺陷（通胀率、集中度） | 排雷层 |
| 硬否决级事件（下架 / 被盗 / 监管执法） | `crypto_intel_engine/news.py:169` `check_hard_events` |

> 💡 **`check_hard_events` 已经是一个规则版的反方** —— 它做的正是「找否决这笔交易的理由」。本卡的 LLM 反方**不该重复它已覆盖的事件类型**，而应补它补不了的（叙事证伪、竞品替代、估值逻辑漏洞）。**开工时先读它的 `_HARD_EVENT_RULES`，避免重叠。**

### crypto 分支的落点增量

| 类型 | 文件 | 要做什么 |
|---|---|---|
| 新增 | `backend/agents/subagents/devils_advocate.py` | 入参契约支持结构化输入（方向 B） |
| 新增 | `backend/agents/skills/devils_advocate.md` | prompt 分支：股票用财务/估值/竞争视角；crypto 用解锁/链上/代币经济学视角 |
| 修改 | `backend/agents/skills/monitor.md` | 纪律要写成「**deep_stock 或 analyze_crypto** 给出 BUY 后追加反方」，别只写 deep_stock |
| 测试 | `backend/tests/agents/` | 契约测试要**两种输入形状都覆盖** |

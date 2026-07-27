---
id: P3-2
title: subagent 并行 + trace 离线回放 + 决策留痕面板
size: 大
depends: 无
paths_verified: 2026-07-07 ⚠️ 待核实
note: 工程纵深，商业化打磨期再做
---

# P3-2 subagent 并行 + trace 回放

> ⚠️ **路径是 2026-07-07 的，早于 13.x 大重构。开工第一步 grep 确认。**

> **根因（trace 侧）**：留痕是「**写入优先**」建起来的——写入时的用户故事是「以后蒸馏/训练用」，不是「现在审计用」。**读取侧从来没被设计**：DecisionLog 无 HTTP 路由无前端页，trace 无查询 UI，**数据躺在 DB/JSONL 里只有 LLM 和离线脚本能碰**。
>
> **别急着做 UI。审计需求先用脚本满足。**

## A. trace 离线回放（先做这个，最便宜）

| 类型 | 文件 | 要做什么 |
|---|---|---|
| 新增 | `backend/scripts/trace_replay.py` | 输入 `decision_id` → 输出该轮完整 prompt + 工具原文链。关联 `backend/data/agent_traces/{session}.jsonl`（turn trace）与 `agent_traces_raw/`（工具原文），**用 `call_id` 串起来**（写入逻辑见 `backend/agents/trace.py`） |

**为什么需要**：turn trace 里工具结果是 **3000 字截断 summary**，raw 里有全文，但**两份数据无关联查询手段**。

## B. 决策留痕面板（遵守「不新开页面」铁律）

| 类型 | 文件 | 要做什么 |
|---|---|---|
| 修改 | `frontend/src/components/datamonitor/` | 加「决策留痕」卡片（**data-monitor 是任务可视化的规定归口**，铁律 3） |
| 修改 | `backend/agents/widgets.py` | decision 详情 widget（含 `input_snapshot` 展开） |

> ⚠️ `decision_tools.py` 注释提到的「/decisions 页面」**前端不存在**。别照着注释找。

## C. subagent 并行（真·大工程，最后做）

**受益场景**：报告多章节独立数据采集。

**难点**：需解开 `run_lock` 的粒度——subagent 各自 mini-session **本就隔离**，父 session 等待期间可并行。

| 类型 | 文件 | 要做什么 |
|---|---|---|
| 修改 | `backend/agents/tool_dispatch.py` | 并行编排 |
| 修改 | `backend/agents/orchestrator.py`（`_run_subagent`，:322-355） | **因 TurnMonitor 记账耦合未搬离**，动这里要小心 |
| 修改 | `backend/agents/context.py`（run_lock 粒度，:55） | 同 session 现被 `run_lock` 强制串行 |

> ⚠️ **与 P3-1 硬约束 3 冲突**：picks 的 Ch8 批次链式**不可拆并行**。并行改造不得波及它。

## 🪙 crypto 兼容（2026-07-27 重设计｜**分类 ①：市场无关**）

> 先读 `00-PLAN.md` §4b 通用口径。

**A（trace 回放）和 C（subagent 并行）与市场完全无关** —— trace 是按 `call_id` 串 prompt 和工具原文，`run_lock` 是会话级并发控制，两者都不看 symbol。**落点表不动。**

**B（决策留痕面板）有两条 crypto 注意事项**，都不改方案、只影响 UI 呈现：

1. **crypto 是留痕的大头，不是边角** —— 实测 `decision_logs` 里 **crypto 占 54%**（`00-PLAN.md` §4b.1）。面板默认视图若不分桶，**打开就是满屏 BTC**。**按 source / market 分桶是必需项不是可选项。**
2. **⚠️ 面板会把 [[P0-4]] 的脏数据直接摆到 Jason 眼前** —— 39 条 `BTCUSDT.BN BUY entry=100.0` 和 45 条订单执行回执，现在藏在库里没人看见，**做了面板就全暴露了**。

   这**不是坏事**（面板正好会暴露数据质量问题，这是它的价值之一），但**做本卡 B 段前最好先做 P0-4**，否则第一次打开面板看到的就是一堆需要解释的垃圾。

## 参考

- **LangSmith / Langfuse**：trace→run→span 层级组织 + 「按 session 回放」交互。**Langfuse 开源可自部署**，甚至可考虑把 JSONL trace 直接导出成 Langfuse 格式而**不自建 UI**。⚠️ **格式兼容性从未验证过**——要走这条路，**第一步就是验格式**，别先动手写导出器。

> **MLflow 已于 2026-07-17 移到 P0-1**——它讲的是 `prompt_version` 管理，而 `prompt_version` 落地是 **P0-1 的验收标准 3**。挂在本卡（标着「远期、商业化打磨期再做」）等于 P0-1 开工时根本看不到它。

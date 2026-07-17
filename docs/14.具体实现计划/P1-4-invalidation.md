---
id: P1-4
title: 失效条件结构化 + 自动盯失效
size: 中
depends: P0-1（留痕）
paths_verified: 2026-07-07 ⚠️ 待核实
---

# P1-4 失效条件结构化 + 自动盯失效

> ⚠️ **路径是 2026-07-07 的，早于 13.x 大重构。开工第一步 grep 确认。**

> **一句话**：deep_stock 输出的「失效条件」是**给人看的文本，没有机器去盯**。结论产出即死亡——不被检查失效、不被自动更新。
>
> **闭环基建是项目强项**（SignalTracker、PredictionValidator、price_alert_monitor 都在），唯独「研究结论」这条线没接。

## 验收标准

1. 失效条件从自由文本改为**机器可查格式**：`{metric: "close", op: "<", value: 18.5}`。
2. 落库后复用 `price_alert_monitor` 引擎盯盘。
3. 触发时发通知：「你 6 月 20 日对 XX 的看多结论已触发失效条件」。

## 落点

| 类型 | 文件 | 要做什么 |
|---|---|---|
| 修改 | `backend/agents/skills/deep_stock.md` + `backend/agents/subagents/deep_stock.py` | 失效条件改 `{metric, op, value}` 结构 |
| 修改 | `backend/data_engine/storage/models.py` | 失效条件落库（挂 `DecisionLog` 加 JSON 字段，或新表） |
| 抄的模式 + 修改 | `backend/automation/price_alert_monitor.py` | **30s 轮询全市场快照比对 active 条件的引擎现成**——扩展支持「结论失效条件」类目，触发走 `backend/business_events.py` + `backend/notify/`（Mac 通知） |
| 修改 | `backend/automation/scheduler.py` | 非价格类条件（如「毛利率下滑破 X%」）挂低频检查 / 季度财务更新后检查 |

## 连带：研究结论 TTL（同卡顺手做）

DecisionLog / 报告加 `valid_until`。过期未复核的结论在被 `get_decision_history` 引用时**自动标注「已过期，建议重新研判」**。

## 参考

**本卡无外部参考，不需要探查前置。**

> **TipRanks 已于 2026-07-17 移到 P0-1**——它讲的是「每个建议都被记分」（= 胜率回填），那是 P0-1 的活；本卡是**失效条件盯盘**，两回事。`docs/14` §7 原本也是把它对应到 P0-1 的，挂在这里是切卡时挂错了。

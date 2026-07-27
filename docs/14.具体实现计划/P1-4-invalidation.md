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

---

## 🪙 crypto 兼容（2026-07-27 重设计｜**分类 ②：要加 crypto 分支**）

> 先读 `00-PLAN.md` §4b 通用口径。

### 🔴 开工前必读：验收标准 2 的前提**当前不成立**（且不只对 crypto）

本卡验收标准 2 是「落库后**复用 `price_alert_monitor` 引擎盯盘**」。查代码后：

```
price_alert_monitor._scan_once()            automation/price_alert_monitor.py:38
  └→ fetch_quotes_by_symbols(symbols)       acquisition/markets/realtime.py:585
       └→ quote_router.fetch_quotes(symbols) acquisition/markets/quote_router.py:246
            └→ targets = [s for s in symbols if _to_prefixed(s)]   ← 只留 .SH/.SZ
```

`quote_router.fetch_quotes` 的 docstring 原文：**「只处理沪深标的（`.SH`/`.SZ`）；HK/US 忽略，交由各自 fetcher。」**

**后果**：`BTCUSDT.BN`、`00700.HK`、`AAPL` 传进去会被过滤掉 → `quote_map.get(alert.symbol)` 返回 None → `_scan_once` 第 66-67 行 `continue` → **静默跳过，不告警、不报错、不写日志**。

| 市场 | price_alert_monitor 实际能盯吗 |
|---|---|
| A 股 | ✅ |
| **港股** | ❌ 静默失效 |
| **美股** | ❌ 静默失效 |
| **crypto** | ❌ 静默失效 |

⚠️ **这条超出 crypto 范围**：本卡若按现有验收标准施工，**四个市场里三个的失效条件永远不会触发**，而且**没有任何迹象**表明它没在工作。「静默跳过」是最坏的失效方式 —— 系统会显得一切正常。

**开工第一步必须先决定**：是先补齐 `price_alert_monitor` 的多市场取价（本卡范围外的前置工程），还是本卡自带一条取价路径。**这个选择要报 Jason，别自己拍板。**

### crypto 的三处概念差异

| 维度 | 股票 | crypto |
|---|---|---|
| **盯盘时段** | 有开盘/收盘，非交易时段轮询是浪费 | **7×24 无收盘**，要真·全天候。现有 30s 轮询在 crypto 上是**唯一合理**的（股票侧反而该按时段收敛） |
| **非价格类条件** | 「毛利率下滑破 X%」→ 挂季度财务更新后检查 | **没有财报**。对应物 = **代币解锁事件 / TVL 跌破 / 资金费率转负 / 交易所储备异动**（`crypto_intel_engine` 的 flow 维与真解锁表已有现成数据，见 [[crypto-decision-sources-v2]]） |
| **结论 TTL**（本卡「连带」段） | 一份股票研究结论几周到几个月有效 | **币圈一天股市一年** —— crypto 结论的 `valid_until` 默认值**必须显著更短**。⛔ 别给两边用同一个默认值 |

### ✅ 可复用的 crypto 现成资产（别重造）

- `crypto_strategy` 的护栏体系与 `automation/position_guardian`（止损盯盘）已在跑，见 [[crypto-auto-trading-req3]]
- 待确认单机制 `crypto_strategy/pending.py` —— 失效条件触发后若要产生动作，走这条，**不是 `confirm_gate`**
- ⚠️ [[crypto-auto-trading-req3]] 记着一笔未完成的债：**「电平触发 → 边沿触发」（Jason 亲提最重要，仍未做）**。本卡的失效条件盯盘是**同一个问题**（条件持续满足时会不会每 30s 重复告警一次）—— **两件事应该一起解决，别各修一遍**。

### crypto 分支的落点增量

| 类型 | 文件 | 要做什么 |
|---|---|---|
| **前置**（范围待定） | `acquisition/markets/quote_router.py` 或本卡自带 | 多市场取价，否则验收标准 2 对三个市场落空（见上） |
| 修改 | `backend/data_engine/storage/models.py` | 失效条件的 `metric` 枚举要容纳链上指标（tvl / funding_rate / unlock_date / exchange_reserve），不能只有 `close`/`毛利率` 这类 |
| 修改 | `backend/automation/scheduler.py` | 非价格类 crypto 条件挂**链上数据刷新后**检查，不是「季度财务更新后」 |
| 复用 | `crypto_intel_engine` 的 flow 维 / 真解锁表 | 链上条件的数据源已有，别新接 |

> **TipRanks 已于 2026-07-17 移到 P0-1**——它讲的是「每个建议都被记分」（= 胜率回填），那是 P0-1 的活；本卡是**失效条件盯盘**，两回事。`docs/14` §7 原本也是把它对应到 P0-1 的，挂在这里是切卡时挂错了。

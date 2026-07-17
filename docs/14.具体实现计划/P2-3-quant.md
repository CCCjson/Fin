---
id: P2-3
title: 量化补齐（参数敏感性 / 最小因子引擎 / 泄漏扫描）
size: 中
depends: 无
paths_verified: 2026-07-07 ⚠️ 待核实
---

# P2-3 量化补齐

> ⚠️ **路径是 2026-07-07 的，早于 13.x 大重构（回测已统一 C++ 单口径）。开工第一步 grep 确认。**

> **策略研究是十项里最强的一项**（C++ 回测 + walk-forward + 显式过拟合检测 `overfit_score = 1 - val_sharpe/train_sharpe`，>0.3 警告 >0.5 拒绝）。
>
> **缺口的共性是「验证的系统化」**：单点做对了（shift、T+1、train/val 分离），但**没把「防泄漏/防过拟合」上升为可自动执行的检查套件**。

## 🔍 探查前置：Qlib + RD-Agent 编为**一组**，推迟到 **alpha_lab 精进期**（2026-07-17 Jason 拍板）

> **这两个是一组，一起探，探查时机 = 精进 alpha_lab 的时候，不是本卡开工时。**
>
> **为什么编组**：它们的价值**全落在 alpha_lab 那条线上**——Qlib 给的是「因子评估的口径」，RD-Agent 给的是「LLM 自动提因子→实现→回测→迭代」这个循环的下一形态，**而 alpha_lab 已经是这个形状了**。分两次探是浪费；等真要精进 alpha_lab 时一次读完，对照的是同一个东西。

| 档 | 对象 | 带着这个问题去 | 状态 |
|---|---|---|---|
| 组队 | **Qlib** `qlib.contrib.eva` | 「**因子引擎的事实标准**」这个说法**从来没人验证过**，却已经当依据写进排期了。要探明：① IC/IR 到底怎么算——**分组数、去极值、行业/市值中性化做不做**？② 分层回测的口径？③ 它的表达式引擎值不值得抄，还是对我们过重？ | ❌ 未探（等 alpha_lab 精进期） |
| 组队 | **RD-Agent** | 「LLM 自动提因子→实现→回测→迭代」＝ alpha_lab + idea_miner 的下一形态。**我们已经有这个循环了**，所以问题不是「怎么做」而是：**它比我们多做对了什么？**（提因子的方式？迭代的终止条件？过拟合的挡法？——我们的 `overfit_score` 是自己定的） | ❌ 未探（等 alpha_lab 精进期） |

**对本卡的影响（重要）**：

- **A 段（参数敏感性）和 C 段（泄漏扫描）不受影响**，随时可开工——它们不依赖任何外部口径。
- **B 段（最小因子引擎）若在 alpha_lab 精进期之前开工** → **IC/IR 的口径就是我们自己定的**。可以做，但必须在代码里留注释标明「**口径待与 Qlib 对齐**」，**别当成定论**。否则将来对照完要改口径时，已经算出来的历史 IC 全部作废——这正是 P0-1 用 `engine_version` 在防的同一类病。

## A. 参数敏感性（最便宜，先做）

**核心指标**：最优参数邻域是否**平坦**（平坦 = 稳健，尖峰 = 过拟合）。walk-forward 是**寻优不是敏感性**，两者别混。

| 类型 | 文件 | 要做什么 |
|---|---|---|
| 现成资产 | `backend/api/routes/backtest_cpp.py` 的 `POST /backtest_cpp/batch` + `backend/services/backtest_cpp_client.py` | **批量回测 API 现成**，敏感性 = 包一层「参数网格 × 邻域扰动 → 热图」 |
| 修改 | `backend/agents/tools/backtest_tools.py` | 加 `run_param_sensitivity` 工具。**⚠️ 发 C++ body 必经 `common/market.py` 的 `to_cpp_market()`**（见 GOTCHAS 市场命名坑）。**归组 `tool_groups.py`** |
| 可选前端 | `frontend/src/components/backtest/`（已有 14 个回测组件） | 热图渲染 |

**参考**：vectorbt 的参数化回测热图（未核实，当思路）。

## B. 最小因子引擎

**不必按 `docs/phase4-factor-engine.md` 全量实现**——那是纯设计文档，`backend/factor_engine/` **目录当前不存在**（07-17 复核）。

**只做两个函数**：IC/IR 计算 + 分层回测。数据就是 `daily_quotes` × `financial_data`。

| 类型 | 文件 | 要做什么 |
|---|---|---|
| 新增 | `backend/factor_engine/`（**目录不存在**，按 phase4 文档裁剪到最小） | IC/IR + 分层回测两个函数 |
| 修改 | `backend/alpha_lab/evaluator.py` | 因子 IC 作为策略评估补充维度（该文件已有 `_detect_overfitting`，加在旁边） |

> ⚠️ **口径待与 Qlib 对齐**（见本卡开头「探查前置」）。Qlib 已与 RD-Agent 编组、**推迟到 alpha_lab 精进期**，所以本段**不再阻塞**——但 IC/IR 的口径在对照前是**我们自己定的**，代码里必须留注释标明，别当定论。

## C. 泄漏检查套件

| 类型 | 文件 | 要做什么 |
|---|---|---|
| 新增测试 | `backend/prediction_engine/` 特征防泄漏**性质测试** | 「特征时间戳 ≤ 标签时间戳」，针对 `features.py:93-96` 的 shift 逻辑 |
| 修改 | alpha_lab 沙箱 | 执行前 AST 扫描 `shift(-n)` / 未来列引用 |

## 本卡不做（记录以免遗漏）

- **Survivorship bias**：股票池是当前存续股票。修法：deep_history 保留退市股票（akshare 可取终止上市列表），回测股票池按「当时点存续」过滤。**中期工程，另开卡。**
- **组合层面 portfolio construction 优化器**：真缺口，已确认保留，但未进 P0/P1。
- **RD-Agent 对照**：已移到本卡开头「探查前置」，与 Qlib **编为一组、推迟到 alpha_lab 精进期**（2026-07-17 Jason 拍板）。此处不再重复。

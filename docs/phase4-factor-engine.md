# Phase 4：因子研究 (Python)

> 展示能力：量化研究流程、IC/IR 分析、因子组合、多空回测

## 1. 概述

"因子"是用来预测股票未来收益的指标。因子研究就是：
1. 定义一个因子（比如"过去 20 天涨幅"）
2. 每天给所有股票计算因子值
3. 看因子值高的股票未来是不是真的涨得好

这个模块集成到现有 Fin 项目的 Python 后端，复用 `DataEngine` 获取数据。

## 2. 目录结构

```
backend/factor_engine/
├── __init__.py
├── engine.py                        # FactorEngine 编排器
├── factors/
│   ├── __init__.py
│   ├── base.py                      # BaseFactor 抽象基类
│   ├── momentum.py                  # 动量因子
│   ├── value.py                     # 价值因子
│   ├── volatility.py                # 波动率因子
│   ├── technical.py                 # 技术因子
│   └── growth.py                    # 成长因子
├── analysis/
│   ├── __init__.py
│   ├── ic_calculator.py             # IC/IR 计算
│   ├── decay_analyzer.py            # IC 衰减分析
│   ├── combiner.py                  # 因子组合
│   └── neutralizer.py               # 中性化处理
└── backtest/
    ├── __init__.py
    ├── long_short.py                # 多空组合回测
    └── quantile.py                  # 分位组合分析
```

## 3. 核心概念

### 3.1 什么是因子？

举例：**动量因子** = 过去 20 天的累计涨幅

```
                    动量因子值    未来 5 天涨幅
股票 A (涨了 15%)     0.15         +3%
股票 B (涨了 8%)      0.08         +1%
股票 C (跌了 2%)     -0.02         -2%
股票 D (跌了 10%)    -0.10         -4%

→ 动量因子和未来涨幅正相关 → 这是一个有效因子！
```

### 3.2 什么是 IC（信息系数）？

IC = 因子值和未来收益之间的**相关性**（用 Spearman 秩相关）。

```
IC = 1.0  → 完美预测（不可能）
IC = 0.05 → 有一点点预测能力（在量化里已经不错了）
IC = 0.0  → 完全没用
IC = -0.05 → 反向预测（反着用也可以）
```

每天算一个 IC，一年约 250 个 IC 值，取平均就是 **Mean IC**。

### 3.3 什么是 IR（信息比率）？

```
IR = Mean(IC) / Std(IC)

好因子的标准:
  |Mean IC| > 0.03
  IR > 0.5
```

IR 高说明因子不仅平均有效，而且**稳定**有效（不是时灵时不灵）。

### 3.4 什么是 IC 衰减？

因子对**不同时间段**的预测能力：
```
1 天后:   Mean IC = 0.08  (最强)
5 天后:   Mean IC = 0.05
10 天后:  Mean IC = 0.03
20 天后:  Mean IC = 0.01  (几乎失效)

→ 这个因子适合短线，不适合长线
```

### 3.5 什么是多空回测？

```
每个月初:
  1. 给所有股票算因子值
  2. 因子值最高的 20% → 做多（买入）
  3. 因子值最低的 20% → 做空（卖出）
  4. 组合收益 = 多头收益 - 空头收益

如果组合持续赚钱 → 因子有效
```

## 4. BaseFactor 基类

```python
class BaseFactor(ABC):
    """所有因子的基类，仿照现有 BaseIndicator 模式"""

    def __init__(self, name: str, category: str, params: Dict = None):
        self.name = name              # 因子名称，如 "momentum_20d"
        self.category = category      # 因子类别，如 "momentum"
        self.params = params or {}

    @abstractmethod
    def calculate(self, df: pd.DataFrame) -> pd.Series:
        """
        计算因子值

        Args:
            df: 包含 OHLCV 的 DataFrame（一只股票的历史数据）

        Returns:
            pd.Series: 以日期为索引的因子值序列
        """
        pass

    @property
    def description(self) -> str:
        """因子描述"""
        return ""
```

## 5. 具体因子

### 5.1 动量因子 (`momentum.py`)

| 因子名 | 计算方式 | 参数 |
|--------|---------|------|
| momentum_1m | 过去 20 个交易日的累计收益率 | period=20 |
| momentum_3m | 过去 60 个交易日的累计收益率 | period=60 |
| momentum_6m | 过去 120 个交易日的累计收益率 | period=120 |
| momentum_12m | 过去 252 个交易日的累计收益率 | period=252 |

### 5.2 价值因子 (`value.py`)

| 因子名 | 计算方式 | 说明 |
|--------|---------|------|
| ep_ratio | 1 / PE（市盈率倒数） | 需要 PE 数据 |
| bp_ratio | 1 / PB（市净率倒数） | 需要 PB 数据 |
| dividend_yield | 股息率 | 需要股息数据 |

> 注：如果没有基本面数据，暂用价格代理（如反转因子 = -momentum）

### 5.3 波动率因子 (`volatility.py`)

| 因子名 | 计算方式 |
|--------|---------|
| realized_vol_20d | 过去 20 天日收益率的标准差 |
| realized_vol_60d | 过去 60 天日收益率的标准差 |
| vol_ratio | 短期波动 / 长期波动 |

### 5.4 技术因子 (`technical.py`)

| 因子名 | 计算方式 | 说明 |
|--------|---------|------|
| rsi_14 | RSI(14) 指标值 | 复用 AnalysisEngine |
| macd_hist | MACD 柱状值 | 复用 AnalysisEngine |
| distance_ma20 | (close - MA20) / MA20 | 距均线偏离度 |

### 5.5 成长因子 (`growth.py`)

| 因子名 | 计算方式 | 说明 |
|--------|---------|------|
| revenue_growth | 营收同比增速 | 需要财务数据，暂用代理 |
| price_acceleration | 动量的变化率 | 二阶动量 |

## 6. 分析模块

### 6.1 IC 计算器 (`ic_calculator.py`)

```
对于每一个交易日 t:
  1. 收集所有股票在 t 日的因子值
  2. 收集所有股票在 t+N 日的收益率
  3. 计算 Spearman 秩相关 = IC(t)

输出: IC 时间序列 [IC(1), IC(2), ..., IC(T)]
```

### 6.2 衰减分析器 (`decay_analyzer.py`)

```
分别用 N = 1, 5, 10, 20 天计算 Mean IC:
  1d forward:  mean IC = 0.08
  5d forward:  mean IC = 0.05
  10d forward: mean IC = 0.03
  20d forward: mean IC = 0.01

输出: { "1d": 0.08, "5d": 0.05, "10d": 0.03, "20d": 0.01 }
```

### 6.3 组合器 (`combiner.py`)

| 方式 | 说明 |
|------|------|
| 等权 | 每个因子权重一样 |
| IC 加权 | IC 高的因子权重大 |

### 6.4 中性化 (`neutralizer.py`)

去除行业和市值对因子的影响，让因子更"纯粹"：
```
1. 去极值: 超过 3 倍标准差的截断
2. 标准化: Z-score = (x - mean) / std
3. 行业中性化: 每个行业内部排名（可选）
4. 市值中性化: 回归残差法（可选）
```

## 7. 多空回测

### 7.1 多空组合 (`long_short.py`)

```
每期（如每月）:
  1. 计算所有股票因子值
  2. 排序后分组:
     多头组 = 前 20%（因子值最高）
     空头组 = 后 20%（因子值最低）
  3. 多头等权买入，空头等权卖出
  4. 持有到下个换仓日
  5. 组合收益 = 多头收益 - 空头收益

输出:
  - 多空资金曲线
  - 年化收益、Sharpe、最大回撤
```

### 7.2 分位分析 (`quantile.py`)

```
把股票按因子值分成 5 组 (quintile):
  Q1 = 因子值最低的 20%
  Q2 = 20%-40%
  Q3 = 40%-60%
  Q4 = 60%-80%
  Q5 = 因子值最高的 20%

分别计算每组的平均收益:
  Q1: -2%
  Q2: 0%
  Q3: +1%
  Q4: +3%
  Q5: +5%

如果从 Q1 到 Q5 收益单调递增 → 因子非常有效
```

## 8. 数据库模型

### `FactorValue` — 因子值存储

| 列 | 类型 | 说明 |
|----|------|------|
| id | Integer PK | — |
| factor_name | String | "momentum_20d" |
| symbol | String | "000001.SH" |
| date | Date | — |
| value | Float | 因子值 |

### `FactorAnalysisResult` — 分析结果

| 列 | 类型 | 说明 |
|----|------|------|
| task_id | String PK | 唯一标识 |
| factor_name | String | — |
| symbols_count | Integer | 股票池大小 |
| start_date / end_date | Date | 分析区间 |
| mean_ic | Float | 平均 IC |
| ir | Float | 信息比率 |
| ic_positive_pct | Float | IC > 0 的占比 |
| decay_profile | Text (JSON) | `{"1d": 0.08, ...}` |
| long_short_annual_return | Float | 多空年化收益 |
| long_short_sharpe | Float | 多空 Sharpe |
| long_short_max_drawdown | Float | 多空最大回撤 |
| ic_series | Text (JSON) | IC 时间序列 |
| equity_curve | Text (JSON) | 多空资金曲线 |
| created_at | DateTime | — |

## 9. API 端点

| Method | Path | 说明 |
|--------|------|------|
| GET | `/factors/list` | 可用因子列表 |
| POST | `/factors/analyze` | 运行因子分析（NDJSON 流式） |
| GET | `/factors/results` | 历史分析结果 |
| GET | `/factors/results/{task_id}` | 详情 |
| DELETE | `/factors/results/{task_id}` | 删除 |
| POST | `/factors/combine` | 多因子组合 |

### 流式进度事件

```jsonl
{"event": "start", "factor": "momentum_20d", "symbols_count": 100}
{"event": "progress", "step": "fetching_data", "current": 30, "total": 100}
{"event": "progress", "step": "calculating_factor", "current": 80, "total": 100}
{"event": "progress", "step": "computing_ic", "message": "计算 IC 时间序列..."}
{"event": "progress", "step": "running_backtest", "message": "多空回测中..."}
{"event": "done", "task_id": "xxx", "summary": { "mean_ic": 0.05, "ir": 0.8 }}
```

## 10. 前端页面 (`FactorResearch.tsx`)

```
┌────────────────────────────────────────────────────────┐
│                    因子研究                               │
├────────────────────────────────────────────────────────┤
│  因子: [动量 20 日 ▼]    参数: period=[20]              │
│  股票池: [全 A 股 ▼] 或 手动输入                         │
│  分析区间: [2023-01-01] ~ [2025-01-01]                  │
│  [开始分析]                                              │
├────────────────────────────────────────────────────────┤
│  进度: 获取数据 30/100... ████████░░                     │
├─────────┬─────────┬─────────┬─────────────────────────┤
│ IC 序列  │ IC 衰减  │ 多空曲线 │ 统计概要               │
├─────────┴─────────┴─────────┴─────────────────────────┤
│ (当前 Tab 的图表内容)                                    │
│                                                         │
│  IC 时间序列柱状图:                                      │
│  ▓  ▓     ▓  ▓                                         │
│  ▓  ▓  ▓  ▓  ▓     ▓                                  │
│  ─────────────────────── 0 线                           │
│        ▓        ▓  ▓                                    │
│                                                         │
│  Mean IC: 0.05  |  IR: 0.82  |  IC>0: 68%             │
│  年化收益: 15.3%  |  Sharpe: 1.2  |  最大回撤: -8.5%   │
└────────────────────────────────────────────────────────┘
```

## 11. 实施步骤

| Step | 内容 | 产出文件 |
|------|------|---------|
| 4.1 | BaseFactor 基类 | `factors/base.py` |
| 4.2 | 动量因子 | `factors/momentum.py` |
| 4.3 | 价值 + 波动率 + 技术 + 成长因子 | `factors/value.py` 等 |
| 4.4 | IC 计算器 | `analysis/ic_calculator.py` |
| 4.5 | 衰减分析器 | `analysis/decay_analyzer.py` |
| 4.6 | 组合器 + 中性化 | `analysis/combiner.py`, `neutralizer.py` |
| 4.7 | 多空回测 + 分位分析 | `backtest/long_short.py`, `quantile.py` |
| 4.8 | FactorEngine 编排器 | `engine.py` |
| 4.9 | 数据库模型 | 修改 `models.py` |
| 4.10 | API 路由（流式） | `api/routes/factor.py` |
| 4.11 | 前端 FactorResearch.tsx | `frontend/src/pages/FactorResearch.tsx` |

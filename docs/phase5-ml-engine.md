# Phase 5：ML 预测模型 (Python)

> 展示能力：特征工程、LightGBM、Walk-Forward 验证、模型评估

## 1. 概述

用机器学习预测股票未来涨跌。核心流程：

```
历史数据 → 特征工程 → 标签生成 → 模型训练 → Walk-Forward 验证 → 生成预测
```

集成到现有 Fin 项目的 Python 后端，复用 `DataEngine` + `AnalysisEngine`。

## 2. 目录结构

```
backend/ml_engine/
├── __init__.py
├── engine.py                        # MLEngine 编排器
├── features/
│   ├── __init__.py
│   ├── base.py                      # BaseFeatureBuilder 基类
│   ├── technical.py                 # 技术指标特征
│   ├── returns.py                   # 收益率特征
│   ├── volume.py                    # 成交量特征
│   └── builder.py                   # 特征组合器
├── labels/
│   ├── __init__.py
│   └── generator.py                 # 标签生成
├── models/
│   ├── __init__.py
│   ├── base.py                      # BaseMLModel 基类
│   ├── lightgbm_model.py            # LightGBM 封装
│   └── garch_model.py               # GARCH(1,1) 波动率模型
├── training/
│   ├── __init__.py
│   ├── trainer.py                   # 训练编排器
│   ├── walk_forward.py              # 滚动窗口验证
│   └── evaluator.py                 # 模型评估
└── prediction/
    ├── __init__.py
    └── predictor.py                 # 预测器
```

## 3. 核心概念

### 3.1 特征工程 — 把原始数据变成模型能用的"信号"

```
原始数据 (OHLCV):
  日期        开盘   最高   最低   收盘   成交量
  2025-01-10  100   105   98    103   1000000

变成特征:
  rsi_14          = 55.3        (RSI 指标)
  macd_hist       = 0.5         (MACD 柱)
  return_1d       = 0.03        (昨日涨了 3%)
  return_5d       = 0.08        (过去 5 天涨了 8%)
  volatility_20d  = 0.02        (20 天波动率 2%)
  volume_ratio    = 1.5         (今天量是 20 天均量的 1.5 倍)
  ...

→ 每天每只股票 = 一行特征向量（20-50 个特征）
```

### 3.2 标签 — 我们想预测什么？

**二分类**（涨还是跌）：
```
未来 5 天收益率 > 0  → 标签 = 1 (涨)
未来 5 天收益率 ≤ 0  → 标签 = 0 (跌)
```

**回归**（涨多少）：
```
标签 = 未来 5 天的实际收益率（如 0.03 = 3%）
```

### 3.3 Walk-Forward 验证 — 防止"偷看未来"

普通的 train/test split 有问题：如果随机拆分，可能用 2025 年的数据训练去预测 2024 年的，这在金融里是"作弊"。

Walk-Forward（滚动窗口）解决这个问题：

```
时间轴 →

Fold 1:
  训练: [────── 252 天 ──────]
  测试:                        [── 21 天 ──]

Fold 2:
  训练: [──────── 252+21 天 ────────]
  测试:                                [── 21 天 ──]

Fold 3:
  训练: [────────── 252+42 天 ──────────]
  测试:                                        [── 21 天 ──]

...

特点:
  - 训练集只用过去数据，永远不偷看未来
  - 训练窗口逐步扩大（expanding window）
  - 每 fold 输出一组预测 + 评估指标
```

### 3.4 为什么用 LightGBM？

LightGBM 是微软出的梯度提升树（GBDT）库，在量化界非常流行：
- 处理表格数据效果好
- 训练速度快
- 自带特征重要性
- 能处理缺失值
- 不需要特征标准化

### 3.5 GARCH 模型 — 作为对比

GARCH(1,1) 是传统金融里最常用的波动率模型：
```
σ²(t) = ω + α × r²(t-1) + β × σ²(t-1)

其中:
  σ²(t) = 今天的波动率预测
  r(t-1) = 昨天的收益率
  σ²(t-1) = 昨天的波动率
```

用途：对比 ML 模型和传统模型的表现，展示 ML 的优势和局限。

## 4. 特征集

### 4.1 技术指标特征 (`technical.py`)

复用 `AnalysisEngine` 已经计算好的指标：

| 特征 | 来源 | 说明 |
|------|------|------|
| rsi_14 | MomentumIndicators | RSI 相对强弱指标 |
| macd_hist | TrendIndicators | MACD 柱状值 |
| macd_signal | TrendIndicators | MACD 信号线 |
| bb_width | VolatilityIndicators | 布林带宽度 |
| bb_position | VolatilityIndicators | 价格在布林带中的位置 |
| adx | TrendIndicators | 趋势强度 |
| cci | MomentumIndicators | 商品通道指标 |
| obv_change | VolumeIndicators | OBV 变化率 |

### 4.2 收益率特征 (`returns.py`)

| 特征 | 计算 |
|------|------|
| return_1d | 昨日收益率 |
| return_5d | 过去 5 天累计收益率 |
| return_10d | 过去 10 天累计收益率 |
| return_20d | 过去 20 天累计收益率 |
| volatility_5d | 过去 5 天日收益标准差 |
| volatility_20d | 过去 20 天日收益标准差 |
| max_drawdown_20d | 过去 20 天最大回撤 |

### 4.3 成交量特征 (`volume.py`)

| 特征 | 计算 |
|------|------|
| volume_ratio_5d | 今日成交量 / 5 日均量 |
| volume_ratio_20d | 今日成交量 / 20 日均量 |
| volume_trend | 5 日均量 / 20 日均量 |
| price_volume_corr | 过去 20 天价格和量的相关性 |

## 5. 模型定义

### 5.1 BaseMLModel 基类

```python
class BaseMLModel(ABC):
    @abstractmethod
    def train(self, X_train, y_train, X_val=None, y_val=None):
        """训练模型"""
        pass

    @abstractmethod
    def predict(self, X) -> np.ndarray:
        """预测"""
        pass

    @abstractmethod
    def predict_proba(self, X) -> np.ndarray:
        """预测概率（分类模型）"""
        pass

    @abstractmethod
    def save(self, path: str):
        """保存模型到文件"""
        pass

    @classmethod
    @abstractmethod
    def load(cls, path: str):
        """从文件加载模型"""
        pass

    @property
    def feature_importances(self) -> Dict[str, float]:
        """特征重要性"""
        pass
```

### 5.2 LightGBM 模型 (`lightgbm_model.py`)

```python
# 二分类参数
params_clf = {
    "objective": "binary",       # 二分类
    "metric": "auc",             # 评估用 AUC
    "num_leaves": 31,            # 叶子节点数
    "learning_rate": 0.05,       # 学习率
    "feature_fraction": 0.8,     # 每棵树随机用 80% 的特征
    "bagging_fraction": 0.8,     # 每棵树随机用 80% 的数据
    "verbose": -1,
}

# 回归参数
params_reg = {
    "objective": "regression",
    "metric": "mse",
    ...
}
```

### 5.3 GARCH 模型 (`garch_model.py`)

```python
from arch import arch_model

# 拟合 GARCH(1,1)
model = arch_model(returns, vol='Garch', p=1, q=1, dist='normal')
result = model.fit(disp='off')
forecast = result.forecast(horizon=5)
# forecast.variance → 未来 5 天的波动率预测
```

## 6. 训练流程

### 6.1 Walk-Forward 验证 (`walk_forward.py`)

```python
class WalkForwardValidator:
    def __init__(self,
                 train_days: int = 252,     # 初始训练窗口
                 test_days: int = 21,       # 每折测试长度
                 step_days: int = 21):      # 步进天数

    def split(self, dates) -> List[Tuple[train_idx, test_idx]]:
        """生成 train/test 索引对"""
        folds = []
        train_end = train_days
        while train_end + test_days <= len(dates):
            train_idx = range(0, train_end)
            test_idx = range(train_end, train_end + test_days)
            folds.append((train_idx, test_idx))
            train_end += step_days
        return folds
```

### 6.2 评估器 (`evaluator.py`)

| 指标 | 说明 | 适用 |
|------|------|------|
| Accuracy | 正确预测的比例 | 分类 |
| Precision | 预测为涨的里面，真涨的比例 | 分类 |
| Recall | 真正涨的里面，被预测到的比例 | 分类 |
| F1 Score | Precision 和 Recall 的调和平均 | 分类 |
| AUC-ROC | ROC 曲线下面积（0.5=随机，1.0=完美）| 分类 |
| 混淆矩阵 | 2×2 表格显示 TP/FP/TN/FN | 分类 |
| MSE | 均方误差 | 回归 |
| Sharpe (策略) | 按预测信号交易的 Sharpe 比率 | 两者 |

### 6.3 训练编排器 (`trainer.py`)

```
完整训练流程:

1. 获取数据 (DataEngine)
2. 计算技术指标 (AnalysisEngine)
3. 构建特征矩阵 (FeatureBuilder)
4. 生成标签 (LabelGenerator)
5. Walk-Forward 循环:
   for each fold:
     a. 拆分 train/test
     b. 训练 LightGBM
     c. 在 test 上预测
     d. 评估指标
     e. yield 进度事件
6. 汇总所有 fold 的指标
7. 用全部数据训练最终模型 → 保存
8. 返回完整结果
```

## 7. 数据库模型

### `MLModel` — 模型记录

| 列 | 类型 | 说明 |
|----|------|------|
| model_id | String PK | 唯一标识 |
| symbol | String | 训练的股票 |
| model_type | String | "lightgbm_clf" / "lightgbm_reg" / "garch" |
| features | Text (JSON) | 使用的特征列表 |
| forward_days | Integer | 前瞻天数 |
| train_start / train_end | Date | 训练数据区间 |
| metrics | Text (JSON) | `{ accuracy, auc, sharpe, ... }` |
| fold_metrics | Text (JSON) | 每折指标列表 |
| confusion_matrix | Text (JSON) | `[[TP, FP], [FN, TN]]` |
| feature_importances | Text (JSON) | `{ "rsi_14": 0.15, ... }` |
| equity_curve | Text (JSON) | Walk-Forward 资金曲线 |
| model_path | String | 模型文件保存路径 |
| created_at | DateTime | — |

### `MLPrediction` — 预测记录

| 列 | 类型 | 说明 |
|----|------|------|
| id | Integer PK | — |
| model_id | String FK | 用哪个模型预测的 |
| symbol | String | — |
| date | Date | 预测日期 |
| prediction | Float | 预测值（概率或收益率） |
| direction | String | "UP" / "DOWN" |
| confidence | Float | 置信度 |
| actual_return | Float | 实际收益率（回填） |
| created_at | DateTime | — |

## 8. API 端点

| Method | Path | 说明 |
|--------|------|------|
| GET | `/ml/features` | 可用特征集列表 |
| POST | `/ml/train` | 训练模型（NDJSON 流式，按 fold 推送） |
| GET | `/ml/models` | 已训练模型列表 |
| GET | `/ml/models/{model_id}` | 模型详情（指标 + 特征重要性 + 混淆矩阵） |
| DELETE | `/ml/models/{model_id}` | 删除模型 |
| POST | `/ml/predict` | 用已训练模型做预测 |
| GET | `/ml/predictions` | 预测记录列表 |

### 流式训练进度

```jsonl
{"event": "start", "symbol": "000001.SH", "total_folds": 12}
{"event": "fold", "fold": 1, "total": 12, "train_size": 252, "test_size": 21}
{"event": "fold_result", "fold": 1, "accuracy": 0.57, "auc": 0.61}
{"event": "fold", "fold": 2, "total": 12, "train_size": 273, "test_size": 21}
{"event": "fold_result", "fold": 2, "accuracy": 0.55, "auc": 0.59}
...
{"event": "training_final", "message": "用全量数据训练最终模型..."}
{"event": "done", "model_id": "xxx", "summary": { "avg_accuracy": 0.56, "avg_auc": 0.60 }}
```

## 9. 前端页面 (`MLPrediction.tsx`)

```
┌────────────────────────────────────────────────────────┐
│                   ML 预测模型                            │
├────────────────────────────────────────────────────────┤
│  股票: [000001.SH]    模型: [LightGBM 分类 ▼]          │
│  前瞻天数: [5]        特征: ☑ RSI ☑ MACD ☑ 收益率 ...  │
│  训练窗口: [252] 天   测试窗口: [21] 天                  │
│  [开始训练]                                              │
├────────────────────────────────────────────────────────┤
│  训练进度: Fold 5/12  ████████░░░░                      │
│  Fold 5: Accuracy=0.57  AUC=0.61                       │
├─────────┬──────────┬──────────┬────────────────────────┤
│ 特征重要性│ WF 准确率 │ 混淆矩阵  │ 资金曲线              │
├─────────┴──────────┴──────────┴────────────────────────┤
│                                                         │
│  特征重要性 (水平柱状图):                                 │
│  rsi_14          ████████████████  0.15                 │
│  return_5d       ██████████████    0.13                 │
│  volume_ratio    ████████████      0.11                 │
│  macd_hist       ██████████        0.09                 │
│  volatility_20d  ████████          0.07                 │
│  ...                                                    │
│                                                         │
│  混淆矩阵:        预测涨    预测跌                        │
│  实际涨           120(TP)   80(FN)                      │
│  实际跌            70(FP)  110(TN)                      │
│                                                         │
├────────────────────────────────────────────────────────┤
│  已训练模型列表:                                         │
│  模型 ID    股票      准确率   AUC    Sharpe   日期      │
│  abc123   000001.SH   56%    0.61    0.8    2025-02-20 │
│  def456   AAPL        54%    0.58    0.5    2025-02-18 │
└────────────────────────────────────────────────────────┘
```

## 10. 实施步骤

| Step | 内容 | 产出文件 |
|------|------|---------|
| 5.1 | BaseFeatureBuilder 基类 | `features/base.py` |
| 5.2 | 技术 + 收益率 + 成交量特征 | `features/technical.py` 等 |
| 5.3 | FeatureBuilder 组合器 | `features/builder.py` |
| 5.4 | 标签生成器 | `labels/generator.py` |
| 5.5 | BaseMLModel 基类 | `models/base.py` |
| 5.6 | LightGBM 模型封装 | `models/lightgbm_model.py` |
| 5.7 | GARCH 模型封装 | `models/garch_model.py` |
| 5.8 | Walk-Forward 验证 | `training/walk_forward.py` |
| 5.9 | 评估器 | `training/evaluator.py` |
| 5.10 | 训练编排器 | `training/trainer.py` |
| 5.11 | 预测器 | `prediction/predictor.py` |
| 5.12 | MLEngine 编排器 | `engine.py` |
| 5.13 | 数据库模型 | 修改 `models.py` |
| 5.14 | API 路由（流式） | `api/routes/ml.py` |
| 5.15 | 前端 MLPrediction.tsx | `frontend/src/pages/MLPrediction.tsx` |

## 11. 重要提醒

### 模型的局限性（必须在文档和 UI 中展示）
- 金融数据信噪比极低，56% 的准确率在业界已经不错
- 过拟合风险高，Walk-Forward 验证至关重要
- 特征重要性不代表因果关系
- 历史表现不代表未来收益
- GARCH 对比展示的是"ML 不总是更好"

### 面试讨论点
- 为什么用 Walk-Forward 而不是 K-Fold？→ 时序数据不能随机切割
- 为什么用 LightGBM 而不是深度学习？→ 表格数据 + 数据量有限
- 如何避免过拟合？→ 正则化 + 特征筛选 + Walk-Forward
- 模型部署考虑？→ 每日自动预测 + 监控模型衰减

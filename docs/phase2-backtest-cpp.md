# Phase 2：C++ 回测系统

> 展示能力：OOP 设计、策略模式、绩效计算

## 1. 概述

回测（Backtest）就是拿历史数据"假装"交易，看策略能不能赚钱。现有项目已有 Python 版本，这个 C++ 版本重点展示：
- **面向对象设计**：抽象基类 + 策略模式
- **性能**：C++ 逐 bar 循环比 Python 快得多
- **工程能力**：完整的构建、测试流程

独立 C++ 项目，运行在 `:8002` 端口。

## 2. 目录结构

```
backtest_cpp/
├── CMakeLists.txt
├── include/backtest/
│   ├── types.h                     # Bar, Order, Fill, Position 等基础类型
│   ├── portfolio.h                 # 仓位管理 + 资金追踪
│   ├── strategy_base.h             # IStrategy 抽象基类（纯虚函数）
│   ├── strategy_context.h          # 传递给策略的上下文信息
│   ├── engine.h                    # 回测引擎核心
│   ├── metrics.h                   # 绩效指标计算
│   └── data_loader.h              # 数据加载（CSV / JSON）
├── src/
│   ├── portfolio.cpp
│   ├── engine.cpp
│   ├── metrics.cpp
│   ├── data_loader.cpp
│   ├── strategies/
│   │   ├── ma_cross_strategy.h     # 均线交叉策略
│   │   ├── ma_cross_strategy.cpp
│   │   ├── momentum_strategy.h     # 动量策略
│   │   └── momentum_strategy.cpp
│   ├── server.cpp                  # REST API
│   └── main.cpp
├── tests/
│   ├── test_portfolio.cpp
│   ├── test_metrics.cpp
│   ├── test_engine.cpp
│   └── test_strategies.cpp
├── data/                           # 测试用 CSV 数据
│   └── sample.csv
└── README.md
```

## 3. 核心类设计

### 3.1 基础类型 (`types.h`)

```cpp
// 一根 K 线（一天的数据）
struct Bar {
    std::string date;       // "2025-01-15"
    double open;            // 开盘价
    double high;            // 最高价
    double low;             // 最低价
    double close;           // 收盘价
    double volume;          // 成交量
};

// 订单
struct Order {
    std::string order_id;
    std::string symbol;
    Side side;              // BUY / SELL
    int quantity;
    double price;           // 0 = 市价
    double stop_loss;       // 止损价
};

// 成交
struct Fill {
    std::string order_id;
    double price;           // 实际成交价
    int quantity;
    double commission;      // 手续费
    double slippage;        // 滑点
};

// 持仓
struct Position {
    std::string symbol;
    int quantity;           // 持有数量
    double avg_cost;        // 平均成本
    double unrealized_pnl;  // 未实现盈亏
};
```

### 3.2 策略基类 (`IStrategy`) — OOP 重点

```cpp
// IStrategy 是一个"接口"（纯虚函数基类）
// 所有策略都必须继承它并实现 on_bar 方法
// 这就是"策略模式"设计模式的体现

class IStrategy {
public:
    virtual ~IStrategy() = default;                         // 虚析构函数
    virtual std::string name() const = 0;                   // 策略名称
    virtual void on_init() {}                               // 回测开始前调用
    virtual void on_finish() {}                             // 回测结束后调用
    virtual std::vector<Order> on_bar(
        const StrategyContext& ctx                          // 当前状态
    ) = 0;                                                  // = 0 表示纯虚函数，子类必须实现
};
```

```cpp
// 传给策略的上下文，包含策略做决策需要的所有信息
struct StrategyContext {
    std::string symbol;
    Bar current_bar;                    // 当前 K 线
    std::vector<Bar> history;           // 历史 K 线（含当前）
    double cash;                        // 可用资金
    int position_quantity;              // 当前持仓量
    double position_avg_price;          // 持仓均价
    double total_value;                 // 总资产 = cash + 持仓市值
    int bar_index;                      // 当前是第几根 K 线
};
```

### 3.3 内置策略示例

**均线交叉策略 (`MACrossStrategy`)**：
```
短期均线（如 5 日）上穿长期均线（如 20 日） → 买入
短期均线下穿长期均线 → 卖出
```

**动量策略 (`MomentumStrategy`)**：
```
过去 N 天涨幅 > 阈值 → 买入
过去 N 天跌幅 > 阈值 → 卖出
```

### 3.4 仓位管理 (`Portfolio`)

```cpp
class Portfolio {
    double initial_capital;     // 初始资金
    double cash;                // 当前现金
    std::map<std::string, Position> positions;  // 持仓表
    std::vector<Fill> fill_history;             // 成交历史
    std::vector<double> equity_curve;           // 每日净值曲线

    // 手续费率（不同市场不同）
    struct CommissionConfig {
        double rate;            // 佣金率（如 0.00025 = 万 2.5）
        double stamp_tax;      // 印花税率（如 0.001 = 千 1）
        double min_commission;  // 最低佣金（如 5 元）
    };
};
```

**手续费计算**：

| 市场 | 佣金 | 印花税 | 说明 |
|------|------|--------|------|
| A 股 | 万 2.5 | 千 1（仅卖出）| 最低 5 元 |
| 港股 | 万 5 | 千 1 | — |
| 美股 | $0.005/股 | — | 最低 $1 |

### 3.5 回测引擎 (`BacktestEngine`)

```
核心循环（伪代码）:

engine.load_data("AAPL", bars)
engine.set_strategy(make_unique<MACrossStrategy>(5, 20))
engine.run()

run() 内部:
  strategy->on_init()
  for each bar in data:
      ctx = build_context(bar, history, portfolio)
      orders = strategy->on_bar(ctx)         // 策略生成订单
      for each order in orders:
          fill = execute_order(order, bar)    // 模拟撮合
          portfolio.update(fill)              // 更新持仓
      portfolio.record_equity(bar.close)      // 记录净值
  strategy->on_finish()
  return calculate_metrics()
```

### 3.6 绩效指标 (`Metrics`)

| 指标 | 公式 | 说明 |
|------|------|------|
| 总收益率 | (最终净值 - 初始资金) / 初始资金 | — |
| 年化收益率 | (1 + 总收益率)^(252/交易天数) - 1 | 252 = 年交易日 |
| 最大回撤 | max(peak - trough) / peak | 从峰值到谷值的最大跌幅 |
| Sharpe 比率 | mean(daily_return) / std(daily_return) × √252 | 风险调整收益 |
| Sortino 比率 | mean(daily_return) / std(负收益) × √252 | 只惩罚下行风险 |
| 胜率 | 盈利交易次数 / 总交易次数 | — |
| 盈亏比 | 平均盈利 / 平均亏损 | — |

## 4. REST API

基础 URL: `http://localhost:8002`

| Method | Path | 请求体 | 返回 |
|--------|------|--------|------|
| GET | `/api/strategies` | — | `[{ name, params_schema }]` |
| POST | `/api/backtest/run` | `{ symbol, strategy, params, start_date, end_date, initial_capital, market }` | `{ task_id, metrics, equity_curve, trades }` |
| GET | `/api/backtest/results/{id}` | — | 完整结果 |
| POST | `/api/backtest/data` | `{ symbol, bars: [...] }` | 上传 CSV/JSON 数据供回测使用 |

数据来源：前端/FastAPI 先从 DataEngine 拿到数据，POST 到 C++ 服务再运行回测。

## 5. 前端集成

可以在现有 Backtest.tsx 上加一个 Tab 切换 **"Python 回测"** / **"C++ 回测"**，两者共享输入表单，结果展示格式一致。

## 6. 实施步骤

| Step | 内容 | 产出文件 |
|------|------|---------|
| 2.1 | 项目脚手架 + CMake | `CMakeLists.txt` |
| 2.2 | 基础类型 | `types.h` |
| 2.3 | Portfolio 仓位管理 | `portfolio.h/.cpp` |
| 2.4 | IStrategy 抽象基类 + StrategyContext | `strategy_base.h`, `strategy_context.h` |
| 2.5 | MACrossStrategy 均线策略 | `ma_cross_strategy.h/.cpp` |
| 2.6 | MomentumStrategy 动量策略 | `momentum_strategy.h/.cpp` |
| 2.7 | BacktestEngine 核心循环 | `engine.h/.cpp` |
| 2.8 | Metrics 绩效指标 | `metrics.h/.cpp` |
| 2.9 | DataLoader 数据加载 | `data_loader.h/.cpp` |
| 2.10 | REST API 服务器 | `server.cpp`, `main.cpp` |
| 2.11 | 单元测试 | `tests/*.cpp` |
| 2.12 | Python 代理路由 | `backend/api/routes/backtest_cpp.py` |

## 7. C++ 知识点索引

| 知识点 | 出现位置 | 简单说明 |
|--------|---------|---------|
| 纯虚函数 `= 0` | strategy_base.h | 子类必须实现的接口方法 |
| 虚析构函数 `virtual ~` | strategy_base.h | 通过基类指针删除子类时不会内存泄漏 |
| `std::unique_ptr` | engine.h | 独占所有权的智能指针 |
| `std::make_unique<>()` | engine.cpp | 创建 unique_ptr 的安全方式 |
| `std::vector` | 到处 | 动态数组，C++ 最常用的容器 |
| `std::accumulate` | metrics.cpp | 求和/累积运算 |
| `const` 方法 | 多处 | 承诺不修改对象状态 |
| 模板 `<T>` | metrics.h | 泛型编程（如通用求平均） |
| `override` 关键字 | 策略实现 | 明确表示重写父类方法，编译器帮你检查 |
| 移动语义 `std::move` | engine.cpp | 转移对象所有权，避免拷贝开销 |

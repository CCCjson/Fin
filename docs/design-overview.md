# 量化交易系统 — 五大新模块设计文档

> 最后更新: 2026-02-25

## 1. 项目背景

现有 Fin 项目已覆盖：数据引擎、分析引擎、回测引擎、模拟交易、AI投顾、每日复盘等功能（全部 Python + React）。

为展示 **系统设计(C++) + 量化研究(Python) + 机器学习(Python)** 三方面能力，新增五个模块：

| # | 模块 | 语言 | 展示能力 | 运行形态 |
|---|------|------|---------|---------|
| 1 | 订单簿模拟器 | C++ | 数据结构、撮合算法、市场微观结构 | 独立进程 :8001 |
| 2 | 回测系统 | C++ | OOP 设计、策略模式、绩效计算 | 独立进程 :8002 |
| 3 | 市场数据管道 | C++/Python | 多线程、网络编程、生产者-消费者 | 独立进程 :8003 |
| 4 | 因子研究 | Python | IC/IR 分析、因子组合、多空回测 | 集成到 FastAPI :8000 |
| 5 | ML预测模型 | Python | 特征工程、LightGBM、Walk-Forward | 集成到 FastAPI :8000 |

## 2. 整体架构

```
┌─────────────────────────────────────────────────┐
│                 React 前端 (:5173)               │
│  OrderBook / Backtest / Pipeline / Factor / ML   │
└────────────────────┬────────────────────────────┘
                     │ HTTP / WebSocket
                     ▼
┌─────────────────────────────────────────────────┐
│            Python FastAPI (:8000)                 │
│  ┌──────────┐ ┌──────────┐ ┌──────────────────┐ │
│  │ factor   │ │ ml       │ │ proxy routes     │ │
│  │ _engine  │ │ _engine  │ │ (orderbook/      │ │
│  │ (Python) │ │ (Python) │ │  backtest/       │ │
│  │          │ │          │ │  pipeline)       │ │
│  └──────────┘ └──────────┘ └───┬──┬──┬────────┘ │
└────────────────────────────────┼──┼──┼───────────┘
           HTTP 转发              │  │  │
       ┌─────────────────────────┘  │  └──────────────┐
       ▼                            ▼                  ▼
┌──────────────┐  ┌──────────────┐  ┌──────────────────┐
│ C++ 订单簿    │  │ C++ 回测     │  │ C++ 数据管道      │
│ :8001        │  │ :8002        │  │ :8003             │
│ cpp-httplib  │  │ cpp-httplib  │  │ cpp-httplib       │
└──────────────┘  └──────────────┘  └──────────────────┘
```

**前端只和 FastAPI(:8000) 通信**，FastAPI 中的 proxy routes 用 `httpx` 把请求转发给 C++ 服务。

## 3. C++ 项目公共约定

### 3.1 构建系统
- CMake 3.16+，C++17 标准
- 第三方库通过 `FetchContent` 自动下载（nlohmann/json、cpp-httplib、GoogleTest）

### 3.2 代码规范 & 注释要求

**面向 C++ 初学者的注释风格** — 把读者当作刚开始学 C++ 的人：

- **每个文件顶部**：用中文简述这个文件做什么、核心思路
- **每个类/结构体**：解释它的作用、为什么这样设计
- **关键字解释**：第一次出现 `const&`、`virtual`、`std::move`、`unique_ptr`、`template` 等概念时，用注释解释这个关键字的含义
- **数据结构选择**：为什么用 `std::map` 而不是 `std::unordered_map`？为什么用 `deque` 不用 `vector`？
- **内存管理**：哪里用了智能指针、为什么不用 raw pointer
- **多线程**：锁的范围、为什么这里需要加锁、什么是 RAII 锁
- **不写废话注释**：`int x = 1; // 把 x 设为 1` 这种不写，只写"为什么"而不是"是什么"

示例：
```cpp
// std::map 是红黑树实现的有序字典
// 这里用它而不是 unordered_map，是因为我们需要按价格排序：
// 买盘要从高到低排（出价最高的排前面）
// std::greater<double> 让 map 从大到小排序（默认是从小到大）
std::map<double, PriceLevel, std::greater<double>> bids_;
```

- 命名：类用 PascalCase，函数/变量用 snake_case，常量用 UPPER_CASE

### 3.3 目录结构模板
```
project_name/
├── CMakeLists.txt          # 构建配置
├── include/project_name/   # 头文件（.h）— 声明接口
├── src/                    # 源文件（.cpp）— 实现逻辑
├── tests/                  # 单元测试
├── third_party/            # 第三方库（自动拉取）
└── README.md
```

---

## 4. 详细设计文档索引

每个模块有独立的详细设计文档：

| 文档 | 内容 |
|------|------|
| [phase1-orderbook.md](phase1-orderbook.md) | 订单簿模拟器 (C++) |
| [phase2-backtest-cpp.md](phase2-backtest-cpp.md) | C++ 回测系统 |
| [phase3-data-pipeline.md](phase3-data-pipeline.md) | 市场数据管道 (C++/Python) |
| [phase4-factor-engine.md](phase4-factor-engine.md) | 因子研究 (Python) |
| [phase5-ml-engine.md](phase5-ml-engine.md) | ML预测模型 (Python) |
| [phase6-integration.md](phase6-integration.md) | 集成 & 收尾 |

## 5. 实施顺序

```
Phase 1 ──→ Phase 2 ──→ Phase 3 ──→ Phase 4 ──→ Phase 5 ──→ Phase 6
订单簿(C++)  回测(C++)   管道(C++/Py) 因子(Py)    ML(Py)      集成
  最独立      依赖数据     依赖网络     依赖DataEng  依赖Analysis  全部串联
```

Phase 1-3 是 C++ 项目，可以独立编译运行；Phase 4-5 是 Python 模块，集成到现有后端；Phase 6 把一切串起来。

# Phase 1：订单簿模拟器 (C++)

> 展示能力：数据结构、撮合算法、市场微观结构

## 1. 概述

订单簿（Order Book）是证券交易所的核心组件。它维护所有未成交的买单和卖单，按照**价格优先、时间优先**的规则进行撮合。

这个模块是一个**独立的 C++ 项目**，自带 REST API 服务器，运行在 `:8001` 端口。

## 2. 目录结构

```
orderbook_simulator/
├── CMakeLists.txt                        # 构建配置
├── include/orderbook/                    # 头文件
│   ├── types.h                           # 基础类型定义
│   ├── price_level.h                     # 单价位队列
│   ├── limit_order_book.h                # 限价订单簿
│   ├── matching_engine.h                 # 撮合引擎
│   ├── market_impact.h                   # 市场冲击模型
│   ├── statistics.h                      # 盘口统计
│   ├── session.h                         # 会话管理
│   └── session_manager.h                 # 多会话管理
├── src/
│   ├── price_level.cpp
│   ├── limit_order_book.cpp
│   ├── matching_engine.cpp
│   ├── market_impact.cpp
│   ├── statistics.cpp
│   ├── session.cpp
│   ├── session_manager.cpp
│   ├── server.cpp                        # REST API 服务器
│   └── main.cpp                          # 入口
├── tests/
│   ├── test_price_level.cpp
│   ├── test_order_book.cpp
│   ├── test_matching.cpp
│   └── test_session.cpp
└── README.md
```

## 3. 核心数据结构

### 3.1 基础类型 (`types.h`)

```cpp
// === 枚举 ===

// 买卖方向
enum class Side { BUY, SELL };

// 订单类型
//   MARKET — 市价单，不限价格，无条件吃掉对手盘
//   LIMIT  — 限价单，只在指定价格或更优价格成交，否则挂在订单簿上等待
//   IOC    — Immediate or Cancel，能成多少成多少，剩下的立刻取消
//   FOK    — Fill or Kill，要么全部成交，要么一股都不成交
enum class OrderType { MARKET, LIMIT, IOC, FOK };

// === 订单结构体 ===
struct BookOrder {
    std::string order_id;       // 唯一标识
    Side side;                  // 买还是卖
    OrderType order_type;       // 订单类型
    double price;               // 价格（市价单可以为 0）
    int quantity;               // 总数量
    int filled_quantity;        // 已成交数量
    int64_t timestamp;          // 时间戳（纳秒）
    bool is_active;             // 是否还活跃
    std::string client_tag;     // 标签（"user" 或 "seed"）

    int remaining() const;      // 剩余 = quantity - filled_quantity
    bool is_filled() const;     // 是否已全部成交
    void fill(int qty);         // 部分成交
    void cancel();              // 撤单
};

// === 成交记录 ===
struct Fill {
    std::string fill_id;
    std::string buy_order_id;
    std::string sell_order_id;
    double price;               // 成交价
    int quantity;               // 成交量
    Side aggressor_side;        // 主动方（谁发起的这笔交易）
    int64_t timestamp;
};
```

### 3.2 单价位队列 (`PriceLevel`)

**作用**：同一价格上可能有多个订单排队。`PriceLevel` 管理某一个价格上的所有订单，按**先来后到 (FIFO)** 排列。

**数据结构**：`std::deque<BookOrder>`（双端队列，头部弹出 O(1)，尾部插入 O(1)）

```
价格 = 100.00
┌────────────────────────────────────────┐
│  Order A (100股)  →  Order B (200股)  →  Order C (50股)  │
│  (最早到的)         (第二个)             (最晚到的)        │
└────────────────────────────────────────┘
撮合时从左边（最早的）开始吃
```

**核心方法**：

| 方法 | 说明 | 时间复杂度 |
|------|------|-----------|
| `add_order(order)` | 尾部插入新订单 | O(1) |
| `remove_order(order_id)` | 遍历找到并取消 | O(n) |
| `match(qty, aggressor)` | 从头部开始逐个撮合 | O(k)，k=被吃掉的订单数 |
| `total_quantity()` | 该价位总挂单量 | O(n) |
| `is_empty()` | 是否无活跃订单 | O(1) |

### 3.3 限价订单簿 (`LimitOrderBook`)

**作用**：维护所有价位的买卖盘。买盘从高到低排列（出价最高的优先），卖盘从低到高排列（要价最低的优先）。

**数据结构**：`std::map<double, PriceLevel>`
- 买盘（bids）：用 `std::map<double, PriceLevel, std::greater<double>>`，降序排列
- 卖盘（asks）：用 `std::map<double, PriceLevel>`，默认升序

```
卖盘 (asks) — 从低到高:
  102.00  [Order X: 100股]
  101.50  [Order Y: 200股]
  101.00  [Order Z: 150股]  ← best_ask（最低卖价）
  ─────── 价差 (spread) = 101.00 - 100.50 = 0.50 ───────
  100.50  [Order A: 100股]  ← best_bid（最高买价）
  100.00  [Order B: 200股, Order C: 50股]
   99.50  [Order D: 300股]
买盘 (bids) — 从高到低:
```

**核心方法**：

| 方法 | 说明 |
|------|------|
| `best_bid()` / `best_ask()` | 最优买/卖价 |
| `spread()` | 买卖价差 = best_ask - best_bid |
| `mid_price()` | 中间价 = (best_bid + best_ask) / 2 |
| `add_order(order)` | 插入到对应价位的 PriceLevel |
| `cancel_order(order_id)` | 从所有价位中查找并取消 |
| `get_depth(n)` | 获取前 n 档买卖盘 |

## 4. 撮合引擎 (`MatchingEngine`)

**核心逻辑**：价格-时间优先。

### 4.1 撮合流程

```
新订单进来
    │
    ├── MARKET（市价单）
    │     └── 无条件吃对手盘，吃完为止
    │           买单 → 从 asks 最低价开始吃
    │           卖单 → 从 bids 最高价开始吃
    │
    ├── LIMIT（限价单）
    │     └── 检查对手盘是否有可交叉价格
    │           买单价格 ≥ best_ask → 可以成交
    │           卖单价格 ≤ best_bid → 可以成交
    │           吃完可交叉部分后，剩余挂到订单簿上
    │
    ├── IOC
    │     └── 同 LIMIT 逻辑撮合
    │           但剩余部分不挂单，直接取消
    │
    └── FOK
          └── 先检查对手盘总量是否 ≥ 订单数量
                够 → 全部成交
                不够 → 整单拒绝，一股都不成交
```

### 4.2 撮合示例

当前盘口：
```
asks: 101.00 (150股), 101.50 (200股), 102.00 (100股)
bids: 100.50 (100股), 100.00 (250股)
```

提交：**买入 200 股，LIMIT 101.50**

撮合过程：
1. best_ask = 101.00 ≤ 101.50 → 可交叉
2. 吃掉 101.00 的 150 股 → Fill(101.00, 150)，已成交 150，还剩 50
3. best_ask = 101.50 ≤ 101.50 → 仍可交叉
4. 吃掉 101.50 的 50 股（部分） → Fill(101.50, 50)，已成交 200，完毕

结果：2 笔 Fill，平均成交价 = (101.00×150 + 101.50×50) / 200 = 101.125

## 5. 市场冲击模型

**平方根冲击模型**（业界常用的简化模型）：

```
impact = σ × √(Q / V)

其中:
  σ = 标的日波动率（如 2%）
  Q = 交易数量
  V = 日均成交量
```

用途：在下单前估算大单对市场的冲击成本。

## 6. 盘口统计 (`Statistics`)

| 统计量 | 计算方式 |
|--------|---------|
| 价差 (spread) | best_ask - best_bid |
| 相对价差 | spread / mid_price × 100% |
| 买盘深度 | 前 N 档买盘总量 |
| 卖盘深度 | 前 N 档卖盘总量 |
| 买卖失衡 | (买盘量 - 卖盘量) / (买盘量 + 卖盘量) |
| VWAP | Σ(price × qty) / Σ(qty)，基于成交记录 |
| 总成交量 | 所有 Fill 的 quantity 之和 |
| 总成交笔数 | Fill 的数量 |

## 7. 会话管理

每次"实验"创建一个 **Session**：
- 每个 Session 持有独立的 `LimitOrderBook` + `MatchingEngine`
- `seed_orders(n, mid_price, spread)` — 用随机数在 mid_price 附近生成 n 个限价单，模拟真实盘口
- Session 之间互不影响

`SessionManager` 用 `unordered_map<string, unique_ptr<Session>>` 管理多个会话。

## 8. REST API

基础 URL: `http://localhost:8001`

| Method | Path | 请求体 | 返回 |
|--------|------|--------|------|
| POST | `/api/sessions` | `{ symbol, mid_price, spread, seed_count }` | `{ session_id }` |
| GET | `/api/sessions/{id}/depth?levels=10` | — | `{ bids: [...], asks: [...], spread, mid_price }` |
| POST | `/api/sessions/{id}/orders` | `{ side, order_type, price, quantity }` | `{ order_id, fills: [...], status }` |
| DELETE | `/api/sessions/{id}/orders/{oid}` | — | `{ success }` |
| GET | `/api/sessions/{id}/fills?limit=50` | — | `{ fills: [...] }` |
| GET | `/api/sessions/{id}/stats` | — | `{ spread, depth, vwap, total_volume, ... }` |
| POST | `/api/sessions/{id}/seed` | `{ count, mid_price, spread }` | `{ added_count }` |

## 9. WebSocket

`ws://localhost:8001/ws/orderbook/{session_id}`

每次成交后推送：
```json
{
  "type": "update",
  "depth": { "bids": [...], "asks": [...] },
  "new_fills": [...],
  "stats": { "spread": 0.5, "mid_price": 100.25 }
}
```

## 10. 前端页面 (`OrderBook.tsx`)

```
┌────────────────────────────────────────────────────────────┐
│                    订单簿模拟器                               │
├──────────────┬──────────────┬──────────────────────────────┤
│   买卖盘深度   │  成交价走势   │        下单面板               │
│              │              │  方向: [买入] [卖出]           │
│  ████ 102.00 │      /\      │  类型: [MARKET ▼]            │
│  ██   101.50 │     /  \     │  价格: [_____]               │
│  █    101.00 │    /    \    │  数量: [_____]               │
│  ─── 价差 ───│   /      \   │  [提交订单]                   │
│  ██   100.50 │  /        \  │                              │
│  ████ 100.00 │              │  活跃订单:                    │
│  █     99.50 │              │  #abc 买 100@100.50 [撤单]   │
│              │              │  #def 卖 200@102.00 [撤单]   │
├──────────────┴──────────────┴──────────────────────────────┤
│  成交流水                                                    │
│  时间       价格     数量    主动方                           │
│  12:00:01  101.00   150     买入                            │
│  12:00:01  101.50    50     买入                            │
├────────────────────────────────────────────────────────────┤
│  价差: 0.50 | 买盘深度: 650 | 卖盘深度: 450 | VWAP: 101.08  │
└────────────────────────────────────────────────────────────┘
```

## 11. 实施步骤

| Step | 内容 | 产出文件 |
|------|------|---------|
| 1.1 | 项目脚手架 + CMake + 第三方库 | `CMakeLists.txt` |
| 1.2 | 基础类型定义 | `types.h` |
| 1.3 | PriceLevel 实现 | `price_level.h/.cpp` |
| 1.4 | LimitOrderBook 实现 | `limit_order_book.h/.cpp` |
| 1.5 | MatchingEngine 实现 | `matching_engine.h/.cpp` |
| 1.6 | MarketImpact + Statistics | `market_impact.h/.cpp`, `statistics.h/.cpp` |
| 1.7 | Session + SessionManager | `session.h/.cpp`, `session_manager.h/.cpp` |
| 1.8 | REST API 服务器 | `server.cpp`, `main.cpp` |
| 1.9 | 单元测试 | `tests/*.cpp` |
| 1.10 | Python 代理路由 | `backend/api/routes/orderbook.py` |
| 1.11 | 前端 OrderBook.tsx | `frontend/src/pages/OrderBook.tsx` |

## 12. C++ 知识点索引

这个项目会用到的 C++ 知识点，代码中都会附带注释：

| 知识点 | 出现位置 | 简单说明 |
|--------|---------|---------|
| `enum class` | types.h | 强类型枚举，比普通 enum 更安全 |
| `struct` / 成员函数 | types.h | 带方法的结构体，和 class 几乎一样 |
| `std::deque` | price_level | 双端队列，两头都能快速插入/删除 |
| `std::map` + 自定义比较 | limit_order_book | 红黑树，key 自动排序 |
| `std::greater<>` | limit_order_book | 让 map 从大到小排序（默认从小到大）|
| `std::unique_ptr` | session_manager | 智能指针，自动释放内存 |
| `std::unordered_map` | session_manager | 哈希表，O(1) 查找 |
| `const` 引用传参 | 到处都是 | 避免拷贝，提高性能 |
| `std::optional` | limit_order_book | 表示"可能没有值"（比如空订单簿没有 best_bid）|
| `std::move` | session | 转移所有权，避免不必要的拷贝 |
| `virtual` / 纯虚函数 | 暂无（Phase 2 用）| 多态，实现接口 |
| lambda 表达式 | statistics | 匿名函数，用于排序/过滤 |
| `nlohmann::json` | server | JSON 序列化/反序列化库 |

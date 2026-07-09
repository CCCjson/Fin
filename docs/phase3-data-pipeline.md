# Phase 3：市场数据管道 (C++/Python)

> 展示能力：多线程、网络编程、生产者-消费者模式

## 1. 概述

数据管道（Data Pipeline）负责从外部数据源获取行情数据，经过清洗、转换后存入数据库。现有 Python 版 `DataEngine` 是单线程的，这个 C++/Python 混合版本重点展示：
- **多线程并发**：线程池 + 任务队列
- **生产者-消费者**：数据获取（生产）和数据处理（消费）解耦
- **网络编程**：HTTP 客户端、连接池
- **性能对比**：同样任务 C++ vs Python 的吞吐量

独立 C++ 项目，运行在 `:8003` 端口。Python 部分作为数据源适配器被 C++ 调用。

## 2. 目录结构

```
data_pipeline/
├── CMakeLists.txt
├── include/pipeline/
│   ├── types.h                     # Bar, FetchTask 等基础类型
│   ├── thread_pool.h               # 线程池
│   ├── blocking_queue.h            # 线程安全的阻塞队列
│   ├── http_client.h               # HTTP 客户端（基于 cpp-httplib）
│   ├── parser.h                    # IParser 接口
│   ├── csv_parser.h                # CSV 解析
│   ├── json_parser.h               # JSON 解析
│   ├── storage.h                   # IStorage 接口
│   ├── sqlite_storage.h            # SQLite 存储
│   ├── pipeline.h                  # 管道编排器
│   └── stats.h                     # 吞吐量统计
├── src/
│   ├── thread_pool.cpp
│   ├── blocking_queue.cpp
│   ├── http_client.cpp
│   ├── csv_parser.cpp
│   ├── json_parser.cpp
│   ├── sqlite_storage.cpp
│   ├── pipeline.cpp
│   ├── stats.cpp
│   ├── server.cpp
│   └── main.cpp
├── python/
│   ├── fetcher_adapter.py          # Python 数据源适配器
│   └── requirements.txt
├── tests/
│   ├── test_thread_pool.cpp
│   ├── test_queue.cpp
│   ├── test_parser.cpp
│   └── test_pipeline.cpp
└── README.md
```

## 3. 核心架构

```
                    生产者-消费者模式
                    ==================

  ┌──────────────────────────────────────────────────────┐
  │                    Pipeline 管道编排器                  │
  │                                                      │
  │  ┌─────────┐    ┌──────────┐    ┌────────┐    ┌────┐│
  │  │ Fetcher │    │ 原始数据  │    │ Parser │    │存储 ││
  │  │ (生产者) │──>│   队列    │──>│ (消费者) │──>│    ││
  │  │         │    │          │    │         │    │    ││
  │  │ Thread1 │    │ blocking │    │ Thread3 │    │ DB ││
  │  │ Thread2 │    │  queue   │    │ Thread4 │    │    ││
  │  └─────────┘    └──────────┘    └─────────┘    └────┘│
  │      ↑                                               │
  │      │ HTTP 请求                                      │
  │      ↓                                               │
  │  ┌─────────────┐                                     │
  │  │ Python 适配器│ (子进程 / HTTP)                      │
  │  │ akshare     │                                     │
  │  │ yfinance    │                                     │
  │  └─────────────┘                                     │
  └──────────────────────────────────────────────────────┘
```

## 4. 核心组件

### 4.1 线程池 (`ThreadPool`)

```cpp
// 线程池 = 一组预先创建好的线程 + 一个任务队列
// 有新任务来了 → 丢到队列 → 空闲线程自动取走执行
//
// 为什么用线程池？
// 创建/销毁线程有开销。线程池复用线程，避免频繁创建销毁。

class ThreadPool {
    std::vector<std::thread> workers;           // 工作线程
    std::queue<std::function<void()>> tasks;    // 任务队列
    std::mutex mutex;                           // 互斥锁（保护任务队列）
    std::condition_variable cv;                 // 条件变量（通知线程有新任务）
    bool stop;                                  // 停止标志

public:
    ThreadPool(int num_threads);     // 创建 N 个工作线程
    ~ThreadPool();                   // 等待所有任务完成后销毁

    // 提交一个任务（返回 future 可以拿结果）
    template<class F>
    auto submit(F&& f) -> std::future<decltype(f())>;
};
```

### 4.2 阻塞队列 (`BlockingQueue`)

```cpp
// 线程安全的队列：多个线程可以同时 push/pop，不会出错
// "阻塞"的意思：如果队列空了，pop 会等待直到有新数据
//
// 这就是"生产者-消费者"模式的核心：
// 生产者 push → 队列 → 消费者 pop

template<typename T>
class BlockingQueue {
    std::queue<T> queue;
    std::mutex mutex;               // 互斥锁：同一时间只有一个线程能操作队列
    std::condition_variable cv;     // 条件变量：通知等待的线程
    size_t max_size;                // 最大容量（防止内存爆炸）

public:
    void push(T item);             // 如果满了就等待
    T pop();                       // 如果空了就等待
    bool try_pop(T& item, int timeout_ms);  // 等一段时间，超时返回 false
    size_t size();
    bool empty();
};
```

### 4.3 HTTP 客户端 (`HttpClient`)

```cpp
// 基于 cpp-httplib 的封装
// 支持：超时设置、自动重试、连接复用

class HttpClient {
    int timeout_seconds;
    int max_retries;

public:
    // 发送 GET 请求，返回响应体字符串
    std::string get(const std::string& url);

    // 发送 POST 请求
    std::string post(const std::string& url, const std::string& body);
};
```

### 4.4 解析器接口 (`IParser`)

```cpp
// 解析器把原始文本（CSV/JSON）转换成结构化的 Bar 数据
class IParser {
public:
    virtual ~IParser() = default;
    virtual std::vector<Bar> parse(const std::string& raw_data) = 0;
};

class CsvParser : public IParser { ... };   // 解析 CSV 格式
class JsonParser : public IParser { ... };  // 解析 JSON 格式
```

### 4.5 存储接口 (`IStorage`)

```cpp
class IStorage {
public:
    virtual ~IStorage() = default;
    virtual int save_bars(const std::string& symbol, const std::vector<Bar>& bars) = 0;
    virtual std::vector<Bar> load_bars(const std::string& symbol,
                                        const std::string& start_date,
                                        const std::string& end_date) = 0;
};

class SqliteStorage : public IStorage { ... };  // 写入现有 market.db
```

### 4.6 管道编排器 (`Pipeline`)

```
Pipeline 的工作流程:

1. 接收任务列表: [("000001.SH", "2024-01-01", "2025-01-01"), ...]

2. 生产阶段（多线程并发获取）:
   线程池中 N 个线程同时调用 Python 适配器获取数据
   每获取完一个 → push 到 raw_data_queue

3. 消费阶段（多线程并发解析+存储）:
   另外 M 个线程从 raw_data_queue pop 数据
   解析 CSV/JSON → 清洗 → 批量写入 SQLite

4. 统计: 记录每个阶段的耗时、吞吐量
```

## 5. Python 适配器

C++ 不方便直接调用 akshare/yfinance（它们是 Python 库），所以用 Python 写一个 HTTP 小服务当"中间人"：

```python
# python/fetcher_adapter.py
# 运行在 :8004，C++ 管道通过 HTTP 调用它获取数据

@app.get("/fetch/{symbol}")
def fetch(symbol: str, start_date: str, end_date: str):
    # 用 akshare/yfinance 获取数据
    # 返回 CSV 格式
    ...
```

或者更简单：C++ 用 `subprocess` 调用 Python 脚本，通过 stdout 传数据。

## 6. 吞吐量统计 (`Stats`)

| 统计量 | 说明 |
|--------|------|
| 总任务数 | 要获取多少只股票 |
| 已完成数 | 已经获取完的 |
| 失败数 | 获取失败的 |
| 总耗时 | 从开始到结束 |
| 平均每只耗时 | 总耗时 / 已完成数 |
| 吞吐量 | 已完成数 / 总耗时（只/秒）|
| 队列积压 | raw_data_queue 当前长度 |
| 数据行数 | 总共处理了多少行 Bar 数据 |

## 7. REST API

基础 URL: `http://localhost:8003`

| Method | Path | 请求体 | 返回 |
|--------|------|--------|------|
| POST | `/api/pipeline/fetch` | `{ symbols: [...], start_date, end_date, workers }` | `{ task_id }` |
| GET | `/api/pipeline/status/{id}` | — | `{ progress, completed, failed, stats }` |
| GET | `/api/pipeline/stats` | — | 全局吞吐量统计 |
| POST | `/api/pipeline/stop/{id}` | — | 停止运行中的任务 |

## 8. 前端页面 (`DataPipeline.tsx`)

```
┌────────────────────────────────────────────────────────┐
│                   市场数据管道                            │
├────────────────────────────────────────────────────────┤
│  股票池: [全A股 ▼]  日期: [2024-01-01] ~ [2025-01-01]  │
│  并发线程: [4]                                          │
│  [启动拉取]  [停止]                                     │
├────────────────────────────────────────────────────────┤
│  进度: ████████░░ 156/200 (78%)    失败: 3              │
│  耗时: 45s    速度: 3.5 只/秒    队列积压: 12           │
├────────────────────────────────────────────────────────┤
│  实时日志:                                              │
│  [12:00:01] 000001.SH ✓ 245 bars, 0.3s                │
│  [12:00:01] 000002.SH ✓ 245 bars, 0.4s                │
│  [12:00:02] 000003.SH ✗ timeout                        │
│  ...                                                    │
├────────────────────────────────────────────────────────┤
│  性能对比:                                              │
│  C++ 管道: 3.5 只/秒 (4 线程)                           │
│  Python:   0.8 只/秒 (单线程)                           │
│  加速比:   4.4x                                         │
└────────────────────────────────────────────────────────┘
```

## 9. 实施步骤

| Step | 内容 | 产出文件 |
|------|------|---------|
| 3.1 | 项目脚手架 + CMake | `CMakeLists.txt` |
| 3.2 | 基础类型 | `types.h` |
| 3.3 | 阻塞队列 | `blocking_queue.h/.cpp` |
| 3.4 | 线程池 | `thread_pool.h/.cpp` |
| 3.5 | HTTP 客户端 | `http_client.h/.cpp` |
| 3.6 | CSV / JSON 解析器 | `csv_parser.h/.cpp`, `json_parser.h/.cpp` |
| 3.7 | SQLite 存储 | `sqlite_storage.h/.cpp` |
| 3.8 | Pipeline 编排器 | `pipeline.h/.cpp` |
| 3.9 | 吞吐量统计 | `stats.h/.cpp` |
| 3.10 | Python 适配器 | `python/fetcher_adapter.py` |
| 3.11 | REST API 服务器 | `server.cpp`, `main.cpp` |
| 3.12 | 单元测试 | `tests/*.cpp` |
| 3.13 | Python 代理路由 | `backend/api/routes/pipeline.py` |
| 3.14 | 前端 DataPipeline.tsx | `frontend/src/pages/DataPipeline.tsx` |

## 10. C++ 知识点索引

| 知识点 | 出现位置 | 简单说明 |
|--------|---------|---------|
| `std::thread` | thread_pool | C++ 标准线程，一个 thread 对象 = 一个操作系统线程 |
| `std::mutex` | blocking_queue | 互斥锁，同一时间只有一个线程能进入被保护的代码段 |
| `std::lock_guard` | blocking_queue | RAII 锁，构造时上锁，析构时自动解锁，防止忘记解锁 |
| `std::unique_lock` | blocking_queue | 更灵活的锁，可以手动 lock/unlock，配合条件变量使用 |
| `std::condition_variable` | thread_pool, queue | 线程间通信：一个线程等待，另一个线程通知"有活干了" |
| `std::future` / `std::promise` | thread_pool | 异步获取结果：提交任务后拿到 future，以后再取结果 |
| `std::function<void()>` | thread_pool | 通用函数包装器，可以装任何可调用对象 |
| `template<typename T>` | blocking_queue | 模板：让队列可以存任何类型的数据 |
| `std::atomic` | stats | 原子变量，多线程读写不需要加锁 |
| RAII 模式 | 到处 | 资源获取即初始化，对象销毁时自动释放资源 |
| `virtual` + 接口类 | parser, storage | 运行时多态，允许替换不同实现 |
| `std::chrono` | stats | 高精度计时器 |

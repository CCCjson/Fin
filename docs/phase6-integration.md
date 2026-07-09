# Phase 6：集成 & 收尾

> 把五个模块串联到一起，统一启动、统一访问

## 1. 概述

Phase 1-5 分别实现了五个模块，Phase 6 负责：
1. C++ 服务的编译和启动脚本
2. Python FastAPI 注册新路由 + 代理层
3. 前端导航和类型定义
4. 依赖管理
5. 端到端验证

## 2. 服务端口规划

| 服务 | 端口 | 语言 | 说明 |
|------|------|------|------|
| React 前端 | 5173 | TypeScript | Vite dev server |
| FastAPI 后端 | 8000 | Python | 主后端，代理 C++ 服务 |
| 订单簿模拟器 | 8001 | C++ | 独立进程 |
| C++ 回测 | 8002 | C++ | 独立进程 |
| 数据管道 | 8003 | C++ | 独立进程 |
| Python 数据适配器 | 8004 | Python | 给数据管道提供数据源 |

## 3. 启动脚本

### `scripts/start_all.sh`

```bash
#!/bin/bash
# 一键启动所有服务

echo "=== 启动量化交易系统 ==="

# 1. 编译 C++ 项目（如果需要）
echo "[1/5] 编译 C++ 项目..."
cd orderbook_simulator && mkdir -p build && cd build && cmake .. && make -j4 && cd ../..
cd backtest_cpp && mkdir -p build && cd build && cmake .. && make -j4 && cd ../..
cd data_pipeline && mkdir -p build && cd build && cmake .. && make -j4 && cd ../..

# 2. 启动 C++ 服务
echo "[2/5] 启动 C++ 服务..."
./orderbook_simulator/build/orderbook_server &
./backtest_cpp/build/backtest_server &
./data_pipeline/build/pipeline_server &

# 3. 启动 Python 数据适配器
echo "[3/5] 启动数据适配器..."
conda run -n quant python data_pipeline/python/fetcher_adapter.py &

# 4. 启动 FastAPI 后端
echo "[4/5] 启动 FastAPI..."
conda run -n quant python -m uvicorn api.main:app --host 0.0.0.0 --port 8000 &

# 5. 启动前端
echo "[5/5] 启动前端..."
cd frontend && npm run dev &

echo "=== 所有服务已启动 ==="
echo "前端:  http://localhost:5173"
echo "API:   http://localhost:8000/docs"
```

### `scripts/stop_all.sh`

```bash
#!/bin/bash
# 停止所有服务
pkill -f orderbook_server
pkill -f backtest_server
pkill -f pipeline_server
pkill -f fetcher_adapter
pkill -f uvicorn
echo "所有服务已停止"
```

## 4. Python 代理路由

### 4.1 代理工具函数 (`api/utils/proxy.py`)

```python
import httpx

async def proxy_request(
    method: str,
    target_url: str,
    body: dict = None,
    timeout: float = 30.0
) -> dict:
    """转发请求到 C++ 服务"""
    async with httpx.AsyncClient(timeout=timeout) as client:
        if method == "GET":
            resp = await client.get(target_url)
        elif method == "POST":
            resp = await client.post(target_url, json=body)
        elif method == "DELETE":
            resp = await client.delete(target_url)
        resp.raise_for_status()
        return resp.json()
```

### 4.2 新增代理路由

| 文件 | 代理目标 | 前缀 |
|------|---------|------|
| `api/routes/orderbook.py` | `localhost:8001` | `/orderbook/...` |
| `api/routes/backtest_cpp.py` | `localhost:8002` | `/backtest-cpp/...` |
| `api/routes/pipeline.py` | `localhost:8003` | `/pipeline/...` |

### 4.3 新增 Python 原生路由

| 文件 | 说明 |
|------|------|
| `api/routes/factor.py` | 因子研究（直接调用 FactorEngine） |
| `api/routes/ml.py` | ML 预测（直接调用 MLEngine） |

## 5. 修改现有文件

### 5.1 `backend/api/main.py`

```python
# 新增 import
from api.routes import orderbook, backtest_cpp, pipeline, factor, ml

# 新增路由注册
app.include_router(orderbook.router)
app.include_router(backtest_cpp.router)
app.include_router(pipeline.router)
app.include_router(factor.router)
app.include_router(ml.router)
```

### 5.2 `backend/data_engine/storage/models.py`

新增 6 张表：

| 表名 | 来自模块 | 用途 |
|------|---------|------|
| `OrderBookSession` | 订单簿 | 模拟会话记录 |
| `OrderBookFill` | 订单簿 | 成交记录存档 |
| `FactorValue` | 因子研究 | 因子值存储 |
| `FactorAnalysisResult` | 因子研究 | 分析结果 |
| `MLModel` | ML 预测 | 模型元数据 |
| `MLPrediction` | ML 预测 | 预测记录 |

### 5.3 `backend/requirements.txt`

新增依赖：
```
# Phase 4: 因子研究
scipy>=1.11.0

# Phase 5: ML 预测
lightgbm>=4.0.0
scikit-learn>=1.3.0
joblib>=1.3.0
arch>=7.0.0

# Phase 6: 代理
httpx>=0.25.0
```

### 5.4 `frontend/src/components/layout/Layout.tsx`

新增 5 个页面的 lazy import + routeConfig + navItems：

```typescript
// 新增 lazy import
const OrderBook = React.lazy(() => import('../../pages/OrderBook').then(m => ({ default: m.OrderBook })));
const BacktestCpp = React.lazy(() => import('../../pages/BacktestCpp').then(m => ({ default: m.BacktestCpp })));
const DataPipeline = React.lazy(() => import('../../pages/DataPipeline').then(m => ({ default: m.DataPipeline })));
const FactorResearch = React.lazy(() => import('../../pages/FactorResearch').then(m => ({ default: m.FactorResearch })));
const MLPrediction = React.lazy(() => import('../../pages/MLPrediction').then(m => ({ default: m.MLPrediction })));

// routeConfig 追加
{ path: '/orderbook', Component: OrderBook },
{ path: '/backtest-cpp', Component: BacktestCpp },
{ path: '/data-pipeline', Component: DataPipeline },
{ path: '/factor-research', Component: FactorResearch },
{ path: '/ml-prediction', Component: MLPrediction },

// navItems 追加（分组显示）
// --- 量化研究 ---
{ path: '/factor-research', label: '因子研究', icon: '🔍' },
{ path: '/ml-prediction', label: 'ML 预测', icon: '🧠' },
// --- 系统工程 ---
{ path: '/orderbook', label: '订单簿', icon: '📊' },
{ path: '/backtest-cpp', label: 'C++回测', icon: '⚡' },
{ path: '/data-pipeline', label: '数据管道', icon: '🔄' },
```

### 5.5 `frontend/src/types/index.ts`

新增类型定义（详见各 Phase 文档中的数据结构）。

## 6. 依赖安装

### Python
```bash
conda run -n quant pip install scipy lightgbm scikit-learn joblib arch httpx
```

### C++ (CMake 自动拉取)
- nlohmann/json — JSON 序列化
- cpp-httplib — HTTP 服务器
- GoogleTest — 单元测试
- sqlite3 — 数据库（系统自带或 brew install）

### 前端
不需要新增 npm 包，复用现有 Recharts + TailwindCSS。

## 7. 端到端验证清单

### 7.1 订单簿模拟器
- [ ] C++ 服务编译通过
- [ ] `POST /api/sessions` 创建会话成功
- [ ] `POST /api/sessions/{id}/seed` 播种订单
- [ ] `POST /api/sessions/{id}/orders` MARKET 单 → 成交
- [ ] `POST /api/sessions/{id}/orders` LIMIT 单 → 挂单或成交
- [ ] `POST /api/sessions/{id}/orders` FOK 单 → 拒绝或全成交
- [ ] `DELETE /api/sessions/{id}/orders/{oid}` 撤单
- [ ] `GET /api/sessions/{id}/depth` 盘口正确
- [ ] WebSocket 实时推送
- [ ] 前端 OrderBook.tsx 页面正常

### 7.2 C++ 回测
- [ ] C++ 服务编译通过
- [ ] `POST /api/backtest/run` 均线策略回测
- [ ] 绩效指标正确（Sharpe、回撤等）
- [ ] 前端展示结果

### 7.3 数据管道
- [ ] C++ 服务编译通过
- [ ] Python 适配器启动正常
- [ ] `POST /api/pipeline/fetch` 多线程并发拉取
- [ ] 吞吐量统计正确
- [ ] 对比 Python 版性能提升

### 7.4 因子研究
- [ ] 选择动量因子 → 全 A 股分析
- [ ] 流式进度条正常
- [ ] IC 序列柱状图显示正确
- [ ] IC 衰减柱状图显示正确
- [ ] 多空资金曲线正常
- [ ] 结果保存到数据库

### 7.5 ML 预测
- [ ] 选择 LightGBM 分类 → 训练
- [ ] Walk-Forward 按 fold 推送进度
- [ ] 特征重要性柱状图正确
- [ ] 混淆矩阵显示正确
- [ ] Walk-Forward 资金曲线正常
- [ ] 模型保存/加载/预测

### 7.6 全局
- [ ] 前端 5 个新页面导航正常
- [ ] 暗色主题一致
- [ ] 侧边栏分组显示
- [ ] `start_all.sh` 一键启动
- [ ] `stop_all.sh` 一键停止

## 8. 实施步骤

| Step | 内容 |
|------|------|
| 6.1 | 创建 `scripts/start_all.sh` 和 `stop_all.sh` |
| 6.2 | 创建 `api/utils/proxy.py` 代理工具 |
| 6.3 | 创建代理路由：`orderbook.py`、`backtest_cpp.py`、`pipeline.py` |
| 6.4 | 修改 `api/main.py` 注册 5 个新 router |
| 6.5 | 修改 `models.py` 添加 6 张新表 |
| 6.6 | 修改 `requirements.txt` 添加依赖 |
| 6.7 | 修改 `Layout.tsx` 添加导航 + 路由 |
| 6.8 | 修改 `types/index.ts` 添加类型 |
| 6.9 | `conda run -n quant pip install` 安装依赖 |
| 6.10 | 端到端验证所有模块 |

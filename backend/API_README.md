# 量化交易系统 API 文档

## 快速开始

### 1. 启动API服务器

```bash
bash restart.sh --backend
```

服务器将在 `http://127.0.0.1:8000` 启动

### 2. 访问API文档

- **Swagger UI**: http://127.0.0.1:8000/docs
- **ReDoc**: http://127.0.0.1:8000/redoc

### 3. 运行演示

```bash
python tests/demo_api.py
```

## API模块

### 📊 数据模块 (`/data`)

#### 获取日线数据
```bash
POST /data/daily
{
  "symbol": "688576.SH",
  "start_date": "2025-01-01",
  "end_date": "2025-12-31"
}
```

#### 获取股票列表
```bash
GET /data/stocks?market=A
```

#### 更新数据
```bash
POST /data/update?symbol=688576.SH&days=30
```

### 📈 分析模块 (`/analysis`)

#### 计算技术指标
```bash
POST /analysis/indicators
{
  "symbol": "688576.SH",
  "start_date": "2025-01-01",
  "end_date": "2025-12-31",
  "indicators": ["MA", "RSI", "MACD"]
}
```

支持的指标：
- **趋势**: MA, EMA, MACD, BOLL, DMI, ADX
- **动量**: RSI, KDJ, CCI, ROC, WR
- **波动**: ATR, KC, DC
- **成交量**: OBV, VWAP, MFI, VR

#### 检测信号
```bash
POST /analysis/signals
{
  "symbol": "688576.SH",
  "start_date": "2025-01-01",
  "end_date": "2025-12-31"
}
```

#### 识别K线形态
```bash
POST /analysis/patterns
{
  "symbol": "688576.SH",
  "start_date": "2025-01-01",
  "end_date": "2025-12-31"
}
```

### 🔄 回测模块 (`/backtest`)

#### 运行回测
```bash
POST /backtest/run
{
  "strategy_name": "MA_CROSS",
  "symbol": "688576.SH",
  "start_date": "2024-01-01",
  "end_date": "2025-12-31",
  "initial_capital": 1000000.0,
  "strategy_params": {
    "fast_period": 5,
    "slow_period": 20
  }
}
```

支持的策略：
- **MA_CROSS**: 均线交叉策略
- **SIGNAL**: 基于技术信号的策略

### 💰 交易模块 (`/trading`)

#### 初始化账户
```bash
POST /trading/init?initial_cash=1000000.0&commission_rate=0.0003
```

#### 提交订单
```bash
POST /trading/order
{
  "symbol": "688576.SH",
  "action": "BUY",
  "quantity": 100,
  "price": null  # null表示市价单
}
```

#### 更新市场价格
```bash
POST /trading/price/update
{
  "symbol": "688576.SH",
  "price": 63.50
}
```

#### 查看账户
```bash
GET /trading/account
```

#### 查看持仓
```bash
GET /trading/position/688576.SH
```

#### 查看订单
```bash
GET /trading/orders?symbol=688576.SH
```

### 📊 监控模块 (`/monitor`)

#### 初始化监控
```bash
POST /monitor/init?initial_capital=1000000.0
```

#### 获取交易日志
```bash
GET /monitor/trades?symbol=688576.SH&limit=100
```

#### 获取订单状态
```bash
GET /monitor/orders/status
```

#### 获取绩效统计
```bash
GET /monitor/performance
```

#### 获取告警
```bash
GET /monitor/alerts?alert_type=WARNING&limit=100
```

## 使用示例

### Python示例

```python
import requests

BASE_URL = "http://127.0.0.1:8000"

# 1. 初始化交易账户
response = requests.post(f"{BASE_URL}/trading/init", params={
    "initial_cash": 1000000.0
})
print(response.json())

# 2. 获取股票数据
response = requests.post(f"{BASE_URL}/data/daily", json={
    "symbol": "688576.SH",
    "start_date": "2025-01-01",
    "end_date": "2025-12-31"
})
data = response.json()
print(f"获取到 {data['count']} 条数据")

# 3. 更新价格并下单
requests.post(f"{BASE_URL}/trading/price/update", json={
    "symbol": "688576.SH",
    "price": 63.50
})

response = requests.post(f"{BASE_URL}/trading/order", json={
    "symbol": "688576.SH",
    "action": "BUY",
    "quantity": 100
})
order = response.json()
print(f"订单成交: {order['message']}")

# 4. 查看账户
response = requests.get(f"{BASE_URL}/trading/account")
account = response.json()
print(f"总资产: {account['total_value']:,.2f}")
```

### cURL示例

```bash
# 初始化账户
curl -X POST "http://127.0.0.1:8000/trading/init?initial_cash=1000000.0"

# 获取数据
curl -X POST "http://127.0.0.1:8000/data/daily" \
  -H "Content-Type: application/json" \
  -d '{
    "symbol": "688576.SH",
    "start_date": "2025-01-01",
    "end_date": "2025-12-31"
  }'

# 下单
curl -X POST "http://127.0.0.1:8000/trading/order" \
  -H "Content-Type: application/json" \
  -d '{
    "symbol": "688576.SH",
    "action": "BUY",
    "quantity": 100
  }'

# 查看账户
curl "http://127.0.0.1:8000/trading/account"
```

## 注意事项

1. **本地使用**: API运行在 `127.0.0.1:8000`，仅本地可访问
2. **Paper Trading**: 交易模块使用模拟交易，不会实际下单
3. **数据要求**: 某些指标需要足够的历史数据才能计算
4. **会话管理**: 交易账户和监控数据在API重启后会清空

## 目录结构

```
backend/
├── api/
│   ├── main.py              # FastAPI主程序
│   ├── routes/              # API路由
│   │   ├── data.py         # 数据接口
│   │   ├── analysis.py     # 分析接口
│   │   ├── backtest.py     # 回测接口
│   │   ├── trading.py      # 交易接口
│   │   └── monitor.py      # 监控接口
│   └── models/
│       └── schemas.py       # 数据模型
└── tests/
    ├── demo_api.py          # API演示
    └── test_api.py          # API测试
```

## 下一步

1. 集成实时行情数据源
2. 添加策略管理功能
3. 实现定时任务（自动更新数据、策略执行）
4. 开发Web前端界面
5. 添加用户认证和权限管理

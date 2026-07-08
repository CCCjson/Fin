# API 接口设计文档

## 1. 概述

### 1.1 设计原则
- **RESTful 风格**: 资源导向的URL设计
- **版本化**: API版本控制(/api/v1)
- **统一响应**: 标准化的响应格式
- **错误处理**: 清晰的错误码和信息
- **文档自动生成**: FastAPI自动生成OpenAPI文档

### 1.2 技术栈
- **框架**: FastAPI
- **认证**: JWT (可选)
- **文档**: Swagger UI / ReDoc
- **CORS**: 跨域配置
- **限流**: slowapi

### 1.3 基础URL
```
开发环境: http://localhost:8000/api/v1
生产环境: https://api.yourdomain.com/api/v1
WebSocket: ws://localhost:8000/ws
```

## 2. 通用规范

### 2.1 请求格式

#### HTTP方法
- `GET`: 获取资源
- `POST`: 创建资源
- `PUT`: 完整更新资源
- `PATCH`: 部分更新资源
- `DELETE`: 删除资源

#### 请求头
```http
Content-Type: application/json
Authorization: Bearer <token>  # 可选
```

#### 查询参数
```
分页: ?page=1&page_size=20
排序: ?sort_by=date&order=desc
过滤: ?filter={"signal_type": "BUY"}
```

### 2.2 响应格式

#### 成功响应
```json
{
  "code": 200,
  "message": "Success",
  "data": {
    // 实际数据
  },
  "meta": {
    "page": 1,
    "page_size": 20,
    "total": 100
  }
}
```

#### 错误响应
```json
{
  "code": 400,
  "message": "Bad Request",
  "errors": [
    {
      "field": "symbol",
      "message": "Invalid symbol format"
    }
  ]
}
```

### 2.3 状态码

| 状态码 | 说明 |
|-------|------|
| 200 | 成功 |
| 201 | 创建成功 |
| 204 | 无内容(删除成功) |
| 400 | 请求参数错误 |
| 401 | 未授权 |
| 403 | 禁止访问 |
| 404 | 资源不存在 |
| 429 | 请求过于频繁 |
| 500 | 服务器内部错误 |

## 3. API 接口

### 3.1 市场数据 (Market)

#### 3.1.1 获取K线数据

```http
GET /api/v1/market/kline
```

**请求参数:**
```typescript
{
  symbol: string;        // 股票代码, 如 "000001.SZ"
  start_date: string;    // 开始日期, 如 "2024-01-01"
  end_date: string;      // 结束日期, 如 "2024-12-31"
  frequency?: string;    // 频率, 默认 "1d" (日线)
  adjust?: string;       // 复权方式, 默认 "qfq" (前复权)
}
```

**响应:**
```json
{
  "code": 200,
  "message": "Success",
  "data": [
    {
      "date": "2024-01-01T00:00:00Z",
      "open": 10.50,
      "high": 11.20,
      "low": 10.30,
      "close": 11.00,
      "volume": 1500000,
      "amount": 16500000
    }
  ]
}
```

#### 3.1.2 获取实时行情

```http
POST /api/v1/market/realtime
```

**请求体:**
```json
{
  "symbols": ["000001.SZ", "600000.SH", "AAPL"]
}
```

**响应:**
```json
{
  "code": 200,
  "message": "Success",
  "data": [
    {
      "symbol": "000001.SZ",
      "price": 11.25,
      "change": 0.25,
      "change_percent": 2.27,
      "volume": 2000000,
      "timestamp": "2024-02-05T09:30:00Z"
    }
  ]
}
```

#### 3.1.3 搜索股票

```http
GET /api/v1/market/search?keyword=平安
```

**响应:**
```json
{
  "code": 200,
  "message": "Success",
  "data": [
    {
      "symbol": "000001.SZ",
      "name": "平安银行",
      "market": "a_share",
      "industry": "银行"
    }
  ]
}
```

#### 3.1.4 获取股票信息

```http
GET /api/v1/market/info/{symbol}
```

**响应:**
```json
{
  "code": 200,
  "message": "Success",
  "data": {
    "symbol": "000001.SZ",
    "name": "平安银行",
    "market": "a_share",
    "industry": "银行",
    "sector": "金融",
    "list_date": "1991-04-03",
    "exchange": "SZ"
  }
}
```

### 3.2 分析服务 (Analysis)

#### 3.2.1 计算技术指标

```http
POST /api/v1/analysis/indicators
```

**请求体:**
```json
{
  "symbol": "000001.SZ",
  "start_date": "2024-01-01",
  "end_date": "2024-12-31",
  "indicators": [
    {
      "name": "ma",
      "params": {"period": 20}
    },
    {
      "name": "rsi",
      "params": {"period": 14}
    }
  ]
}
```

**响应:**
```json
{
  "code": 200,
  "message": "Success",
  "data": {
    "symbol": "000001.SZ",
    "kline": [...],
    "indicators": {
      "ma_20": [10.5, 10.6, ...],
      "rsi": [45.2, 48.5, ...]
    }
  }
}
```

#### 3.2.2 检测交易信号

```http
POST /api/v1/analysis/signals
```

**请求体:**
```json
{
  "symbol": "000001.SZ",
  "strategy": "default",
  "start_date": "2024-01-01",
  "end_date": "2024-12-31"
}
```

**响应:**
```json
{
  "code": 200,
  "message": "Success",
  "data": [
    {
      "symbol": "000001.SZ",
      "date": "2024-02-01T00:00:00Z",
      "signal_type": "BUY",
      "strength": 0.85,
      "price": 11.20,
      "reasons": [
        {
          "indicator": "ma_golden_cross",
          "detail": "MA5 上穿 MA20"
        },
        {
          "indicator": "rsi_oversold",
          "detail": "RSI 从 28 回升至 35"
        }
      ],
      "suggested_action": {
        "entry_price": 11.20,
        "stop_loss": 10.64,
        "take_profit": 12.32,
        "position_size": "5%"
      }
    }
  ]
}
```

#### 3.2.3 批量筛选股票

```http
POST /api/v1/analysis/screener
```

**请求体:**
```json
{
  "market": "a_share",
  "strategy": "default",
  "filters": {
    "min_price": 5.0,
    "max_price": 50.0,
    "min_volume": 1000000,
    "min_strength": 0.6
  },
  "limit": 20
}
```

**响应:**
```json
{
  "code": 200,
  "message": "Success",
  "data": [
    {
      "symbol": "000001.SZ",
      "name": "平安银行",
      "latest_price": 11.20,
      "signals": [
        {
          "signal_type": "BUY",
          "strength": 0.85,
          "date": "2024-02-05"
        }
      ]
    }
  ],
  "meta": {
    "total": 15,
    "scanned": 3500
  }
}
```

### 3.3 回测服务 (Backtest)

#### 3.3.1 创建回测任务

```http
POST /api/v1/backtest/create
```

**请求体:**
```json
{
  "name": "MA Cross Strategy Test",
  "strategy": {
    "type": "ma_cross",
    "params": {
      "short_period": 5,
      "long_period": 20
    }
  },
  "symbols": ["000001.SZ", "600000.SH"],
  "start_date": "2023-01-01",
  "end_date": "2024-12-31",
  "initial_capital": 1000000,
  "config": {
    "commission_rate": 0.00025,
    "slippage_rate": 0.001
  }
}
```

**响应:**
```json
{
  "code": 201,
  "message": "Backtest task created",
  "data": {
    "task_id": "bt_20240205_abc123",
    "status": "pending"
  }
}
```

#### 3.3.2 获取回测结果

```http
GET /api/v1/backtest/{task_id}
```

**响应:**
```json
{
  "code": 200,
  "message": "Success",
  "data": {
    "task_id": "bt_20240205_abc123",
    "status": "completed",
    "result": {
      "metrics": {
        "returns": {
          "total_return": 0.25,
          "annual_return": 0.22
        },
        "risk": {
          "max_drawdown": -0.15,
          "sharpe_ratio": 1.8,
          "sortino_ratio": 2.3
        },
        "trades": {
          "total_trades": 45,
          "win_rate": 0.62,
          "profit_loss_ratio": 1.5
        }
      },
      "daily_records": [...],
      "trade_records": [...]
    },
    "created_at": "2024-02-05T10:00:00Z",
    "completed_at": "2024-02-05T10:05:30Z"
  }
}
```

#### 3.3.3 列出回测任务

```http
GET /api/v1/backtest/list?page=1&page_size=10
```

**响应:**
```json
{
  "code": 200,
  "message": "Success",
  "data": [
    {
      "task_id": "bt_20240205_abc123",
      "name": "MA Cross Strategy Test",
      "status": "completed",
      "created_at": "2024-02-05T10:00:00Z"
    }
  ],
  "meta": {
    "page": 1,
    "page_size": 10,
    "total": 25
  }
}
```

### 3.4 交易服务 (Trade)

#### 3.4.1 获取账户信息

```http
GET /api/v1/trade/account
```

**响应:**
```json
{
  "code": 200,
  "message": "Success",
  "data": {
    "account_id": "ACC12345678",
    "cash": 850000.00,
    "available_cash": 800000.00,
    "position_value": 250000.00,
    "total_value": 1100000.00,
    "total_pnl": 100000.00,
    "daily_pnl": 5000.00,
    "is_trading_halted": false
  }
}
```

#### 3.4.2 获取持仓列表

```http
GET /api/v1/trade/positions
```

**响应:**
```json
{
  "code": 200,
  "message": "Success",
  "data": [
    {
      "symbol": "000001.SZ",
      "quantity": 10000,
      "available_quantity": 10000,
      "avg_cost": 10.50,
      "current_price": 11.20,
      "market_value": 112000.00,
      "unrealized_pnl": 7000.00,
      "pnl_pct": 0.0667,
      "stop_loss": 9.98,
      "take_profit": 12.60
    }
  ]
}
```

#### 3.4.3 提交订单

```http
POST /api/v1/trade/orders
```

**请求体:**
```json
{
  "symbol": "000001.SZ",
  "side": "BUY",
  "quantity": 1000,
  "order_type": "MARKET",
  "price": null,
  "strategy": "ma_cross",
  "reason": "MA5 golden cross MA20"
}
```

**响应:**
```json
{
  "code": 201,
  "message": "Order created",
  "data": {
    "order_id": "ORD20240205ABC123",
    "symbol": "000001.SZ",
    "side": "BUY",
    "quantity": 1000,
    "status": "SUBMITTED",
    "created_at": "2024-02-05T10:00:00Z"
  }
}
```

#### 3.4.4 查询订单

```http
GET /api/v1/trade/orders?status=PENDING&page=1&page_size=20
```

**响应:**
```json
{
  "code": 200,
  "message": "Success",
  "data": [
    {
      "order_id": "ORD20240205ABC123",
      "symbol": "000001.SZ",
      "side": "BUY",
      "order_type": "MARKET",
      "quantity": 1000,
      "status": "PENDING",
      "created_at": "2024-02-05T10:00:00Z"
    }
  ],
  "meta": {
    "page": 1,
    "page_size": 20,
    "total": 5
  }
}
```

#### 3.4.5 取消订单

```http
DELETE /api/v1/trade/orders/{order_id}
```

**响应:**
```json
{
  "code": 200,
  "message": "Order cancelled",
  "data": {
    "order_id": "ORD20240205ABC123",
    "status": "CANCELLED"
  }
}
```

#### 3.4.6 获取交易记录

```http
GET /api/v1/trade/history?start_date=2024-01-01&end_date=2024-12-31
```

**响应:**
```json
{
  "code": 200,
  "message": "Success",
  "data": [
    {
      "trade_id": "T20240205001",
      "order_id": "ORD20240205ABC123",
      "symbol": "000001.SZ",
      "direction": "long",
      "quantity": 1000,
      "price": 11.25,
      "commission": 2.81,
      "amount": 11250.00,
      "executed_at": "2024-02-05T10:05:00Z"
    }
  ]
}
```

### 3.5 系统服务 (System)

#### 3.5.1 健康检查

```http
GET /api/v1/health
```

**响应:**
```json
{
  "code": 200,
  "message": "Healthy",
  "data": {
    "status": "ok",
    "version": "1.0.0",
    "uptime": 86400,
    "database": "connected",
    "cache": "connected"
  }
}
```

#### 3.5.2 获取系统配置

```http
GET /api/v1/system/config
```

**响应:**
```json
{
  "code": 200,
  "message": "Success",
  "data": {
    "markets": ["a_share", "hk_stock", "us_stock"],
    "indicators": ["ma", "ema", "macd", "rsi", "kdj", "boll"],
    "strategies": ["default", "conservative", "aggressive"],
    "trading_mode": "paper"
  }
}
```

## 4. WebSocket 接口

### 4.1 连接

```javascript
const ws = new WebSocket('ws://localhost:8000/ws');
```

### 4.2 订阅实时行情

**发送:**
```json
{
  "action": "subscribe",
  "channel": "market",
  "symbols": ["000001.SZ", "600000.SH"]
}
```

**接收:**
```json
{
  "channel": "market",
  "data": {
    "symbol": "000001.SZ",
    "price": 11.25,
    "change": 0.05,
    "volume": 1500000,
    "timestamp": "2024-02-05T10:00:00Z"
  }
}
```

### 4.3 订阅信号推送

**发送:**
```json
{
  "action": "subscribe",
  "channel": "signals",
  "filters": {
    "min_strength": 0.7
  }
}
```

**接收:**
```json
{
  "channel": "signals",
  "data": {
    "symbol": "000001.SZ",
    "signal_type": "BUY",
    "strength": 0.85,
    "price": 11.20,
    "timestamp": "2024-02-05T10:00:00Z"
  }
}
```

### 4.4 订阅订单状态

**发送:**
```json
{
  "action": "subscribe",
  "channel": "orders"
}
```

**接收:**
```json
{
  "channel": "orders",
  "data": {
    "order_id": "ORD20240205ABC123",
    "status": "FILLED",
    "filled_price": 11.25,
    "filled_quantity": 1000,
    "timestamp": "2024-02-05T10:05:00Z"
  }
}
```

## 5. FastAPI 实现

### 5.1 主应用

```python
# backend/main.py
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from api.routes import market, analysis, backtest, trade, system

app = FastAPI(
    title="Quant Trading API",
    description="个人量化交易系统 API",
    version="1.0.0",
    docs_url="/api/docs",
    redoc_url="/api/redoc"
)

# CORS配置
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:3000"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# 注册路由
app.include_router(market.router, prefix="/api/v1/market", tags=["Market"])
app.include_router(analysis.router, prefix="/api/v1/analysis", tags=["Analysis"])
app.include_router(backtest.router, prefix="/api/v1/backtest", tags=["Backtest"])
app.include_router(trade.router, prefix="/api/v1/trade", tags=["Trade"])
app.include_router(system.router, prefix="/api/v1/system", tags=["System"])

@app.get("/api/v1/health")
async def health_check():
    return {
        "code": 200,
        "message": "Healthy",
        "data": {
            "status": "ok",
            "version": "1.0.0"
        }
    }
```

### 5.2 路由示例

```python
# api/routes/market.py
from fastapi import APIRouter, Query, HTTPException
from typing import List, Optional
from pydantic import BaseModel
from datetime import datetime

router = APIRouter()

class KlineRequest(BaseModel):
    symbol: str
    start_date: str
    end_date: str
    frequency: str = "1d"
    adjust: str = "qfq"

@router.get("/kline")
async def get_kline(
    symbol: str = Query(..., description="股票代码"),
    start_date: str = Query(..., description="开始日期"),
    end_date: str = Query(..., description="结束日期"),
    frequency: str = Query("1d", description="频率"),
    adjust: str = Query("qfq", description="复权方式")
):
    """获取K线数据"""
    try:
        # 调用数据引擎
        data = data_engine.get_daily_data(
            symbol=symbol,
            start_date=start_date,
            end_date=end_date,
            adjust=adjust
        )

        return {
            "code": 200,
            "message": "Success",
            "data": data.to_dict(orient="records")
        }

    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@router.post("/realtime")
async def get_realtime_quotes(request: dict):
    """获取实时行情"""
    symbols = request.get("symbols", [])

    try:
        quotes = data_engine.get_realtime_quotes(symbols)

        return {
            "code": 200,
            "message": "Success",
            "data": quotes
        }

    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
```

### 5.3 统一响应模型

```python
# api/models.py
from pydantic import BaseModel
from typing import Optional, Any, List

class Response(BaseModel):
    """统一响应模型"""
    code: int
    message: str
    data: Optional[Any] = None
    meta: Optional[dict] = None

class ErrorResponse(BaseModel):
    """错误响应模型"""
    code: int
    message: str
    errors: Optional[List[dict]] = None
```

### 5.4 异常处理

```python
# api/exceptions.py
from fastapi import Request, status
from fastapi.responses import JSONResponse

@app.exception_handler(Exception)
async def global_exception_handler(request: Request, exc: Exception):
    return JSONResponse(
        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        content={
            "code": 500,
            "message": "Internal Server Error",
            "errors": [{"detail": str(exc)}]
        }
    )
```

## 6. API 测试

### 6.1 使用 curl

```bash
# 获取K线数据
curl -X GET "http://localhost:8000/api/v1/market/kline?symbol=000001.SZ&start_date=2024-01-01&end_date=2024-12-31"

# 提交订单
curl -X POST "http://localhost:8000/api/v1/trade/orders" \
  -H "Content-Type: application/json" \
  -d '{
    "symbol": "000001.SZ",
    "side": "BUY",
    "quantity": 1000,
    "order_type": "MARKET"
  }'
```

### 6.2 使用 Python requests

```python
import requests

# 获取K线数据
response = requests.get(
    "http://localhost:8000/api/v1/market/kline",
    params={
        "symbol": "000001.SZ",
        "start_date": "2024-01-01",
        "end_date": "2024-12-31"
    }
)

data = response.json()
print(data["data"])
```

## 7. 限流配置

```python
from slowapi import Limiter, _rate_limit_exceeded_handler
from slowapi.util import get_remote_address
from slowapi.errors import RateLimitExceeded

limiter = Limiter(key_func=get_remote_address)
app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)

@router.get("/kline")
@limiter.limit("100/minute")
async def get_kline(request: Request, ...):
    pass
```

## 8. API 文档

FastAPI 自动生成:
- Swagger UI: http://localhost:8000/api/docs
- ReDoc: http://localhost:8000/api/redoc
- OpenAPI JSON: http://localhost:8000/openapi.json

## 9. 总结

API 接口设计要点:
1. **RESTful 规范**: 清晰的资源定义和URL设计
2. **统一响应**: 标准化的响应格式
3. **完整文档**: 自动生成的交互式文档
4. **错误处理**: 清晰的错误信息
5. **性能优化**: 限流、缓存、异步处理

API 接口是前后端通信的桥梁,设计需要清晰、规范、易用。

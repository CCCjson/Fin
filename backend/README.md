# 量化交易系统后端

## 快速开始

### 1. 安装依赖

```bash
# 创建虚拟环境（推荐）
python -m venv venv
source venv/bin/activate  # Linux/Mac
# 或
venv\Scripts\activate  # Windows

# 安装依赖
pip install -r requirements.txt
```

### 2. 配置环境变量

复制 `.env.example` 为 `.env` 并配置：

```bash
cp .env.example .env
```

### 3. 测试数据引擎

```bash
python test_data_engine.py
```

## 数据引擎使用示例

```python
from data_engine import DataEngine, init_db

# 初始化数据库
init_db()

# 创建数据引擎实例
engine = DataEngine()

# 获取日线数据
df = engine.get_daily_data(
    symbol="000001.SZ",
    start_date="2024-01-01",
    end_date="2024-12-31"
)

# 搜索股票
results = engine.search_stocks("平安", market="a_share")

# 获取实时行情
quotes = engine.get_realtime_quotes(["000001.SZ", "600000.SH"])

# 关闭连接
engine.close()
```

## 目录结构

```
backend/
├── data_engine/          # 数据引擎模块
│   ├── fetchers/        # 数据获取器
│   ├── storage/         # 数据存储
│   ├── processors/      # 数据处理器
│   └── engine.py        # 数据引擎主类
├── data/                # 数据库文件
├── logs/                # 日志文件
├── tests/               # 测试文件
└── requirements.txt     # 依赖列表
```

## 支持的市场

- **A股**: 通过 akshare 获取数据
- **港股**: 通过 yfinance 获取数据
- **美股**: 通过 yfinance 获取数据

## 主要功能

### 数据获取
- ✅ 日线数据获取（OHLCV）
- ✅ 实时行情获取
- ✅ 股票搜索
- ✅ 股票列表更新

### 数据处理
- ✅ 数据标准化
- ✅ 数据验证（OHLC 关系检查）
- ✅ 数据清洗（去重、填充、异常值处理）

### 数据存储
- ✅ SQLite 数据库存储
- ✅ 增量更新
- ✅ 数据去重

## 下一步

- [ ] 实现分析引擎（技术指标计算）
- [ ] 实现回测引擎
- [ ] 实现交易引擎
- [ ] 实现前端界面

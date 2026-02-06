# 📈 量化交易系统

一个功能完整的量化交易系统，支持策略回测、Paper Trading 模拟交易、实时行情监控和信号分析。

![License](https://img.shields.io/badge/license-MIT-blue.svg)
![Python](https://img.shields.io/badge/python-3.13-blue.svg)
![React](https://img.shields.io/badge/react-18-blue.svg)

## ✨ 核心功能

### 🔄 数据引擎
- **A股行情数据获取**：通过 AkShare 获取沪深股票日线数据
- **数据持久化**：SQLite 数据库存储历史行情
- **增量更新**：智能判断是否需要从网络获取最新数据
- **数据清洗**：自动处理缺失值和异常数据

### 📊 回测引擎
- **多策略支持**：
  - MA Cross（均线交叉策略）
  - MACD 策略
  - KDJ 策略
  - RSI 策略
  - 自定义信号策略
- **完整的性能指标**：
  - 总收益率、年化收益率
  - 夏普比率、波动率
  - 最大回撤及回撤期间
  - 胜率、盈亏比
  - 交易次数统计
- **可视化报告**：
  - 资产曲线图
  - 交易记录详情
  - 每日收益明细

### 💹 交易引擎
- **Paper Trading 模拟盘**：
  - 模拟真实交易流程
  - T+1 交易规则
  - 手续费和滑点模拟
  - 持仓管理
- **订单管理**：
  - 市价单/限价单
  - 订单状态跟踪
  - 成交记录查询

### 📡 信号系统
- **实时信号生成**：基于技术指标自动生成买卖信号
- **信号历史记录**：保存所有历史信号便于回溯
- **信号统计分析**：按时间段、股票、策略统计信号表现

### 📱 前端界面
- **仪表盘**：账户总览、持仓概览、最近信号
- **行情页面**：K线图、实时报价、技术指标
- **交易页面**：下单、持仓查看、订单记录
- **回测页面**：创建回测任务、查看回测结果、策略对比
- **响应式设计**：支持桌面和移动端

## 🛠️ 技术栈

### 后端
- **框架**：FastAPI
- **数据库**：SQLite + SQLAlchemy ORM
- **数据源**：AkShare
- **技术指标**：TA-Lib / 自定义实现
- **日志**：Loguru
- **并发**：BackgroundTasks 异步任务

### 前端
- **框架**：React 18 + TypeScript
- **构建工具**：Vite
- **图表库**：Recharts
- **HTTP 客户端**：Axios
- **样式**：Tailwind CSS
- **路由**：React Router

### 数据分析
- **数据处理**：Pandas + NumPy
- **技术指标计算**：自定义指标库
- **性能优化**：向量化计算

## 🚀 快速开始

### 环境要求

- Python 3.13+
- Node.js 18+
- SQLite 3

### 后端安装

```bash
# 进入后端目录
cd backend

# 创建虚拟环境
python -m venv venv
source venv/bin/activate  # Windows: venv\Scripts\activate

# 安装依赖
pip install -r requirements.txt

# 启动后端服务
python -m uvicorn api.main:app --reload --port 8000
```

### 前端安装

```bash
# 进入前端目录
cd frontend

# 安装依赖
npm install

# 启动开发服务器
npm run dev
```

### 访问应用

- 前端界面：http://localhost:5173
- API 文档：http://localhost:8000/docs

## 📁 项目结构

```
Fin/
├── backend/                    # 后端服务
│   ├── api/                   # API 路由
│   │   ├── routes/           # 路由定义
│   │   │   ├── data.py       # 数据接口
│   │   │   ├── trading.py    # 交易接口
│   │   │   └── history.py    # 历史记录接口
│   │   ├── models/           # 数据模型
│   │   └── main.py           # 应用入口
│   ├── data_engine/          # 数据引擎
│   │   ├── fetchers/         # 数据获取
│   │   ├── storage/          # 数据存储
│   │   └── engine.py         # 引擎核心
│   ├── backtest_engine/      # 回测引擎
│   │   ├── strategies/       # 交易策略
│   │   ├── portfolio/        # 投资组合
│   │   ├── metrics/          # 性能指标
│   │   └── engine.py         # 回测引擎
│   ├── trading_engine/       # 交易引擎
│   │   └── brokers/          # 券商接口
│   ├── strategy/             # 策略模块
│   │   └── indicators.py     # 技术指标
│   ├── scripts/              # 工具脚本
│   └── data/                 # 数据库文件
├── frontend/                  # 前端应用
│   ├── src/
│   │   ├── pages/           # 页面组件
│   │   ├── components/      # UI 组件
│   │   ├── services/        # API 服务
│   │   ├── types/           # TypeScript 类型
│   │   └── App.tsx          # 应用根组件
│   └── public/              # 静态资源
└── README.md                 # 项目文档
```

## 💡 使用示例

### 1. 获取股票数据

```bash
# 使用内置脚本获取所有A股数据
cd backend
python scripts/fetch_all_a_shares.py
```

### 2. 创建回测任务

通过前端界面：
1. 进入"回测"页面
2. 选择股票代码（如 000001.SZ）
3. 设置日期范围和初始资金
4. 选择策略（MACD、KDJ、RSI 等）
5. 点击"开始回测"

### 3. Paper Trading

1. 进入"交易"页面
2. 输入股票代码和数量
3. 选择买入/卖出
4. 输入价格或使用市价
5. 提交订单

### 4. 查看历史信号

1. 进入"仪表盘"
2. 查看最近生成的交易信号
3. 点击"查看更多"查看详细统计

## 🔧 配置说明

### 数据库配置

数据库文件位于 `backend/data/market.db`，包含以下表：

- `daily_quotes`: 日线行情数据
- `stock_info`: 股票基本信息
- `signals`: 交易信号记录
- `backtest_tasks`: 回测任务
- `backtest_results`: 回测结果
- `orders`: 订单记录
- `trades`: 成交记录

### 策略参数配置

在创建回测任务时，可以通过 `strategy_params` 自定义策略参数：

```json
{
  "fast_period": 5,
  "slow_period": 20,
  "position_size": 0.95
}
```

## 📊 性能指标说明

| 指标 | 说明 |
|------|------|
| 总收益率 | (期末资产 - 期初资产) / 期初资产 |
| 年化收益率 | ((1 + 总收益率) ^ (252 / 交易日数)) - 1 |
| 夏普比率 | (年化收益率 - 无风险利率) / 年化波动率 |
| 最大回撤 | 从峰值到谷底的最大跌幅 |
| 胜率 | 盈利交易次数 / 总交易次数 |
| 盈亏比 | 总盈利 / 总亏损 |

## 🐛 常见问题

### Q: 网络代理错误导致无法获取数据？
A: 可以使用已保存的历史数据，或配置代理环境变量。

### Q: 回测资产曲线是平线？
A: 检查日期范围是否有数据，策略是否产生了交易信号。

### Q: Paper Trading 下单失败？
A: 确保输入了价格（限价单），或使用 `update_price` 接口更新市场价格。

## 🔮 未来规划

- [ ] 支持更多交易所和市场（港股、美股）
- [ ] 实时行情推送（WebSocket）
- [ ] 更多技术指标和策略
- [ ] 策略优化工具（参数扫描）
- [ ] 风险管理模块
- [ ] 多账户管理
- [ ] 实盘对接

## 🤝 贡献指南

欢迎提交 Issue 和 Pull Request！

## 📄 开源协议

MIT License

## 👨‍💻 作者

Built with ❤️ by cccjson

---

**注意**：本系统仅供学习和研究使用，不构成任何投资建议。实际交易请谨慎，风险自负。

# Fin — 个人量化交易平台

> 一个面向 A股、港股、美股 的全功能量化交易系统，集数据获取、技术分析、策略回测、AI 策略生成、模拟交易于一体。

![Version](https://img.shields.io/badge/version-4.0.0-brightgreen.svg)
![Python](https://img.shields.io/badge/python-3.12+-blue.svg)
![React](https://img.shields.io/badge/react-19-blue.svg)
![License](https://img.shields.io/badge/license-MIT-blue.svg)

---

## 功能模块

### 数据引擎
- 多市场数据获取：A股（AkShare / Tushare）、港股 & 美股（YFinance）
- SQLite 本地持久化，支持日线 / 周线 / 月线
- 两阶段智能加载：本地快速查询（毫秒级）+ 后台网络补齐（3 天新鲜度容忍）
- 增量更新，自动去重，异常值清洗
- APScheduler 定时任务，收盘后自动拉取

### 技术分析引擎
覆盖 4 大类、16+ 技术指标：

| 类别 | 指标 |
|------|------|
| 趋势 | MA、EMA、MACD、ADX、DMI |
| 动量 | RSI、KDJ、CCI、ROC、Williams %R |
| 波动 | Bollinger Bands、ATR、Keltner Channel、Donchian Channel |
| 成交量 | OBV、VWAP、MFI、成交量比率 |

信号检测规则（内置可配置）：

| 信号 | 触发条件 |
|------|---------|
| 均线金叉 / 死叉 | MA5 与 MA20 上穿 / 下穿 |
| MACD 交叉 | DIF 上穿 / 下穿 DEA |
| RSI 超买超卖 | RSI < 30 回升为买，> 70 回落为卖 |
| 布林带 | 价格触及上下轨回弹 |
| 放量突破 | 突破阻力位 + 成交量 > 2 倍均量 |
| K 线形态 | 锤子线、吞没形态、早晨之星等 |
| 多信号共振 | ≥ 3 个信号同时触发才开仓 |

### 回测引擎
- 初始资金可配置（默认 100 万）
- 手续费模拟：A股万2.5 佣金 + 千1 印花税（卖出）
- 滑点模拟：买入加点，卖出减点
- 仓位管理：固定金额、固定比例、Kelly 公式
- 完整绩效指标：总收益率、年化收益率、夏普比率、索提诺比率、最大回撤、胜率、盈亏比
- 结果可视化：资金曲线、回撤曲线、交易记录

### Paper Trading 模拟交易
- 完整模拟真实交易流程，遵守 A 股 T+1 规则
- 市价单 / 限价单，订单状态全程跟踪
- 持仓管理：成本价、浮盈浮亏实时计算
- WebSocket 实时推送账户与订单更新

### AI 策略生成（Alpha Lab）
- 描述策略意图，LLM 自动编写 Python 策略代码
- 沙盒安全执行，自动评分（夏普比率 + 过拟合检测）
- 两阶段迭代优化：Explore（探索）→ Refine（精炼）
- 策略版本管理，支持历史对比

### AI 财经顾问
- 聚合财经新闻（Finnhub API）+ 情绪分析
- 结合持仓状态和市场行情，生成个性化投资建议
- 支持 OpenAI API 及本地 MLX 模型（Apple Silicon 加速）

### 价格预测引擎
- 多模型集成：LSTM、XGBoost、Ensemble
- 特征工程：技术指标 + 历史价格序列
- 预测结果可视化，置信度展示

### 自动化交易控制
- 自动化任务调度（APScheduler，支持 cron）
- 待成交订单管理：标记、冻结资金、自动撤销
- 风控硬限（不可绕过）：
  - 单股最大仓位 ≤ 20%
  - 单日最大亏损 ≤ 3%，触发自动停止
  - 总持仓不超过 80%，保留现金缓冲
  - 每笔交易必须设置止损（默认 -5%）
  - 连续亏损 3 次后暂停交易 1 天

### 模型微调（Fine-tuning）
- 支持本地和远程 GPU（SSH 隧道）
- 构建量化交易专属数据集
- 微调进度实时查看

---

## 技术栈

### 后端
| 组件 | 技术 |
|------|------|
| Web 框架 | FastAPI 0.109 + Uvicorn |
| 数据库 | SQLite + SQLAlchemy 2.0 ORM |
| 数据源 | AkShare、Tushare、YFinance |
| 任务调度 | APScheduler 3.10 |
| AI | OpenAI API + 本地 MLX（Apple Silicon）|
| 日志 | Loguru |
| 运行环境 | Python 3.12+ / Conda `quant` |

### 前端
| 组件 | 技术 |
|------|------|
| 框架 | React 19 + TypeScript |
| 构建工具 | Vite 7 |
| K 线图表 | Lightweight Charts 5（TradingView 开源）|
| 统计图表 | Recharts 3 |
| 样式 | Tailwind CSS 4 |
| 状态管理 | Zustand 5 |
| 路由 | React Router 7 |
| HTTP | Axios |

---

## 快速开始

### 环境要求

- Python 3.12+（推荐通过 Conda 管理）
- Node.js 18+
- Conda（必须，所有 Python 命令在 `quant` 环境下运行）

### 一键部署（推荐）

```bash
# macOS / Linux
bash deploy.sh

# Windows
deploy.bat
```

脚本会自动创建 `quant` Conda 环境、安装所有依赖、构建前端。

### 手动安装

```bash
# 1. 创建并激活 Conda 环境
conda create -n quant python=3.12 -y
conda activate quant

# 2. 安装后端依赖
cd backend
pip install -r requirements.txt

# 3. 安装前端依赖
cd ../frontend
npm install
```

### 配置环境变量

复制并编辑 `.env` 文件：

```bash
cd backend
cp .env.example .env
```

关键配置项：

```env
# 数据源
TUSHARE_TOKEN=your_token_here

# AI 功能
OPENAI_API_KEY=your_key_here

# 财经新闻
FINNHUB_API_KEY=your_key_here

# 本地模型（Apple Silicon 可用）
ALPHA_LAB_PROVIDER=local
ALPHA_LAB_LOCAL_MODEL=mlx-community/Qwen2.5-Coder-14B-Instruct-4bit
ALPHA_LAB_LOCAL_BASE_URL=http://localhost:11434/v1

# 登录认证
AUTH_USERNAME=Jason
AUTH_PASSWORD=your_password
JWT_SECRET=your_jwt_secret

# 实盘对接（可选，国金 QMT）
QMT_BRIDGE_URL=http://127.0.0.1:5100
QMT_ACCOUNT_ID=your_account_id
```

### 启动服务

```bash
# macOS / Linux（一键启动前后端）
bash start.sh

# Windows
start.bat
```

或手动分别启动：

```bash
# 后端（在项目根目录）
conda run -n quant python -m uvicorn api.main:app --host 0.0.0.0 --port 8000

# 前端（在 frontend 目录）
npm run dev
```

**Apple Silicon 用户**（使用本地 MLX 模型）：

```bash
# 启动前需要先启动 MLX 服务
bash backend/start_mlx_server.sh
```

### 访问

| 服务 | 地址 |
|------|------|
| 前端界面 | http://localhost:5173 |
| API 文档（Swagger）| http://localhost:8000/docs |
| API 文档（ReDoc）| http://localhost:8000/redoc |

---

## 项目结构

```
Fin/
├── backend/
│   ├── api/
│   │   ├── main.py                 # FastAPI 应用入口
│   │   └── routes/                 # API 路由（18 个模块）
│   │       ├── data.py             # 行情数据接口
│   │       ├── analysis.py         # 技术分析接口
│   │       ├── backtest.py         # 回测接口
│   │       ├── trading.py          # 交易接口
│   │       ├── signal_generation.py
│   │       ├── alpha_lab.py        # AI 策略生成接口
│   │       ├── advisor.py          # AI 顾问接口
│   │       ├── prediction.py       # 价格预测接口
│   │       ├── news.py             # 新闻接口
│   │       ├── automation.py       # 自动化控制接口
│   │       ├── fine_tune.py        # 模型微调接口
│   │       ├── portfolio.py        # 组合管理接口
│   │       ├── report.py           # 报告生成接口
│   │       └── ws.py               # WebSocket
│   ├── data_engine/                # 数据引擎
│   │   ├── fetchers/               # 多源数据适配器（AkShare / Tushare / YFinance）
│   │   ├── storage/                # ORM 模型与数据库管理
│   │   ├── processors/             # 数据清洗与标准化
│   │   └── daily_updater.py        # 增量更新任务
│   ├── analysis_engine/            # 技术分析引擎
│   │   ├── indicators/             # 指标库（趋势 / 动量 / 波动 / 成交量）
│   │   ├── signals/                # 信号检测（金叉 / MACD / RSI / 共振）
│   │   └── patterns/               # K 线形态识别
│   ├── backtest_engine/            # 回测引擎
│   │   ├── strategies/             # 内置策略（均线 / MACD / 信号策略）
│   │   ├── portfolio/              # 仓位管理 & 资金曲线
│   │   └── metrics/                # 绩效指标计算
│   ├── trading_engine/             # 交易引擎
│   │   ├── brokers/                # Paper Trading & QMT 实盘适配器
│   │   └── risk/                   # 风控模块（硬编码，不可覆盖）
│   ├── alpha_lab/                  # AI 策略生成
│   │   ├── engine.py               # 迭代优化引擎
│   │   ├── code_generator.py       # LLM 代码生成
│   │   ├── evaluator.py            # 策略评分
│   │   └── sandbox.py              # 沙盒执行环境
│   ├── advisor_engine/             # AI 财经顾问
│   ├── prediction_engine/          # 价格预测（LSTM / XGBoost）
│   ├── news_engine/                # 新闻聚合 & 情绪分析
│   ├── automation/                 # 自动化调度 & WebSocket 管理
│   ├── finetune/                   # 模型微调（本地 & 远程 GPU）
│   ├── scripts/                    # 工具脚本
│   │   └── fetch_all_a_shares.py  # 一键拉取全量 A 股数据
│   ├── data/
│   │   └── market.db              # SQLite 数据库
│   └── requirements.txt
├── frontend/
│   └── src/
│       ├── pages/                  # 19 个功能页面
│       │   ├── Dashboard.tsx       # 账户总览仪表盘
│       │   ├── Market.tsx          # K 线图 & 行情分析
│       │   ├── Trading.tsx         # Paper Trading 交易页
│       │   ├── Backtest.tsx        # 回测管理
│       │   ├── Signals.tsx         # 信号历史
│       │   ├── SignalTracking.tsx  # 信号追踪仪表盘
│       │   ├── Portfolio.tsx       # 投资组合管理
│       │   ├── AlphaLab.tsx        # AI 策略生成
│       │   ├── Advisor.tsx         # AI 投资顾问
│       │   ├── Prediction.tsx      # 价格预测
│       │   ├── News.tsx            # 财经新闻聚合
│       │   ├── Automation.tsx      # 自动化控制
│       │   ├── FineTune.tsx        # 模型微调
│       │   ├── Reports.tsx         # 回测报告
│       │   ├── Realtime.tsx        # 实时行情
│       │   ├── Review.tsx          # 策略审查
│       │   ├── OrderBook.tsx       # 订单簿
│       │   ├── DataPipeline.tsx    # 数据管道监控
│       │   └── Home.tsx            # 登录入口
│       ├── components/             # 可复用 UI 组件
│       ├── services/               # API 调用层（18 个服务）
│       ├── stores/                 # Zustand 全局状态
│       └── types/                  # TypeScript 类型定义
├── deploy.sh / deploy.bat          # 一键部署脚本
├── start.sh / start.bat            # 一键启动脚本
└── CLAUDE.md                       # 开发规范与架构说明
```

---

## API 速览

### 数据接口
```
POST /data/daily          # 获取日线数据（支持本地缓存模式）
GET  /data/stocks         # 获取股票列表
POST /data/update         # 手动触发数据更新
```

### 技术分析
```
POST /analysis/indicators # 计算技术指标
POST /analysis/signals    # 生成买卖信号
POST /analysis/patterns   # 识别 K 线形态
```

### 回测
```
POST /backtest/run        # 运行策略回测
GET  /backtest/strategies # 查看支持的策略列表
```

### 模拟交易
```
POST /trading/order       # 提交订单
GET  /trading/account     # 查看账户信息
GET  /trading/positions   # 查看持仓
GET  /trading/orders      # 查看订单历史
```

### AI 功能
```
POST /alpha_lab/start     # 启动 AI 策略生成
GET  /alpha_lab/progress  # 查看迭代进度（流式）
GET  /alpha_lab/strategies# 查看已生成的策略库
POST /advisor/suggest     # 获取 AI 投资建议
POST /prediction/predict  # 价格预测
GET  /news/latest         # 最新财经新闻
```

完整 API 文档：http://localhost:8000/docs

---

## 数据库表结构

| 表名 | 说明 |
|------|------|
| `daily_quotes` | 日线行情数据（OHLCV）|
| `stock_info` | 股票基本信息 |
| `signals` | 交易信号记录 |
| `backtest_tasks` | 回测任务 |
| `backtest_results` | 回测结果详情 |
| `orders` | 订单记录 |
| `trades` | 成交记录 |

---

## 使用示例

### 拉取全量 A 股数据

```bash
conda run -n quant python backend/scripts/fetch_all_a_shares.py
```

### 运行策略回测

通过前端界面：
1. 进入"回测"页面
2. 选择股票代码（如 `000001.SZ`）
3. 设置日期范围和初始资金
4. 选择策略（MA Cross、MACD、RSI 等）
5. 点击"开始回测"查看绩效报告

或通过 API：

```bash
curl -X POST http://localhost:8000/backtest/run \
  -H "Content-Type: application/json" \
  -d '{
    "symbol": "000001.SZ",
    "start_date": "2023-01-01",
    "end_date": "2024-01-01",
    "initial_capital": 1000000,
    "strategy": "ma_cross",
    "strategy_params": {"fast_period": 5, "slow_period": 20}
  }'
```

### AI 策略生成（Alpha Lab）

1. 进入"Alpha Lab"页面
2. 用自然语言描述策略思路，如：
   > "在 RSI 低于 30 的超卖区间，结合 MACD 金叉信号，分批建仓"
3. 点击"生成策略"，等待 LLM 编写并自动回测
4. 查看迭代优化过程和最终 Sharpe 评分

---

## 绩效指标说明

| 指标 | 计算方式 |
|------|---------|
| 总收益率 | (期末资产 - 期初资产) / 期初资产 |
| 年化收益率 | ((1 + 总收益率) ^ (252 / 交易日数)) - 1 |
| 夏普比率 | (年化收益 - 无风险利率) / 年化波动率 |
| 索提诺比率 | (年化收益 - 无风险利率) / 下行波动率 |
| 最大回撤 | 从峰值到谷底的最大跌幅 |
| 胜率 | 盈利交易次数 / 总交易次数 |
| 盈亏比 | 平均盈利 / 平均亏损 |

---

## 风控规则

以下规则硬编码在 `trading_engine/risk/` 中，**不可被策略代码覆盖**：

- 单股最大仓位 ≤ 总资金 **20%**
- 单日最大亏损 ≤ 总资金 **3%**，触发后自动停止当日交易
- 总持仓上限 **80%**，保留 20% 现金缓冲
- 每笔交易必须设置止损（默认 **-5%**）
- 连续亏损 **3 次**后暂停交易 1 天

---

## 常见问题

**Q: 数据获取失败，提示网络错误？**
A: 检查代理配置（`HTTP_PROXY` / `HTTPS_PROXY` 环境变量），或先使用本地缓存数据（`db_only=true`）。

**Q: Alpha Lab 报错找不到模型？**
A: 本地模式需要先启动 MLX 服务（`bash backend/start_mlx_server.sh`）；或切换为 OpenAI 模式（设置 `ALPHA_LAB_PROVIDER=openai`）。

**Q: Paper Trading 下单失败？**
A: 确认账户已初始化（`POST /trading/init`），并通过 `POST /trading/price/update` 更新当前市价。

**Q: 回测资产曲线是平线？**
A: 检查所选日期范围内是否有行情数据，以及策略参数是否产生了交易信号。

---

## 版本历史

| 版本 | 主要功能 |
|------|---------|
| V4.0.0 | Alpha Lab AI 策略生成、模型微调、数据管道可视化、本地 MLX 支持 |
| V3.0.0 | AI 财经顾问、实时行情 WebSocket、自动化交易调度、C++ 高速回测 |
| V2.1.0 | 界面深度优化、交互体验提升 |
| V2.0.0 | 多设备同局域网访问、Bug 修复 |
| V1.4.1 | 报告生成 Bug 修复 |

---

## 开发规范

- 所有函数必须有 **类型注解**（type hints）
- 文档字符串遵循 **Google style docstring**
- 错误处理：禁止裸 `except`，必须捕获具体异常类型
- 日志：使用 **Loguru**，关键操作必须记录
- 敏感信息（API Key 等）只存 `.env`，不得硬编码

---

## 免责声明

本系统仅供学习研究和策略验证使用，不构成任何投资建议。实盘交易风险自负，请在充分了解市场风险后谨慎操作。

---

## 作者

Built with by cccjson (Jason)

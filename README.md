# Fin — 个人量化交易平台

> 一个面向 **A股 / 港股 / 美股** 的全功能量化交易系统，从数据获取、技术分析、策略回测，到 AI 策略生成、多智能体投研助手、模拟交易，一站式打通。

![Version](https://img.shields.io/badge/version-5.0.0-brightgreen.svg)
![Python](https://img.shields.io/badge/python-3.12+-blue.svg)
![React](https://img.shields.io/badge/react-19-blue.svg)
![FastAPI](https://img.shields.io/badge/FastAPI-0.109-009688.svg)
![License](https://img.shields.io/badge/license-MIT-blue.svg)

---

## 系统架构总览

```
┌──────────────────────────────────────────────────────────────────────────┐
│                          前端 Web (React 19 + Vite 7)                        │
│  K线图表 · 仪表盘 · 回测报告 · MoneyBill 对话 · 决策驾驶舱 · 选股器 · 23 个页面    │
└───────────────────────────────┬──────────────────────────────────────────┘
                  REST API + WebSocket（实时推送 / 流式进度）
┌───────────────────────────────┴──────────────────────────────────────────┐
│                       FastAPI 网关 (api/) · 30+ 路由模块                      │
└───┬────────┬─────────┬─────────┬─────────┬─────────┬──────────┬───────────┘
    │        │         │         │         │         │          │
┌───┴───┐┌───┴────┐┌───┴────┐┌───┴────┐┌───┴─────┐┌──┴──────┐┌──┴────────┐
│ 数据   ││ 分析    ││ 回测    ││ 交易    ││  AI 智能  ││ 决策辅助 ││ 知识/外脑   │
│ 引擎   ││ 引擎    ││ 引擎    ││ 引擎    ││  体系    ││ 引擎    ││ knowledge  │
│data_  ││analysis││backtest││trading ││MoneyBill││cockpit  ││_engine     │
│engine ││_engine ││_engine ││_engine ││alpha_lab││screener ││ RAG 金融   │
│       ││        ││ +C++   ││ +风控   ││advisor  ││watchlist││ 大脑       │
└───┬───┘└────────┘└────────┘└────────┘└─────────┘└─────────┘└────────────┘
    │
┌───┴──────────────────────────────────────────────────────────────────────┐
│   存储层：SQLite (market.db 行情/信号/交易)  +  knowledge.db (向量库 sqlite-vec) │
│   数据源：AkShare · Tushare · YFinance · Finnhub · 快代理            │
└──────────────────────────────────────────────────────────────────────────┘
```

**MoneyBill 多智能体编排（V5 核心）**

```
         用户自然语言提问
                │
        ┌───────▼────────┐    function-calling loop
        │  MoneyBill 主 Agent │  （编排 · 工具调用 · 二次确认）
        └───────┬────────┘
     ┌──────────┼──────────────┬──────────────┐
 ┌───▼───┐  ┌───▼────┐    ┌────▼────┐    ┌────▼─────┐
 │ 工具集 │  │ 子智能体 │    │ Widget  │    │ 风控守门 │
 │ 数据/  │  │deep_stock│    │ 卡片渲染 │    │ 下单确认 │
 │ 分析/  │  │news     │    └─────────┘    └──────────┘
 │ 组合/  │  │report   │
 │ 交易   │  │alpha_lab│
 └────────┘  └─────────┘
```

---

## 功能模块

### 🗄️ 数据引擎 `data_engine`
- 多市场数据获取：A股（AkShare / Tushare）、港股 & 美股（YFinance）
- SQLite 本地持久化，支持日线 / 周线 / 月线
- **两阶段智能加载**：本地毫秒级查询 + 后台网络补齐（3 天新鲜度容忍）
- 增量更新、自动去重、异常值清洗
- APScheduler 定时任务，收盘后自动拉取；快代理慢路径全市场补齐

### 📈 技术分析引擎 `analysis_engine`
覆盖 4 大类、16+ 技术指标：

| 类别 | 指标 |
|------|------|
| 趋势 | MA、EMA、MACD、ADX、DMI |
| 动量 | RSI、KDJ、CCI、ROC、Williams %R |
| 波动 | Bollinger Bands、ATR、Keltner Channel、Donchian Channel |
| 成交量 | OBV、VWAP、MFI、成交量比率 |

内置可配置信号检测：均线金叉/死叉、MACD 交叉、RSI 超买超卖、布林带回弹、放量突破、K 线形态（锤子线/吞没/早晨之星）、**多信号共振（≥3 个同时触发才开仓）**。信号支持历史回补与追踪（盈亏跟踪仪表盘）。

### ⚙️ 回测引擎 `backtest_engine` + `backtest_cpp`
- 初始资金可配置（默认 100 万），手续费 & 滑点真实模拟（A股万2.5 + 千1 印花税）
- 仓位管理：固定金额 / 固定比例 / Kelly 公式
- **C++ 高速回测内核**（`backtest_cpp/`，CMake 构建），批量回测 + 排名
- **Walk-Forward 滚动优化**，对抗过拟合
- 完整绩效指标：总收益率、年化、夏普、索提诺、最大回撤、胜率、盈亏比、基准对比
- 结果可视化：资金曲线、回撤曲线、交易记录表

### 💹 交易引擎 `trading_engine`
- **Paper Trading** 完整模拟，遵守 A 股 T+1，市价/限价单全程跟踪
- 持仓管理：成本价、浮盈浮亏实时计算，WebSocket 推送
- 实盘适配器：国金 QMT Bridge、Mac 自动化
- **风控硬限（硬编码不可绕过）**：单股仓位 ≤20% · 单日亏损 ≤3% 自动停 · 总持仓 ≤80% · 强制止损 -5% · 连亏 3 次停 1 天

### 🤖 MoneyBill 多智能体投研助手 `agents`
- LLM function-calling 编排层，主 Agent **MoneyBill** 自主调度工具与子智能体
- **工具集**：数据查询、技术分析、组合查询、下单（带二次确认守门）
- **子智能体**：`deep_stock`（个股深研）、`news`（新闻）、`report`（报告）、`alpha_lab`（策略）
- **Widget 卡片**：结构化结果以可视化卡片渲染回前端
- 流式输出、用量统计、技能（skills）热加载

### 🧪 AI 策略生成 Alpha Lab `alpha_lab`
- 自然语言描述策略意图 → LLM 自动编写 Python 策略代码
- 沙盒安全执行，自动评分（夏普 + 过拟合检测）
- 两阶段迭代：Explore（探索）→ Refine（精炼），策略版本管理
- 支持 OpenAI API 及本地 **MLX 模型**（Apple Silicon GPU 加速）

### 🧠 外置金融大脑 `knowledge_engine`（RAG）
- 标准 RAG：本地 **bge-m3** 向量化 + ChatGPT 当大脑，独立 `knowledge.db`（sqlite-vec）
- 文档切块（chunker）、嵌入（embedding）、检索（retriever）
- Web 检索：DuckDuckGo、SEC EDGAR；Alpha 灵感提炼（idea_miner）
- 暴露 `search_knowledge` 工具，供 MoneyBill / 顾问调用

### 🎛️ 决策辅助引擎
- **决策驾驶舱 `cockpit_engine`**：多维度聚合打分（aggregator + scorer），一屏看清个股决策依据
- **基本面选股器 `screener`**：多因子批量筛选
- **自选股 + 常驻预警 `watchlist`**：盯盘清单与触发告警

### 🔮 价格预测引擎 `prediction_engine`
- 多模型集成：LSTM、XGBoost、Ensemble
- 特征工程：技术指标 + 历史价格序列，置信度可视化

### 📰 AI 财经顾问 `advisor_engine` + 新闻 `news_engine`
- 聚合财经新闻（Finnhub）+ 情绪分析
- 结合持仓与行情，生成个性化投资建议（OpenAI / 本地 MLX）

### 🎚️ 自动化 & 微调
- **自动化交易控制 `automation`**：APScheduler cron 调度、待成交订单管理（标记/冻结资金/自动撤销）
- **模型微调 `finetune`**：本地 & 远程 GPU（SSH 隧道），构建量化专属数据集，进度实时查看

---

## 技术栈

### 后端
| 组件 | 技术 |
|------|------|
| Web 框架 | FastAPI 0.109 + Uvicorn |
| 数据库 | SQLite + SQLAlchemy 2.0 ORM；knowledge.db（sqlite-vec 向量库）|
| 数据源 | AkShare、Tushare、YFinance、Finnhub |
| 任务调度 | APScheduler 3.10 |
| AI / LLM | OpenAI API + 本地 MLX（Apple Silicon）；function-calling 编排 |
| RAG | bge-m3 嵌入 + sqlite-vec |
| 高速回测 | C++ 内核（CMake） |
| 日志 | Loguru |
| 运行环境 | Python 3.12+ / Conda `quant` |

### 前端
| 组件 | 技术 |
|------|------|
| 框架 | React 19 + TypeScript |
| 构建 | Vite 7 |
| K 线图表 | Lightweight Charts 5（TradingView 开源）|
| 统计图表 | Recharts 3 |
| 样式 | Tailwind CSS 4 |
| 状态管理 | Zustand 5 |
| 路由 | React Router 7 |
| HTTP | Axios |

---

## 快速开始

### 环境要求
- Python 3.12+（推荐通过 Conda 管理）· Node.js 18+
- **Conda（必须，所有 Python 命令在 `quant` 环境下运行）**

### 一键部署（推荐）
```bash
# macOS / Linux
bash deploy.sh
# Windows
deploy.bat
```
脚本会自动创建 `quant` Conda 环境、安装依赖、构建前端。

### 手动安装
```bash
# 1. 创建并激活 Conda 环境
conda create -n quant python=3.12 -y
conda activate quant

# 2. 安装后端依赖
cd backend && pip install -r requirements.txt

# 3. 安装前端依赖
cd ../frontend && npm install
```

### 配置环境变量
```bash
cd backend && cp .env.example .env
```
关键配置项：
```env
# 数据源
TUSHARE_TOKEN=your_token_here
FINNHUB_API_KEY=your_key_here

# AI 功能
OPENAI_API_KEY=your_key_here

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
# 后端（backend 目录下）
conda run -n quant python -m uvicorn api.main:app --host 0.0.0.0 --port 8000
# 前端（frontend 目录下）
npm run dev
```
**Apple Silicon 用户**（本地 MLX 模型）启动前需先开 MLX 服务：
```bash
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
│   │   ├── main.py                 # FastAPI 应用入口（注册 30+ 路由）
│   │   └── routes/                 # API 路由模块
│   │       ├── data.py · analysis.py · backtest.py · backtest_cpp.py
│   │       ├── trading.py · automation.py · portfolio.py
│   │       ├── agent.py            # MoneyBill 多智能体接口
│   │       ├── alpha_lab.py · advisor.py · prediction.py · news.py
│   │       ├── knowledge.py        # RAG 金融大脑接口
│   │       ├── cockpit.py · screener.py · watchlist.py · stock_pools.py
│   │       ├── signal_generation.py · tracking.py · walk_forward.py
│   │       ├── review.py · report.py · realtime.py · orderbook.py
│   │       ├── pipeline.py · fine_tune.py · auth.py · ws.py
│   ├── data_engine/                # 数据引擎：fetchers / storage / processors / 增量更新
│   ├── analysis_engine/            # 技术分析：indicators / signals / patterns
│   ├── backtest_engine/            # 回测：strategies / portfolio / metrics
│   ├── trading_engine/             # 交易：brokers（Paper/QMT）/ risk（硬风控）/ mac_automation
│   ├── agents/                     # 🤖 MoneyBill 多智能体编排
│   │   ├── orchestrator.py         #   function-calling 编排循环
│   │   ├── registry.py · executor.py · context.py · llm_client.py
│   │   ├── tools/                  #   数据 / 分析 / 组合 / 交易 工具
│   │   ├── subagents/              #   deep_stock / news / report / alpha_lab
│   │   ├── skills/ · widgets.py    #   技能热加载 / Widget 卡片
│   ├── alpha_lab/                  # AI 策略生成：engine / code_generator / evaluator / sandbox
│   ├── knowledge_engine/           # 🧠 RAG 金融大脑
│   │   ├── embedding.py · vector_store.py · retriever.py · chunker.py
│   │   ├── ingest/                 #   internal / web 数据源
│   │   ├── websearch/              #   ddg / sec_edgar
│   │   └── idea_miner.py           #   Alpha 灵感提炼
│   ├── cockpit_engine/             # 🎛️ 决策驾驶舱：aggregator / scorer / prompt_builder
│   ├── advisor_engine/             # AI 财经顾问
│   ├── prediction_engine/          # 价格预测（LSTM / XGBoost / Ensemble）
│   ├── news_engine/                # 新闻聚合 & 情绪分析
│   ├── automation/                 # 自动化调度 & 待成交订单管理
│   ├── finetune/                   # 模型微调（本地 & 远程 GPU）
│   ├── scripts/                    # 工具脚本（全量拉取 / 数据补齐）
│   ├── data/market.db              # SQLite 行情数据库
│   └── requirements.txt
├── backtest_cpp/                   # ⚡ C++ 高速回测内核（CMake）
├── frontend/
│   └── src/
│       ├── pages/                  # 23 个功能页面
│       │   ├── Dashboard · Market · Trading · Backtest · Portfolio
│       │   ├── MoneyBill           #   多智能体对话
│       │   ├── Cockpit             #   决策驾驶舱
│       │   ├── Knowledge           #   金融大脑
│       │   ├── Screener · Watchlist · AlphaLab · Advisor · Prediction
│       │   ├── Signals · SignalTracking · News · Automation
│       │   ├── FineTune · Reports · Realtime · Review · OrderBook · DataPipeline
│       ├── components/             # UI 组件（moneybill / charts / backtest / trading-center …）
│       ├── services/ · stores/ · types/
├── deploy.sh / deploy.bat          # 一键部署
├── start.sh / start.bat           # 一键启动
└── CLAUDE.md                       # 开发规范与架构说明
```

---

## API 速览

```
# 数据 / 分析
POST /data/daily              # 日线数据（支持本地缓存模式）
POST /analysis/signals        # 生成买卖信号
POST /analysis/patterns       # 识别 K 线形态

# 回测
POST /backtest/run            # 运行策略回测
POST /walk_forward/run        # Walk-Forward 滚动优化

# 模拟交易
POST /trading/order           # 提交订单
GET  /trading/positions       # 查看持仓

# AI 智能体
POST /agent/chat              # MoneyBill 多智能体对话（流式）
POST /alpha_lab/start         # 启动 AI 策略生成
POST /advisor/suggest         # AI 投资建议
POST /knowledge/search        # RAG 金融大脑检索

# 决策辅助
GET  /cockpit/{symbol}        # 决策驾驶舱评分
POST /screener/run            # 基本面选股
GET  /watchlist               # 自选股 & 预警
```
完整 API 文档：http://localhost:8000/docs

---

## 数据库表结构

| 表名 | 说明 |
|------|------|
| `daily_quotes` | 日线行情数据（OHLCV）|
| `stock_info` | 股票基本信息 |
| `signals` | 交易信号记录 |
| `backtest_tasks` / `backtest_results` | 回测任务与结果 |
| `orders` / `trades` | 订单与成交记录 |
| `knowledge.db`（独立）| RAG 文档块 + bge-m3 向量（sqlite-vec）|

---

## 风控规则

以下规则硬编码在 `trading_engine/risk/` 中，**不可被策略代码覆盖**：

- 单股最大仓位 ≤ 总资金 **20%**
- 单日最大亏损 ≤ 总资金 **3%**，触发后自动停止当日交易
- 总持仓上限 **80%**，保留 20% 现金缓冲
- 每笔交易必须设置止损（默认 **-5%**）
- 连续亏损 **3 次**后暂停交易 1 天

---

## 版本历史

| 版本 | 主要功能 |
|------|---------|
| **V5.0.0** | **MoneyBill 多智能体投研助手、RAG 金融大脑（knowledge_engine）、决策驾驶舱、基本面选股器、自选股预警** |
| V4.x | Alpha Lab AI 策略生成、模型微调、数据管道可视化、本地 MLX 支持、C++ 高速回测 |
| V3.0.0 | AI 财经顾问、实时行情 WebSocket、自动化交易调度 |
| V2.x | 界面深度优化、多设备同局域网访问 |
| V1.x | 数据/分析/回测/交易核心链路、报告生成 |

---

## 常见问题

**Q: 数据获取失败，提示网络错误？**
A: 检查代理配置（`HTTP_PROXY` / `HTTPS_PROXY`），或先用本地缓存（`db_only=true`）。

**Q: Alpha Lab / MoneyBill 报错找不到模型？**
A: 本地模式需先启动 MLX 服务（`bash backend/start_mlx_server.sh`）；或切 OpenAI 模式（`ALPHA_LAB_PROVIDER=openai`）。

**Q: 回测资产曲线是平线？**
A: 检查所选日期范围内是否有行情数据，以及策略参数是否产生了交易信号。

---

## 开发规范

- 所有函数必须有**类型注解**；文档字符串遵循 **Google style docstring**
- 禁止裸 `except`，必须捕获具体异常类型
- 日志使用 **Loguru**，关键操作必须记录
- 敏感信息（API Key 等）只存 `.env`，不得硬编码

---

## 免责声明

本系统仅供学习研究和策略验证使用，不构成任何投资建议。实盘交易风险自负，请在充分了解市场风险后谨慎操作。

---

## 作者

Built with ❤️ by cccjson (Jason)

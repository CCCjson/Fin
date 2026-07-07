# Fin — 个人量化交易平台

> 一个面向 **A股 / 港股 / 美股** 的全功能量化交易系统。以 **MoneyBill** 对话为唯一主入口，自然语言驱动数据获取、技术分析、策略回测、选股推荐、模拟交易、决策留痕，配 7 个可视化工作台页做深度查看，桌面端有原生 Tauri 壳。

![Version](https://img.shields.io/badge/version-6.0.0-brightgreen.svg)
![Python](https://img.shields.io/badge/python-3.12+-blue.svg)
![React](https://img.shields.io/badge/react-19-blue.svg)
![FastAPI](https://img.shields.io/badge/FastAPI-0.109-009688.svg)
![License](https://img.shields.io/badge/license-MIT-blue.svg)

---

## 系统架构总览

2026-07 完成的架构收敛（Phase 1-7）把系统从「23 个独立页面 + 30+ 路由」收拢成「MoneyBill 对话主入口 + 7 个可视化工作台 + 20 个瘦身路由」，绝大多数业务能力下沉为 agent 可调用的工具，不再单独开页面/接口。

```
┌──────────────────────────────────────────────────────────────────────────┐
│              前端 (React 19 + Vite 7)  ·  Web / Tauri 桌面壳               │
│   MoneyBill 对话（主入口，全屏 + 浮窗两态）                                    │
│   7 个工作台：K线行情 · 策略回测 · 股价预测 · 订单簿模拟 · 数据监控 · 自动化交易 · 模型微调  │
└───────────────────────────────┬──────────────────────────────────────────┘
        JWT 鉴权 REST API + WebSocket（实时推送 / NDJSON 流式对话）
┌───────────────────────────────┴──────────────────────────────────────────┐
│                  FastAPI 网关 (api/) · 20 个路由模块                        │
│   仅剩薄 HTTP 封装：data/analysis/realtime/backtest_cpp/prediction/news/    │
│   automation/screener/knowledge/stock_pools/data_monitor/deep_history/…   │
└───┬────────┬─────────┬─────────┬──────────┬──────────┬──────────┬────────┘
    │        │         │         │          │          │          │
┌───┴───┐┌───┴────┐┌───┴────┐┌───┴─────┐┌───┴──────┐┌──┴──────┐┌──┴────────┐
│ 数据   ││ 分析    ││ 回测    ││ 交易/风控 ││ MoneyBill ││ 决策辅助 ││ 知识/外脑   │
│ engine││ engine ││ C++ 内核││ +风控守门 ││ 多智能体   ││ cockpit ││ knowledge  │
│deep_  ││analysis││backtest│││trading_ ││agents/   ││screener ││ _engine    │
│history││_engine ││_cpp    │││engine   ││tools×21  ││limit_up ││ RAG+爬虫    │
│限价    ││        ││orderbook││risk硬限 ││subagents ││decision ││ 工具        │
│池      ││        ││_simulator││       ││          ││_log留痕 ││            │
└───┬───┘└────────┘└────────┘└─────────┘└──────────┘└─────────┘└────────────┘
    │
┌───┴──────────────────────────────────────────────────────────────────────┐
│  存储：market.db (SQLite, 行情/信号/交易) + knowledge.db (sqlite-vec 向量库)  │
│  代理：net/ 代理池 (proxy_pool/proxy_manager) + business_events 业务事件总线  │
│  数据源：AkShare · Tushare · YFinance · Finnhub · pytdx · 快代理             │
└──────────────────────────────────────────────────────────────────────────┘
```

**MoneyBill 多智能体编排**

```
         用户自然语言提问（Web 全屏 / 浮窗，Tauri 桌面同源）
                    │
           ┌────────▼─────────┐   NDJSON 流式 function-calling loop
           │  MoneyBill 主 Agent │   （orchestrator._loop：Reason → Act → Observe）
           └────────┬─────────┘
     ┌───────────────┼────────────────┬─────────────────┬─────────────────┐
 ┌───▼────┐    ┌─────▼──────┐   ┌─────▼─────┐     ┌──────▼──────┐   ┌──────▼─────┐
 │ 21 类  │    │ 确认门      │   │ Widget    │     │ TurnMonitor │   │ 导航工具    │
 │ 工具组  │    │ confirm_gate│   │ 10 种卡片  │     │ 质量监控+   │   │ open_page  │
 │(按需加载)│    │ 悬空自动作废 │   │ 结构化渲染 │     │ 阶梯干预    │   │ 白名单收口  │
 └────────┘    └────────────┘   └───────────┘     └─────────────┘   └────────────┘
```

---

## 功能模块

### 💰 MoneyBill 多智能体投研助手 `agents`（唯一主入口）
- NDJSON 流式 `/agent/chat`，function-calling 编排循环（`orchestrator.py`）
- **21 类工具组**按需加载（`tool_groups.py`）：数据、分析、行情、回测、组合、交易下单、自选股预警、决策驾驶舱、涨停池、选股推荐、知识检索、设置等，首轮只带核心工具，token 地板从 9.7K 降到 4.4K
- **二次确认守门**（`confirm_gate.py`）：下单/改自选/改预警/改设置等 7 类工具强制走确认，`tool_call_id` 精确点名防重放/防绕过；悬空未响应的确认在下一轮对话自动作废，不会拖垮会话历史
- **Widget 卡片**：结构化结果渲染为 10 种前端卡片（行情、持仓、驾驶舱评分、涨停池等）
- **TurnMonitor**：工具调用质量判定 + 阶梯干预（重复拦截/纠偏/token 软线/熔断），前端悬浮监控面板可看轨迹
- **导航工具** `open_page`：白名单收口到 7 个可视化工作台，旧页面（信号/自选/复盘/报告/顾问/驾驶舱等）全部收敛为直接对话回答，不再跳页
- 会话落盘持久化 + trace 留痕，支持断线重连续跑

### 🗄️ 数据引擎 `data_engine`
- 多市场数据获取：A股（AkShare / Tushare / pytdx）、港股 & 美股（YFinance）
- 深度历史补齐 `deep_history/`（A股/海外分别建任务）、财务数据补齐、涨停行情 `limit_up.py`
- 两阶段智能加载：本地毫秒级查询 + 后台网络补齐；4 worker 并发慢路径全市场补齐约 12 分钟
- 全市场实时行情走 `net/` 代理池并发拉取（5000+ 只股票秒级完成）
- APScheduler 定时任务（`daily_pipeline_scheduler.py`），收盘后自动拉取 → 补信号 → 更新追踪

### 📈 技术分析引擎 `analysis_engine`
覆盖趋势（MA/EMA/MACD/ADX）、动量（RSI/KDJ/CCI/Williams %R）、波动（Bollinger/ATR）、成交量（OBV/VWAP）等指标；内置均线金叉死叉、MACD 交叉、RSI 超买超卖、放量突破、K 线形态识别、多信号共振检测。

### ⚙️ 回测引擎 `backtest_cpp`（C++ 高速内核）
- **C++ 是唯一在用的正版回测引擎**（`backtest_cpp/`，CMake + cpp-httplib，独立服务跑在 8002 端口），Python 版 `backtest_engine/` 保留部分指标计算复用
- 8 种内置策略（均线交叉/动量/MACD/RSI 等），手续费+印花税真实模拟，仓位管理支持固定/比例/Kelly
- 单次 / 批量 / Walk-Forward 滚动优化三种模式，结果自动落库对比

### 🎛️ 决策辅助引擎
- **决策驾驶舱 `cockpit_engine`**：五维体检（技术/基本面/情绪/ML/持仓）聚合打分，通过 `get_cockpit_score` 工具在对话中回答（原独立页面已收敛）
- **基本面选股器 `screener_engine`**：多因子批量筛选，`recommend_stocks` 工具做持仓分流 + 三重闸门推荐
- **涨停引擎 `limit_up_engine`**：涨停候选池、打分、盘中扫描
- **决策留痕 `decision_log.py`**：AI 建议（advisor/cockpit/MoneyBill）持久化归因，可复现推理链路

### 💹 交易与风控 `trading_engine` + `automation`
- Paper Trading 完整模拟盘，遵守 A 股 T+1；订单审批、待成交管理、执行历史
- **风控硬限（`trading_engine/risk/`，代码硬编码不可被策略覆盖）**：单股仓位 ≤20% · 单日亏损限额触发即停 · 强制止损 -5% · 连续亏损 3 次暂停交易 1 天
- 常驻监控：价格预警 `price_alert_monitor` + 持仓止损守护 `position_guardian` + 涨停盘中扫描，均开机自启
- 业务事件总线 `business_events.py` → WebSocket 实时推送到前端

### 🧠 外置金融大脑 `knowledge_engine`（RAG）
- 本地 **bge-m3** 向量化 + LLM 当大脑，独立 `knowledge.db`（sqlite-vec）
- 摄入源：arXiv、cninfo 公告、研报、SEC EDGAR、DuckDuckGo 网页检索
- 内置逆向 API 爬虫工具（`browser/` discover/reverse/sniff）+ 浏览器自动化登录态管理
- 暴露 `search_knowledge` 工具供 MoneyBill 直接调用

### 🔮 价格预测引擎 `prediction_engine`
多模型集成（LSTM / XGBoost / Ensemble），本地或远程 GPU（SSH 隧道）训练，特征工程含技术指标 + 历史价格序列。

### 🧪 AI 策略生成 Alpha Lab `alpha_lab`
自然语言描述策略意图 → LLM 编写 Python 策略代码，沙盒安全执行（AST 逃逸防护）+ 自动评分，Explore→Refine 两阶段迭代，支持 OpenAI API 及本地 MLX 模型（Apple Silicon GPU 加速）。

### 📰 新闻与顾问 `news_engine` + `advisor_engine`
定时抓取国内外财经新闻 + 情绪分析（默认 15 分钟一轮），结合持仓与行情生成个性化投研建议，人设+规则共享一套 prompt 积木（`advisor_engine/prompt_builder.py`）。

### 🎚️ 自动化 & 微调
- **模型微调 `finetune`**：本地 & 远程 GPU（SSH 隧道），流式训练进度，断线可续接
- **桌面壳（Tauri v2）**：`frontend/src-tauri/`，macOS 原生窗口，开 App 自动起后端服务、关窗常驻

---

## 技术栈

### 后端
| 组件 | 技术 |
|------|------|
| Web 框架 | FastAPI 0.109 + Uvicorn |
| 数据库 | SQLite + SQLAlchemy 2.0 ORM；knowledge.db（sqlite-vec 向量库）|
| 数据源 | AkShare、Tushare、pytdx、YFinance、Finnhub |
| 任务调度 | APScheduler 3.10 |
| AI / LLM | OpenAI 兼容 API + 本地 MLX（Apple Silicon）；function-calling 编排 |
| RAG | bge-m3 嵌入 + sqlite-vec |
| 高速服务 | C++ 回测内核 + C++ 订单簿模拟器（均 CMake 构建） |
| 鉴权 | JWT（HS256），全局强制，弱密钥启动即拒绝 |
| 日志 | Loguru |
| 运行环境 | Python 3.12+ / Conda `quant` |

### 前端
| 组件 | 技术 |
|------|------|
| 框架 | React 19 + TypeScript |
| 构建 | Vite 7 |
| 桌面壳 | Tauri v2（Rust） |
| K 线图表 | Lightweight Charts 5（TradingView 开源）|
| 统计图表 | Recharts 3 |
| 样式 | Tailwind CSS 4 |
| 状态管理 | Zustand 5 |
| 路由 | React Router 7 |
| HTTP | Axios（自动带 JWT） |

---

## 快速开始

### 环境要求
- Python 3.12+（通过 Conda 管理）· Node.js 18+ · CMake（编译 C++ 服务）
- **Conda（必须，所有 Python 命令在 `quant` 环境下运行）**

### 一键启动（推荐）
```bash
bash restart.sh
```
按顺序拉起：C++ 订单簿服务（8001）→ C++ 回测服务（8002）→ 后端（8000，`--reload`）→ 前端（vite dev，5174），逐项轮询端口/HTTP 确认就绪（后端约 20-40s），日志落在 `/tmp/fin-*.log`。

### 手动安装
```bash
# 1. 创建并激活 Conda 环境
conda create -n quant python=3.12 -y
conda activate quant

# 2. 安装后端依赖
cd backend && pip install -r requirements.txt

# 3. 安装前端依赖
cd ../frontend && npm install

# 4. 编译 C++ 服务
cd ../backtest_cpp && mkdir -p build && cd build && cmake .. -DCMAKE_BUILD_TYPE=Release && make
cd ../../orderbook_simulator && mkdir -p build && cd build && cmake .. && make
```

### 配置环境变量
```bash
cd backend && cp .env.example .env
```
关键配置项：
```env
# 鉴权（必须，弱密钥/默认值会拒绝启动）
JWT_SECRET=your_strong_random_secret     # openssl rand -hex 32
AUTH_ENFORCE=on
AUTH_USERNAME=your_username
AUTH_PASSWORD=your_password

# 数据源
TUSHARE_TOKEN=your_token_here
FINNHUB_API_KEY=your_key_here

# AI 功能（MoneyBill / Alpha Lab / 知识库）
OPENAI_API_KEY=your_key_here
OPENAI_BASE_URL=https://api.openai.com/v1

# 本地模型（Apple Silicon 可用）
ALPHA_LAB_PROVIDER=local
ALPHA_LAB_LOCAL_MODEL=mlx-community/Qwen2.5-Coder-14B-Instruct-4bit
```

### 手动分别启动（不用 restart.sh 时）
```bash
# 后端（backend 目录下）
conda run -n quant python -m uvicorn api.main:app --host 0.0.0.0 --port 8000
# 前端（frontend 目录下）
npm run dev
# C++ 回测服务
./backtest_cpp/build/backtest_server
# C++ 订单簿服务
./orderbook_simulator/build/orderbook_server
```
**Apple Silicon 用户**（本地 MLX 模型）需另起 MLX 服务：`bash backend/start_mlx_server.sh`（端口 11434）。

### 桌面端（Tauri）
```bash
cd frontend
npm run app:dev     # 开发模式
npm run app:build   # 打包 .app / .dmg
```

### 访问
| 服务 | 地址 |
|------|------|
| 前端界面 | http://localhost:5174 |
| API 文档（Swagger）| http://localhost:8000/docs |
| C++ 回测服务 | http://localhost:8002 |
| C++ 订单簿服务 | http://localhost:8001 |

首次访问需登录（`POST /auth/login` 换 JWT），前端已自动处理。

---

## 项目结构

```
Fin/
├── backend/
│   ├── api/
│   │   ├── main.py                 # FastAPI 入口，注册 20 个路由
│   │   ├── deps.py                 # JWT 鉴权依赖
│   │   └── routes/                 # data / analysis / realtime / backtest_cpp / prediction /
│   │       │                       # news / automation / fine_tune / stock_pools / walk_forward /
│   │       │                       # screener / knowledge / auth / ws / agent / data_monitor /
│   │       │                       # deep_history / monitor / history
│   ├── agents/                     # 💰 MoneyBill 多智能体编排（核心层）
│   │   ├── orchestrator.py         #   function-calling 编排循环
│   │   ├── confirm_gate.py         #   二次确认守门
│   │   ├── turn_setup.py           #   turn 开局副作用（悬空确认作废/历史压缩）
│   │   ├── turn_monitor.py         #   工具调用质量监控
│   │   ├── tool_groups.py          #   21 类工具按需加载
│   │   ├── tools/                  #   21 个工具文件（data/trading/watchlist/screener…）
│   │   └── subagents/ · widgets.py
│   ├── data_engine/                # 数据引擎：fetchers / storage / deep_history / stock_pools
│   ├── analysis_engine/            # 技术分析：indicators / signals / patterns
│   ├── backtest_engine/            # Python 回测（指标计算复用，主力已迁移至 C++）
│   ├── trading_engine/             # 风控硬限（risk/）+ 配置
│   ├── automation/                 # 价格预警 / 持仓守护 / 涨停扫描（开机常驻）
│   ├── alpha_lab/                  # AI 策略生成：沙盒 / 评分 / 迭代
│   ├── knowledge_engine/           # 🧠 RAG 金融大脑 + 逆向爬虫工具 + 摄入源
│   ├── cockpit_engine/             # 决策驾驶舱聚合打分（经工具调用，无独立页）
│   ├── screener_engine/            # 基本面选股器
│   ├── limit_up_engine/            # 涨停候选池/打分/扫描
│   ├── recommend_engine/           # 选股推荐
│   ├── advisor_engine/             # AI 顾问 prompt 积木
│   ├── prediction_engine/          # 价格预测（LSTM / XGBoost / Ensemble）
│   ├── news_engine/                # 新闻聚合 & 情绪分析 & 定时任务
│   ├── portfolio/                  # 持仓/对账/交易记录服务
│   ├── net/                        # 代理池（proxy_pool/proxy_manager/domestic/overseas）
│   ├── common/                     # 市场命名单一真源 market.py、沙盒 AST 工具
│   ├── decision_log.py             # 决策留痕
│   ├── business_events.py          # 业务事件总线 → WS 推送
│   ├── finetune/                   # 模型微调（本地 & 远程 GPU）
│   └── requirements.txt
├── backtest_cpp/                   # ⚡ C++ 高速回测内核（CMake，端口 8002）
├── orderbook_simulator/            # ⚡ C++ 订单簿模拟器（CMake，端口 8001）
├── frontend/
│   ├── src/
│   │   ├── pages/                  # 7 个工作台页 + 登录页
│   │   │   ├── Market · Backtest · Prediction · OrderBook
│   │   │   ├── DataMonitor · Automation · FineTune · LoginPage
│   │   ├── components/moneybill/   # MoneyBill 聊天（全屏 ChatThread + 浮窗 FloatingChat）
│   │   ├── components/shell/       # AppShell / MainStage / ToolsDrawer（页面路由与 keep-alive）
│   │   └── services/ · store/ · utils/
│   └── src-tauri/                  # 🖥️ Tauri v2 桌面壳
├── docs/                           # 架构/数据库/API/安全设计文档
├── restart.sh                      # 一键重启全部服务（含就绪检查）
├── deploy.sh / deploy.bat          # 一键部署
└── CLAUDE.md                       # 开发规范与架构说明
```

---

## API 速览

绝大多数业务能力已收敛为 **MoneyBill 工具调用**（通过 `/agent/chat` 自然语言驱动），不再有独立 HTTP 端点。仍保留 HTTP 接口的是数据/图表类只读或长流式接口：

```
# MoneyBill 对话（唯一 AI 入口）
POST /agent/chat               # 流式对话（NDJSON），工具调用+确认门+widget 全走这里
GET  /agent/session/{id}       # 会话回放
GET  /agent/usage              # token 用量

# 数据 / 实时行情
GET  /data/stocks              # 股票列表
POST /data/update-daily/stream # 全市场日线增量更新（流式进度）
GET  /realtime/quotes          # 全市场实时行情
GET  /realtime/indices         # 大盘指数

# 回测（C++ 内核代理）
GET  /backtest_cpp/strategies  # 内置策略清单
POST /backtest_cpp/run         # 单次回测
POST /backtest_cpp/batch       # 批量回测（流式）
POST /walk_forward/run         # Walk-Forward 滚动优化（流式）

# 选股 / 数据监控
POST /screener/run             # 基本面多因子筛选
GET  /data-monitor/overview    # 数据健康看板聚合

# 自动化交易
GET  /automation/pending-orders     # 待审批订单
POST /automation/pending-orders/confirm
GET  /automation/broker-positions

# 鉴权
POST /auth/login               # 换取 JWT
```
完整 API 文档：http://localhost:8000/docs（已下线路由：advisor/alpha_lab/backtest/cockpit/pipeline/portfolio/report/review/signal_generation/tracking/trading/watchlist，功能全部迁移到 agent 工具）

---

## 数据库表结构

| 表名 | 说明 |
|------|------|
| `daily_quotes` | 日线行情数据（OHLCV）|
| `stock_info` | 股票基本信息 |
| `signals` | 交易信号记录 |
| `backtest_batches` / `backtest_results` | 回测批次与结果（C++ 内核产出） |
| `orders` / `trades` | 订单与成交记录 |
| `decision_log` | AI 建议决策留痕（可复现归因） |
| `knowledge.db`（独立） | RAG 文档块 + bge-m3 向量（sqlite-vec） |

`market.db`（约 14GB）与 `logs/` 软链到外置存储；`data/agent_sessions/` 存 MoneyBill 会话落盘。

---

## 风控规则

以下规则硬编码在 `trading_engine/risk/` 中，**不可被策略代码覆盖**：

- 单股最大仓位 ≤ 总资金 **20%**
- 单日最大亏损达到限额，触发后自动停止当日交易
- 每笔交易必须设置止损（默认 **-5%**）
- 连续亏损 **3 次**后建议暂停交易 1 天

MoneyBill 下单类工具（`place_order`、修改自选/预警/设置等）强制走**二次确认门**，风控预检结果会在确认弹窗中一并展示。

---

## 版本历史

| 版本 | 主要变化 |
|------|---------|
| **V6.0.0** | **架构收敛（Phase 1-7）**：MoneyBill 成为唯一主入口，前端 23→7 工作台页，12 个 HTTP 路由退役下沉为 agent 工具；JWT 全局鉴权强制；ToolEnvelope 全量迁移；工具分组按需加载（token 地板降 55%）；确认门悬空自动作废 + 分型渲染；Tauri 桌面壳落地；net/ 代理池独立、C++ 订单簿模拟器上线 |
| V5.0.0 | MoneyBill 多智能体投研助手、RAG 金融大脑（knowledge_engine）、决策驾驶舱、基本面选股器、自选股预警 |
| V4.x | Alpha Lab AI 策略生成、模型微调、数据管道可视化、本地 MLX 支持、C++ 高速回测 |
| V3.0.0 | AI 财经顾问、实时行情 WebSocket、自动化交易调度 |
| V2.x | 界面深度优化、多设备同局域网访问 |
| V1.x | 数据/分析/回测/交易核心链路、报告生成 |

---

## 常见问题

**Q: 数据获取失败，提示网络错误？**
A: 检查 `net/` 代理池配置（`.env` 里的 `HTTP_PROXY_MODE` 等），或先用本地缓存。

**Q: 启动后端直接报错退出？**
A: 大概率是 `JWT_SECRET` 仍为占位值/弱密钥——`AUTH_ENFORCE=on` 时会拒绝启动，换成强随机值（`openssl rand -hex 32`）。

**Q: Alpha Lab / MoneyBill 报错找不到模型？**
A: 本地模式需先启动 MLX 服务（`bash backend/start_mlx_server.sh`）；或切 OpenAI 兼容模式。

**Q: 想看回测报告/自选股/复盘，找不到对应页面？**
A: 这些功能已收敛到 MoneyBill 对话，直接问它即可（如「帮我看看自选股」「回测报告怎么样」），不再有独立页面。

**Q: 回测资产曲线是平线？**
A: 检查所选日期范围内是否有行情数据，以及策略参数是否产生了交易信号。

---

## 开发规范

- 所有函数必须有**类型注解**；文档字符串遵循 **Google style docstring**
- 禁止裸 `except`，必须捕获具体异常类型
- 日志使用 **Loguru**，关键操作必须记录
- 敏感信息（API Key、JWT_SECRET 等）只存 `.env`，不得硬编码
- 新能力只做「引擎层 + 工具层」两层，不再新开独立页面/路由（见 `CLAUDE.md` 架构收敛说明）

---

## 免责声明

本系统仅供学习研究和策略验证使用，不构成任何投资建议。实盘交易风险自负，请在充分了解市场风险后谨慎操作。

---

## 作者

Built with ❤️ by cccjson (Jason)

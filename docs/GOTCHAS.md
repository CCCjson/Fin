# 已知坑位与架构决策记录

> 最后更新：2026-07-07。这份文档回答"为什么这么设计 / 之前踩过什么坑"，配合 `docs/ARCHITECTURE.md`（是什么）和 `docs/FEATURES.md`（有什么）一起看。内容来自项目历史踩坑记录，按主题分类，每条格式：**是什么 → 为什么 → 现在应该怎么做**。

## 架构决策

### 为什么只剩 8 个页面 + MoneyBill 唯一入口
2026-07-03 把前端从 23 页精简到 8 个工作台页面，后端把 33 个 route 模块精简到 21 个（删了 advisor/report/review/watchlist/signal_generation/tracking/alpha_lab/backtest(Python)/portfolio/settings/decisions/trading 共12个）。

**为什么**：同一能力普遍有"页面+route+MoneyBill工具"三份入口，维护成本三倍；MoneyBill 工具全部走进程内直调引擎，route 的 HTTP 壳对 agent 来说是纯旁路，白白多一层。

**怎么做**：新能力只做「引擎 + MoneyBill 工具」两层，别再开独立页面/route，除非是重可视化需求（K线、曲线级别）。删任何现有 route 前先 `grep frontend/src` 确认真的没有页面在消费它。

### 为什么回测有两套引擎
`/backtest_cpp` 是 **C++ 正版**（Jason 日常在用，独立进程跑在8002端口，最低5元佣金/印花税/滑点/Sharpe/Sortino全对），`backend/backtest_engine` 是 **Python 遗留版**。

**为什么留着 Python 版**：`alpha_lab`（让LLM生成任意策略代码在sandbox里跑）依赖它——C++引擎只能跑8个编译内置策略，LLM生成的任意代码无法塞进编译好的C++二进制，所以alpha_lab只能用Python引擎。2026-07已把Python引擎的费用模型、T+1结算、次日开盘成交都打过补丁，与C++版对齐。

**怎么做**：任何回测相关的改动/审计都优先针对 **C++版**；改C++代码后需 `cd backtest_cpp/build && cmake --build .` 重建并**重启 backtest_server**（常驻进程，旧二进制不会自动更新）。不要在 alpha_lab 之外的场景给 Python 版加新功能。

### 市场命名：canonical vs 短写
单一真源 `backend/common/market.py`：canonical = `a_share`/`hk_stock`/`us_stock`。

**已知踩雷点（改 market 传值前必读）**：
- **C++ 回测服务** `backtest_cpp/src/server.cpp` 只认短写 `us`/`hk`，**不认** `us_stock`/`hk_stock`——传 canonical 会 mis-dispatch 成 A股费率。所有发往 C++ 的 body 必须经 `to_cpp_market()` 翻译（已接入 `services/backtest_cpp_client.py`、`api/routes/backtest_cpp.py`）。
- `cninfo_source.py` 里的 `market="沪深京"` **不是**我们的市场枚举，是 akshare 接口本身要求的参数值，**不要乱改**。
- 待拍板：C++ `us_stock` 费率（佣金0.0001/最低1.0）与 Python `_MARKET_FEES["us_stock"]`（0.0/0.0）数值口径不一致，尚未统一。
- 前端还残留四套写法未完全统一：回测层`us/hk`、数据层`hk_stock/us_stock`、已删的MarketSelector组件`hk-stock/us-stock`、OrderBook的`HK/US`。改跨模块market传值时留意接收方到底吃哪种写法。

### MoneyBill token 优化的机制与坑
每轮固定地板从 ~9.7K 降到 ~4.4K tokens，靠5个机制：工具分组按需加载(`tool_groups.py`)、历史两段式压缩(`history.py`)、页面快照去重、monitor.md瘦身、返回结果瘦身。

**怎么用**：
- 回滚开关 `AGENT_TOOL_GROUPS=off` 恢复46工具全量暴露
- **新增工具必须同步归组 `tool_groups.py`，否则首次会话直接抛 RuntimeError**
- `place_order`/`size_position` 必须留在 CORE 组（二次确认续跑依赖它可见）
- 测量工具：`conda run -n quant python backend/scripts/token_bench.py --tag <标签>`

## 安全加固（agents 层，改确认流/并发/沙箱前必读）

- **`_` 前缀参数是服务端专属命名空间**：orchestrator 的 `_sanitize_tool_args` 会剥掉模型提交的所有 `_` 前缀键，`_confirmed` 只能由服务端的 `resume_with_confirmation` 注入。新增确认类工具时不要依赖模型自己传 `_` 键（防伪造）。
- **同 session 单飞**：并发请求同一会话会返回 HTTP 409（`AgentSession.run_lock`）；客户端断开会置位 `cancel_event`，orchestrator 轮首检查后静默停。
- **confirm 必须点名 `tool_call_id`**，不匹配则拒绝且保留 pending。
- **subagent 失败必须 `ok:False`**（契约：`{ok, summary, widgets, tokens}`），走模型自愈路径，子层崩溃有 try/except 兜底不杀主流程。
- **风控键对模型只读**：`settings_tools._RISK_READONLY_KEYS` 黑名单锁死 `max_position_pct`/`total_capital` 等。
- **alpha_lab 沙箱 AST 逃逸（已修，曾是RCE缺口）**：`ASTChecker` 原先没拦 `.__class__.__base__.__subclasses__()` 这类属性链逃逸，subprocess又无OS级隔离，逃逸=用Jason权限RCE。已加 `BANNED_DUNDER_ATTRS` денylist。**沙箱仍非OS级隔离**，改沙箱代码要意识到这一点。
- **alpha_lab max_iterations 已钳制到 [1,20]**（`AlphaLabEngine.start_session`），别去掉这个上限——无美元预算熔断，轮数是唯一烧钱闸门。
- **advisor_engine 是单次流式completion，不是function-calling循环**，`max_tokens=2500`硬顶，无失控风险，deep_stock只调一次。

## 常见 bug 模式

### 常驻后台 job 的"停止中"永久卡死
**症状**：进程实际已停，但前端一直显示"停止中"。

**根因**：`_run()`只有`try/except Exception`没有`finally`——`SystemExit`/线程被杀不会翻转status；`snapshot()`只读内存状态不检查线程是否存活；`stop()`对已死线程不复位状态。

**已修**：`cninfo_job.py`、`research_report_job.py`。**如果 deep_history 的 a-share/overseas job 或 alpha_lab session_manager 复现同症状，直接照抄这套「finally兜底 + snapshot自愈 + stop复位」模式**，不用重新分析——这几处都是"常驻单例+threading.Thread+status字符串状态机"同构模式。

### market 过滤不统一导致的统计失真
**实锤案例**：`DailyUpdater.get_update_status()` 算 `fresh_count` 时两处查询漏了 `market=="a_share"` 过滤，把三市场股票数混进A股分母，导致覆盖率显示301%。

**怎么用**：`DailyQuote`/`StockInfo` 等跨市场共用表的聚合统计，**任何地方分子分母的market过滤条件不对称都可能重演**。审计数据监控看板或类似统计卡片时，先主动查有没有漏过滤市场的聚合查询，不用等截图报数字诡异才发现。

## 网络与代理

- **代理分工**：国外网站(OpenAI/Google)走Clash(7897)；国内网站(eastmoney/akshare)走快代理(Kuaidaili)，两套不能混用。
- **统一网络层 `backend/net/`**（2026-07重构）：`domestic_akshare()`包装akshare调用+快代理轮换+直连兜底；`domestic_get/domestic_json()`东财REST直调。**现在国内抓取Clash开没开都能跑**（快代理不可用会自动直连兜底）。新增裸调akshare/requests的抓取点，应该走这层，不要各自重复造轮子。
- **快代理取IP这一步本身不能依赖Clash**：`fetch_one_proxy()`要用`trust_env=False`的session，否则Clash没开连取代理IP都失败。
- **重分页接口扛不住持续负载**：像`stock_hk_spot_em`这类要翻40+页的接口，换多轮代理IP依然在头几页就断连——不是IP被封，是动态住宅IP本身超时率高，扛不住对同一host的高频请求。**已根治方案**：绕开akshare包装，直接分页调用东财原始接口，每页独立走`domestic_json`按页级重试，不要指望`domestic_akshare`那种"整函数失败重来"的重试策略。
- **快代理是按订单计费的**，订单到期会全部失败，报错码-131/-102；续费/换订单只需改`.env`里的secret_id+signature，**重启后端**生效（改.env运行中不生效）。

## 前端/桌面端

- **Tauri桌面App里外链必须用 `openExternal`**，不能直接靠`<a target="_blank">`原生行为——WKWebView会静默丢弃点击。修法是装 `tauri-plugin-opener` 插件（注册后朴素`<a>`就能用，不要写JS click拦截helper，会和插件自带的全局拦截器冲突）。**壳改动（Cargo.toml/lib.rs/capabilities）必须重打包才生效**，HMR对Rust插件不起作用。
- **app 端 / web 端双后端进程架构（2026-07落地）**：app 是稳定实例（`desktop/start-services.sh` 起，:8000，不带 `--reload`，连生产库，跑三个定时任务），web 是开发实例（`restart.sh` 起，:8010，带 `--reload`，`FIN_DISABLE_SCHEDULERS=true` 关掉定时任务，`DATABASE_URL`/`KNOWLEDGE_DB_PATH` 指向 `backend/data_dev/` 下的本地空测试库，不碰 T9 生产数据）。app 前端加载真实 `vite build` 产物（`tauri.conf.json` 的 `frontendDist: "../dist"`），不再有中间跳转页也没有 HMR；web 前端不变，vite dev(:5174) proxy 到 8010。改代码要让 app 看到，必须显式跑 `desktop/sync-to-app.sh` promote（重新 `npm run build` + `tauri build` + ditto 覆盖安装 `/Applications/Fin.app`）。两个后端进程的 SQLite 访问本身安全（WAL+busy_timeout，且各连各的库），但**进程内单例状态（`agents/context.py` 会话锁、`business_events` 事件总线等）两边不共享**，web 端的会话/模拟盘状态不代表 app 端真实状态，这是预期的沙箱隔离，不是 bug。配套引入长期分支 `app`（只被 `sync-to-app.sh` 用 `git branch -f` 移动，不手动 commit），`git log app..master` 可以看还有哪些改动没推到 app。
- **路由前缀是 `/app/market` 不是 `/market`**——`MainStage`的`PAGES`映射只认`/app/*`，访问裸路径会静默回退到MoneyBill聊天首页（不报错，排查导航问题时留意）。
- **港股代码是5位数字**（`00700.HK`），手输容易漏位，靠`StockSymbolInput`自动补全能避开。
- **货币符号/展示字段要跟"实际展示的数据"同步派生**，不要跟实时输入框state同步派生——否则会出现"标签先跳新市场、数字还是旧数据"的错位（`Market.tsx`曾踩过，修法是新增`loadedSymbol` state，只在`loadData()`真正成功后才更新）。

## 架构评审遗留（低优先，未修）

- Phase5 的 skills frontmatter（`monitor.md`声明`enabled_tools: [signals, screener, news]`）让这3组工具schema每轮预加载，增加约1400-2300 token固定地板，部分抵消了token优化的收益——是预期行为不是bug，但值得评估这笔"地板换体验"的账划不划算，回退只需裁frontmatter，机制本身不用动。
- 同轮 tool_calls 全部串行执行（性能非正确性问题）。
- USAGE 统计无 per-session 维度（trace里有per-turn数据可以聚合出来）。
- 桌面Tauri打包版的JWT鉴权/绝对API地址在架构安全加固后**没有重新打包验证过**，如果常用桌面端且改了鉴权相关代码，记得重新打包测一次登录流程。

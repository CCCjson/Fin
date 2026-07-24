# 已知坑位与架构决策记录

> 最后更新：2026-07-16。这份文档回答"为什么这么设计 / 之前踩过什么坑"，配合 `docs/ARCHITECTURE.md`（是什么）和 `docs/FEATURES.md`（有什么）一起看。内容来自项目历史踩坑记录，按主题分类，每条格式：**是什么 → 为什么 → 现在应该怎么做**。

## 架构决策

### 为什么只剩 8 个页面 + MoneyBill 唯一入口
2026-07-03 把前端从 23 页精简到 8 个工作台页面，后端把 33 个 route 模块精简到 21 个（删了 advisor/report/review/watchlist/signal_generation/tracking/alpha_lab/backtest(Python)/portfolio/settings/decisions/trading 共12个）。

**为什么**：同一能力普遍有"页面+route+MoneyBill工具"三份入口，维护成本三倍；MoneyBill 工具全部走进程内直调引擎，route 的 HTTP 壳对 agent 来说是纯旁路，白白多一层。

**怎么做**：新能力只做「引擎 + MoneyBill 工具」两层，别再开独立页面/route，除非是重可视化需求（K线、曲线级别）。删任何现有 route 前先 `grep frontend/src` 确认真的没有页面在消费它。

### 为什么投研报告被拆成五个章节工具（13.2）
2026-07-10 把 `run_research_report` 整条链路删除，换成五个可单独调用的章节 subagent
（`report_market` / `report_news` / `report_positions` / `report_strategy` / `report_picks`）。

**为什么**：旧的全量报告是一次 **266.6 秒 / 80668 tokens / 32786 字**、全有或全无的调用——想只看板块轮动也得跑完整套。而且它是个平行宇宙：`data_collector` 自己重写了 `recommend_engine`（选股打分）、`review/service`（上期回顾）、`cockpit_engine/aggregator`（市场总览）。原计划的「DAG 并发提速」也站不住：`CHAPTER_DEPS` 关键路径近乎一条链，10 步只能降到 9 步。

> ⚠️ **上面这句「DAG 并发站不住」只是历史，别拿它论证现在的事**（2026-07-17 补注，此坑已害人绕过一次远路）。它说的 `CHAPTER_DEPS` 是**旧 8 章的 prompt 级链式依赖**，随 `planner.py` 在 13.2-6 一起**硬删**了。**现在的五个 section 工具彼此零数据依赖、理论并行度 5**——13.2 拆章时主动删掉了章间链式（`report_engine/section_writer.py:5-8`：「不做章节间链式上下文……各章独立成稿」）。
>
> 但**结论仍是不做 DAG 并发**，只是理由变了（2026-07-17 拍板）：① 收益从未实测，且天花板卡在 `report_picks`（Ch8 批次链式 `ch8_accumulated` 不可并行）；② 真实工作量在**前端**——`chunk` 事件无章节归属、前端是单个扁平 buffer，五章并发会把 markdown 物理撕碎；③ `collect_common()` 不是 single-flight，并发即 5 倍出网（今天靠串行才让它的 300s 缓存生效）；④ **更根本：病在工具粒度不在编排层**——正解是「按能力重切工具」（见 `docs/14` §2.9「P5 新解法」），那个不需要图也不需要并发。

**怎么做**：
- **「Ch1 纵览 & 操作计划」没有工具**，由 MoneyBill 主 agent 看着五章摘要亲自写。相应地，`agents/policy_checks.py` 的 `_SUBAGENT_NAMES`（触发「收尾只能一句话」的 150 字上限）**故意不含** `report_*`，加进去会把纵览压死。
- 章节摘要末尾必须带一行 `【本章涉及标的】`：`policy_checks._backed_symbols` 只扫工具结果文本判断代码「有没有依据」，而正文只截 600 字进 context，纵览引用的代码很容易被截掉而被判成「凭记忆瞎报」，强制重写一轮。
- `report_engine/web_searcher.py` 与 `stock_analyzer.py` 是**共享件**（news_engine / advisor_engine / cockpit / `api/routes/news.py` 六处依赖），别跟着报告一起删。
- `collect_common()`（行情总览 + 持仓）带 300 秒进程内 TTL 缓存，缓存 key **必须含 `report_type` 与 `period_end`**——只按时间做 key 会让周报命中日报的窗口数据。没有它，MoneyBill 连调四个章节会出网抓四次指数。
- 改任何章节 prompt 前先看 `tests/report/test_section_prompt_parity.py`：它拿退役前冻结的 golden（`tests/report/fixtures/section_prompts_golden.json`，6 个 report_type×周末组合）钉死 prompt 逐字不变。**故意改 prompt 时要连同更新 golden**，并说明改了什么。

### LangGraph 阶段A：alpha_lab 迁循环子图 + 断点续跑（2026-07-16）
13.5 评估报告推荐的阶段A 已完工：把 alpha_lab 深任务迁成 LangGraph 循环子图（`alpha_lab/graph/{state,nodes,build,engine}.py`）+ sqlite checkpointer，补上「进程重启/断连后从第 i 轮恢复」的断点续跑能力。B（report DAG）/ C（主循环迁图）未做。

**为什么这么切**：alpha_lab 的 `SessionState` 只活内存、每轮烧钱且带 cost 累计，进程一挂就得从头再来。checkpointer 在**节点边界**落盘，最贵的 90s C++ 回测完成即持久化——这是 LangGraph 带来的真实新能力，不是重写换重写。薄用四原语（`StateGraph`/sqlite checkpointer/`interrupt`/subgraph，见 `CODING_STANDARDS §6`），旧 `AlphaLabEngine` 一行未改。

**怎么做 / 改这块前必读**：
- **回滚开关 `GRAPH_ALPHA_LAB`**（默认 `off`，生产仍走旧引擎）在 subagent 层（`agents/subagents/alpha_lab.py`）二选一分叉。`on` 才走图路径。**回滚 = 删 `alpha_lab/graph/` 目录 + 去掉 subagent 几行 if**。共享数据准备刻意放 `alpha_lab/data_prep.py`（顶层，非 graph/ 子包），因为旧引擎也 import 它——放 graph/ 会让「删 graph/ 回滚」打断旧引擎。
- **★早停计数器必须持久化**：旧引擎里 `no_improve_count`/`fail_streak`/`phase` 是 `start_session` 局部变量，图路径全提升进 `AlphaLabGraphState`，否则 resume 重算早停会错。
- **⚠️ 中断节点会整个重跑**：节点边界 checkpoint 下，resume 把「中断时正在跑的那个节点」从头重跑。实测中断点常落在 `advance` 提交前 → resume 重跑 `advance` → **重发一次 `iteration_complete` + 重做 best 更新**（幂等无害，贵的 generate/backtest 不重跑）。**所有节点尤其 advance 必须对「同输入重跑」幂等**，别放非幂等副作用（无条件自增外部计数、不带去重的写库）。`strategy_store.save_strategy` 用确定性 id `{sid}_iter{i}` 天然幂等。
- **失败转 flag 不抛异常**：generate/ast_check/backtest 各自把失败落成 flag 交 `advance`，免疫 LangGraph「超步内任一节点抛异常回滚整个超步 state」坑；唯一抛的是 prepare 数据准备失败（走 finalize 收 status=failed）。
- checkpointer 库 `backend/data/alpha_lab_graph.db`（独立于 market.db，env `ALPHA_LAB_GRAPH_DB` 覆写），thread_id=session_id。resume 时临时 CSV 目录多半已被清理→用 symbols+日期区间确定性重建。
- **跑真实冒烟三个环境坑**：① 必须 **cwd=backend**（`.env` 里 `DATABASE_URL=sqlite:///./data/market.db` 是相对路径，且多个 `load_dotenv(override=True)` 会把绝对 override 冲掉）；② 8002 C++ 回测服务要在跑；③ provider=claude 没进 `SessionManager.COST_RATES` 故 cost 显示 $0（非 bug）。
- 门禁：`tests/baseline/test_deep_task_contract_graph.py`（图路径契约，与旧路径过同一套 clamp/单 done）+ `tests/test_alpha_lab_graph_resume.py`（断点续跑）。

### 引入 LangGraph 把 pydantic 顶到 2.13（2026-07-16）
装 `langgraph==1.2.9` + `langgraph-checkpoint-sqlite==3.1.0` 时，pip 把 **`pydantic 2.5.3 → 2.13.4`**、`websockets 16 → 15.0.1` 一起顶了版。

**为什么记这条**：pydantic 是 FastAPI + 整个 `ToolEnvelope`/`args_model` 校验层的核心依赖，跨 2.5→2.13 是大跳。已全量测试验证兼容（**895 passed**），`requirements.txt` 的钉子已改到 `pydantic==2.13.4`。只剩 class-based `config` 的 deprecation 警告（非错误，V3 才移除）。**动依赖前知道 pydantic 已是 2.13，别按 2.5 的假设改。**

### `AnalysisReport` 表已停写停读（13.2）
表定义还在（DB schema 冻结），但 2026-07-10 起没有任何代码读写它。

**为什么**：它原本只被报告引擎自产自销——全量报告把整份 data 快照塞进 `data_snapshot`，下一期报告再读回来取 `top_stocks.buy_recommendations` 做「上期回顾」。没有 route / scheduler / 前端碰它。

**怎么做**：买入推荐现在写 `DecisionLog`（`source="report_picks"`，见 `report_engine/picks_log.py`）——那张表本来就是为「上次推荐对不对」归因而建的。注意**「上期」的语义变了**：不再是「上一份同周期报告」，而是「上一批 `report_picks` 推荐」，因此不按 `report_type` 过滤。

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
- **alpha_lab max_iterations 已钳制到 [1,20]**（`AlphaLabEngine.start_session` + subagent fork 前 + 图引擎兜底），别去掉这个上限——无美元预算熔断，轮数是唯一烧钱闸门。图路径（`GRAPH_ALPHA_LAB=on`）同样钳制，见「架构决策 · LangGraph 阶段A」。
- **advisor_engine 是单次流式completion，不是function-calling循环**，`max_tokens=2500`硬顶，无失控风险，deep_stock只调一次。

## 常见 bug 模式

### 常驻后台 job 的"停止中"永久卡死
**症状**：进程实际已停，但前端一直显示"停止中"。

**根因**：`_run()`只有`try/except Exception`没有`finally`——`SystemExit`/线程被杀不会翻转status；`snapshot()`只读内存状态不检查线程是否存活；`stop()`对已死线程不复位状态。

**已修**：`cninfo_job.py`、`research_report_job.py`。**如果 deep_history 的 a-share/overseas job 或 alpha_lab session_manager 复现同症状，直接照抄这套「finally兜底 + snapshot自愈 + stop复位」模式**，不用重新分析——这几处都是"常驻单例+threading.Thread+status字符串状态机"同构模式。

### 非重入 Lock 双抢 → 整个会话卡死（proxy_route 自锁，2026-07-24 实锤）
**症状**：MoneyBill 对话彻底没反应，但后端「看着很健康」——`/health` 毫秒级 200，23 个线程里 22 个空闲，CPU 0%，日志停在某轮 `[tokens] round=N` 之后再无一行。

**根因**：两个单独看都没错的写法叠在一起。`proxy_route._domestic_proxy_info._fetch()` 外面包了 `with _manager_lock`，里面调的 `_get_manager()` **又拿同一把非重入 `threading.Lock`** → 同线程双抢 = 永久自锁。而 `_manager` 只可能在那个被锁死的块里赋值，所以它永远是 None，**每次调用都必然重演，不是偶发竞态**。引入点是 `62c9495f`（ProxyPool 单例化那次）把自建 `ProxyManager()` 换成了 `_get_manager()`，而后者自带同一把锁。

**为什么超时没救回来**：那圈本来有 `.result(timeout=8)`，但写成了 `with ThreadPoolExecutor(...) as ex:` —— `__exit__` 走 `shutdown(wait=True)` 去 join 已经死掉的 worker，**join 上无限阻塞，TimeoutError 连抛出来的机会都没有**。超时保护被 `with` 语句本身吃掉了。

**连带放大**：死锁线程握着该 session 的 `run_lock` 不放 → 用户再发消息一律 409 → 表现成整个桌面 App 卡死。

**三条可复用的教训**：
1. **`with lock:` 里面不许调「自己也会拿同一把锁」的函数**。要么去掉外层锁（首选），要么换 `RLock`（次选，只是掩盖嵌套）。函数如果自带锁，docstring 必须写明，禁止调用方再包一层。
2. **`with ThreadPoolExecutor(...)` 是超时保护的天敌**：`__exit__` 的 `shutdown(wait=True)` 会 join 卡死的 worker，让任何 `.result(timeout=)` 形同虚设。
3. **改手动 `shutdown(wait=False)` 也不够**：`concurrent.futures` 注册了 atexit 钩子 `_python_exit`，退出时照样 join 所有存活 worker——一个卡死的浏览器任务能让**整个后端进程关不掉**，`restart.sh` 卡在停机那步。正解是 daemon 线程：`acquisition/browser/offthread.run_in_daemon_thread`。

**门禁**：`tests/acquisition/test_browser_no_deadlock.py`（行为 + 结构双保险，含「卡死 worker 不许挡进程退出」的子进程测试）。

**诊断手法（下次遇到「进程活着但不干活」直接照抄）**：`sample <pid> 3 -f out.txt` 抓栈 → 按线程看叶子帧。全员 `uv_cond_wait`/`kevent` = 真空闲；某条卡在 `lock_PyThread_acquire_lock` → `acquire_timed` = 等锁。`py-spy dump` 能直接给 Python 行号但 macOS 上**必须 sudo**。另外 `lsof -nP -a -p <pid> -iTCP` 看有没有在等网络——一条 ESTABLISHED 的对外连接都没有，就说明卡在本地而非网络。

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
- **⛔ app-only 单实例架构（2026-07-21 收敛，此前的「app/web 双轨」已整体退役）**：全项目只服务 Mac 桌面 App。唯一入口 `bash restart.sh`（全量：`desktop/build-app.sh` 重建前端+重装 `/Applications/Fin.app` → `desktop/start-services.sh` 起后端:8000/C++:8001/:8002/SSH:11434 → `open` App）；改 Python 用 `bash restart.sh --backend` 走快档。已删除的东西别再找：web 开发后端(:8010)、vite dev(:5174)与其 proxy、`backend/data_dev/` 测试库、`start.sh`/`stop.sh`/`deploy.*`、`backend/start_api.py`、`app` 分支与 promote 仪式（`sync-to-app.sh` 已改名 `build-app.sh` 并去掉分支检查）。
  - **🔴 App 端没有热更新，这是架构事实不是配置没开**：前端静态产物被 `tauri build` **编译进 Rust 二进制**（`frontendDist: "../dist"`），后端不带 `--reload`。改了 `.tsx` 不重新打包，正在跑的 App 永远看不见。历史教训：双轨时期靠手动 promote，结果 `app` 分支落后 master **133 个 commit**，App 里跑了几个月的旧界面而没人发现。
  - **后端连的是 T9 生产库，没有沙箱**：`restart.sh` 开头硬检查 `/Volumes/T9` 挂载，没插盘直接拒绝启动（否则 SQLAlchemy 会在空目录建个空库，看着能跑数据全没）。想要隔离实例得自己传 `DATABASE_URL` + `FIN_DISABLE_SCHEDULERS=true`。
  - **网络只绑本机**：uvicorn `--host 127.0.0.1`，CORS 白名单只剩 `localhost/127.0.0.1` + `tauri://localhost`（局域网私网段那条随手机访问需求一起删了）。
- **路由前缀是 `/app/market` 不是 `/market`**——`MainStage`的`PAGES`映射只认`/app/*`，访问裸路径会静默回退到MoneyBill聊天首页（不报错，排查导航问题时留意）。
- **港股代码是5位数字**（`00700.HK`），手输容易漏位，靠`StockSymbolInput`自动补全能避开。
- **货币符号/展示字段要跟"实际展示的数据"同步派生**，不要跟实时输入框state同步派生——否则会出现"标签先跳新市场、数字还是旧数据"的错位（`Market.tsx`曾踩过，修法是新增`loadedSymbol` state，只在`loadData()`真正成功后才更新）。

## 架构评审遗留（低优先，未修）

- Phase5 的 skills frontmatter（`monitor.md`声明`enabled_tools: [signals, screener, news]`）让这3组工具schema每轮预加载，增加约1400-2300 token固定地板，部分抵消了token优化的收益——是预期行为不是bug，但值得评估这笔"地板换体验"的账划不划算，回退只需裁frontmatter，机制本身不用动。
- 同轮 tool_calls 全部串行执行（性能非正确性问题）。
- USAGE 统计无 per-session 维度（trace里有per-turn数据可以聚合出来）。
- 桌面Tauri打包版的JWT鉴权/绝对API地址在架构安全加固后**没有重新打包验证过**，如果常用桌面端且改了鉴权相关代码，记得重新打包测一次登录流程。

## 对外字段名冻结自查（域9，改 route 前必读）

第13步大重构铁律：**对外 HTTP API 字段名与 DB schema 逐字段冻结**（前端/桌面 App 零改动可用）。前几域把 agent 工具层入参改了名（`top_k/max_results/days` → `limit/window`），但那**只在 agents/tools 层**，route 是独立契约，**绝不联动**。以下是自查出的「看着该改、其实必须冻结」的点：

- **`knowledge.py` 上 `top_k` / `max_results` / `limit` / `limit_per_symbol` 四个语义不同的入参并存是故意设计**，绝不能因为工具层收敛成 `limit` 就跟着合并/改名，否则语义塌缩 + 破坏前端契约。**最高警惕项**。
- `prediction.py` 的 `forward_days`（前瞻预测期，不是回看窗口，还挂着 DB 列 `models.py`+整个 prediction_engine）、`history` 的 `days`/`limit`、`news.limit_per_source`、`data.stale_days` —— **全冻结**，别跟工具改名走。
- `walk_forward` 响应里的 `"window"` 是**回测时间窗对象**，与工具层新引入的 `window` 只是同名巧合，语义无关，别碰。
- `backtest_cpp.py:246-262` 与 `history.py` 详情的 `metrics` dict 键（`total_return_pct/sharpe_ratio/max_drawdown_pct/…`）直接透传 `result.<attr>`，是冻结对外契约；重构引擎属性名时要回头核对这两处（属性读取会抛异常而非静默改键，相对安全）。
- market 短写 `us/hk` **只许活在 `common/market.py::to_cpp_market()` 边界内**；域9 已修 `backtest_cpp.py` docstring + `history.py` 详情硬赋值（改用 `infer_market_from_symbol` 产 canonical）+ `walk_forward` 引擎（C++ 调用套 `to_cpp_market`，修港美股静默拿错市场的 bug）。

## async 边界（域9 已彻底清零 api/routes）

`async def` 内一切同步阻塞 IO（`session.query`/`repo.*` 落库方法/`data_engine.get_*`/重计算）必须走 `await asyncio.to_thread(...)`，模板见 `api/routes/data.py`。多条连续 DB 操作用**一个闭包整段包**（SQLAlchemy session 生命周期留在同一线程，不可跨线程共享）。**唯一保守未动**：`automation/scheduler.py` 的 config `session.query`（session 跨多个 await 存活、域7 已裁定本地 sqlite 轻 IO、不在 §5 原清单内）。新增 async route handler 别再裸调同步 DB/IO。

## 港美股行情更新（2026-07-17 建成，动港美股/Yahoo 出网前必读）

**港美股此前根本没有增量通道**：`DailyUpdater` 硬过滤 `market == "a_share"`；唯一通道 `deep_history/overseas_job.py` **结构上不可能做增量**（`_resolve_universe` = 「只要这只票在 `daily_quotes` 里有任意一行就永远跳过」，只能把票从「零数据」拉到「有数据」）。所以港股一度停在 07-08、美股停在 07-06 —— 那是深历史最后一次跑完的日子，**不会自己变新**。现由 `data_engine/overseas_daily_updater.py` 补上（独立 job + cron 16:30，不是每日链的一步）。

- 🔴 **`common/market.py::to_yf_symbol()` 是「去掉首位」不是「补零到 4 位」**：`09988.HK` → `9988.HK`。港股库里统一存 5 位、yfinance 只认 4 位，**喂 5 位进去 yfinance 回「possibly delisted; no price data found」——全军拉不到且不抛异常**。静默失败，最坏的那种。任何新的港股 yfinance 调用点都必须过它。
- 🔴 **港美股靠 `stock_type` 排除，不靠 `is_active`**：美股 914 只 `excluded_bond_note`/`excluded_leveraged_etf` **也是 `is_active=1`**。universe 过滤必须写 `stock_type.in_(["stock","etf"])`，只看 `is_active` 会把债券/杠杆 ETF 全捞进来。
- 🔴 **REIT 不是 ETF**：`00823.HK 领展房产基金`、`02778.HK 冠君产业信托` 是正经权益资产（领展是港股大蓝筹）。**按「基金」「信托」关键词筛 ETF 会误杀它们** —— `hk_filter.py` 的 ETF 关键词只认 `ETF` 本身。代价是 `02800.HK 盈富基金` 被标成 `stock`，**无害**（stock/etf 都在抓取范围内，承重的区分是「excluded 与否」）。有测试钉死这个取舍。
- **港股代码段**（按库里真实数据核实，非凭印象）：`0xxxx` 正股（含 GEM `08xxx`，5 位归一后仍 0 开头）/ `8xxxx` **人民币柜台**（`89988.HK 阿里巴巴-WR` = `09988.HK` 的重复，抓两遍还会重复计数；`89021.HK 国债四一零四-R`）/ `4xxxx` 债券票据。清洗后 4699 → 3978 只值得抓。
- ⛔ **不许 `import yfinance`**：`tests/net/test_egress_single_entry.py` 在 **AST 层面**检测 import（不看是否真调用），只有 `acquisition`/`net` 顶包豁免。一律走 `acquisition/markets/yf_batch.py` 门面（代理由 `configure_yf_proxy()` 按 net.overseas 注入，且必须赶在任何 yf 调用前——yfinance 底层 curl_cffi 单例只在首次建 session 时读一次代理）。
- ⚠️ **§8.2 代理铁律（国内抓取失败只换 IP 不许直连）不管海外**：海外走 `channels.resolve_overseas_proxy()`，返回 None 就是**合法直连**。
- **Yahoo 跨 job 互斥** `yf_batch.yahoo_job_lock()`：深历史回补与每日增量同时打 Yahoo = 两倍请求量，两边都可能被限。`overseas_job` 自己的单例锁只管「hk/us 不同时跑」，**管不到别的 job**。优先级：**自动的让位于手动的**（增量抢不到就跳过、明天还有机会；深历史抢不到明确报错停下、Jason 在旁边等着）。**互斥必须双向**，只有一边抢锁等于没锁，有测试钉死。
- **别让一只掉队的票拖累全体**：拉取起点若取全局 `min(latest)`，一只落后一年的票会把所有票的起点拖到 90 天前 → 只缺 9 天的票也拉 90 根 bar，16k 只上是 **10 倍流量**。已改成 todo 按落后程度排序 + **每批各算起点**。
- **零数据的票跳过**（港股 ~817 只）：那是**深历史的活**，增量不该去拉十年数据把一次运行拖死。
- **启动补跑门槛比 A 股钝得多**（`_OVERSEAS_CATCHUP_STALE_DAYS=3`）：A 股误判 = 几秒空转（`DailyUpdater` 探到真实交易日、全部 `already_fresh` 立即 complete）；港美股误判 = **10-15 分钟白打 Yahoo**。而港美股各有独立假期（美股还有夏令时），**没交易日历就分不清「今天是假期」和「job 没跑」** —— 项目里根本没有交易日历模块（`repository.py` / `recommend_engine/session.py` 两处注释都是「没有」的自白）。

## crypto 成交明细同步（2026-07-24 修，动 cost_basis/持仓成本前必读）

crypto 的**持仓成本价不是币安给的**（币安没有持仓成本端点），是靠成交明细表 `crypto_fills` 回放算出来的。表里没有某个币的明细 → 该币成本回放为 `unknown`（`avg_cost=0`），前端显示「成本未知」，且 `StopLossRule` 走「无成本，跳过」分支——**四条硬风控对这个币静默失效**。所以「成交明细同步」是成本/风控的命脉，不是可有可无的后台任务。

- 🔴 **单币同步失败曾拖垮整轮**（ETH/SOL 成本长期 unknown 的真凶）：`sync_held_fills` 原来是列表推导 `[sync_symbol_fills(s) for s in ...]`，任一币的 `_insert_fills` commit 撞 `database is locked` 抛异常 → 冒泡出循环 → **后面还没同步的币全被拖没**。已改逐币 try 隔离，失败的记进 `errors` 下轮重试，不炸整轮。
- 🔴 **`database is locked` 是这个库的常态**：15GB 单文件 SQLite，A 股日线更新有 5000+ 只票的**超长批量写事务**，写锁能持有超过 `busy_timeout=30s`，此时 crypto 同步的 commit 会抛 locked。已加 `cost_basis._commit_with_retry`（应用层退避重试兜底）。**任何 crypto 后台写库路径都该考虑这个锁竞争**。
- ⚠️ **同步成功时以前不记日志**：`crypto_scheduler` 的 sync 段原来只在「有新增或有错误」时才打日志，成功静默 → 同步默默失败时日志一片空白，只能靠「成本显示 unknown」发现。已改**总记一行**（有 errors 记 warning）。排查 crypto 成本问题先 `grep '成交明细同步' /tmp/fin-backend.log`。
- ✅ **已同步一次就永久 `full`**：明细落库是持久的，`INSERT OR IGNORE` 幂等，下一轮不会重复插。unknown 只发生在「这个币从没成功同步过」的窗口期。手动补齐：`conda run -n quant python -c "from crypto_intel_engine import cost_basis as cb; print(cb.sync_held_fills())"`。
- **要同步哪些币** = 交易所当前持仓 ∪ 库里已有 fills 的 symbol（后者是为了已清仓历史仓位的连亏 streak 回放）。新买的币在下一轮调度（默认 30 分钟）才会被纳入——急用就手动跑上面那行。

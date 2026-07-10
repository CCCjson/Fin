# 编码与架构规范（CODING_STANDARDS）

> 2026-07-09 起草，同日拍板生效。配套文档：`docs/13-大重构规范与迁移计划.md`。
>
> **适用范围**：①所有新写代码；②已完成标准化迁移、进入 mypy 强检名单的模块。存量未迁移代码不要求回头改，但改到哪个文件，顺手把该文件收敛到规范。
>
> 本规范的每条 canonical 选择都基于 2026-07-09 的全仓盘点实锤（约 290 个 .py），不是拍脑袋：凡标注「已占 N%」的，是实际统计出的主流写法。

---

## 0. 分层与依赖方向（架构总则）

```
api/routes（薄壳：校验/鉴权/转发）
    ↓
agents / graph（编排层：主循环 harness + LangGraph 子图）
    ↓
engines（业务层：advisor/cockpit/recommend/report/alpha_lab/...，只做编排与存储）
    ↓
acquisition（数据获取层：一切出网抓取的唯一入口，见 §8）
    ↓
common / net / data_engine.storage（基础层）
```

1. **依赖只许向下**。引擎层不得 import `agents`/`graph`/`api`（2026-07 实测现状已是干净的单向 DAG，0 处反向 import——保持住）。
2. **新能力只做「引擎 + 工具」两层**，不新开前端页面、不新开 HTTP route（架构收敛决议）。
3. **route 必须薄**：业务逻辑一律下沉引擎。反例见 `api/routes/walk_forward.py`（整套滚动优化算法写在 route 里，13.4 下沉）。
4. **风控硬规则是纯代码**（`trading_engine/risk/`），位于一切下单路径的必经处。任何编排框架、LLM、策略代码都不可绕过、不可配置化软化。agent 对风控键只读（`settings_tools._RISK_READONLY_KEYS`）。
5. **单一真源原则**（2026-07-09 二轮拍板）：回测执行/费用/绩效指标唯一口径 = **C++ 回测服务**（alpha_lab 走信号驱动端点，Python `backtest_engine/` 退役后禁止新代码 import）；一切出网数据获取唯一入口 = **`acquisition/`**。禁止绕过。

---

## 1. 命名词典（canonical 参数表）★

同一概念全仓只许一种拼法一种类型。改名对照：

| 概念 | canonical | 废弃/收敛的变体 | 备注 |
|---|---|---|---|
| 股票代码（入参） | `symbol: str`，**带后缀**（`600000.SH` / `00700.HK`，美股裸 ticker） | `code` / `ticker` / `secid` / `stock_code` | 已占 229 处压倒性主流。变体只许作**边界局部变量**：`code`=剥后缀裸码、`secid`=东财 wire 格式、`ticker`=yfinance 对象 |
| 取裸码 | `common.market.to_bare_code(symbol)`（**新建**） | 散落 10+ 文件的 `symbol.split(".")[0]` 手写 | 同时新建 `add_exchange_suffix()`、`to_yf_symbol()` 收编 `stock_pools.py:29`、`hk_stock.py:13` 等私有实现 |
| 市场 | `market: str` ∈ `a_share` / `hk_stock` / `us_stock`，默认 `"a_share"` | `us` / `hk` / `A` / `沪深京` | 真源 `common/market.py`。短写 `us/hk` **只许活在 `to_cpp_market()` 边界内**；待修：`api/routes/backtest_cpp.py:55` docstring、`api/routes/history.py:396-398` 硬赋值 |
| 起止日期 | `start_date` / `end_date: str`，格式 `"YYYY-MM-DD"` | `date`/`datetime` 注解；compact `YYYYMMDD`；`begin_date` | dashed 已占 ~70%。compact 只在 fetcher 出网前临时转换。`MarketDataRequest`（`fetchers/base.py:12`）从 `datetime` 改 `str`，消除来回转换 |
| 指标窗口 | `window: int` | `period: int` | 消除 `period` 的类型双关（int=窗口 vs str="2y"） |
| K线频率 | `freq: str`（`"1d"` / `"1w"` / `"1M"`…） | `frequency` / `interval` / `period: str` | 现状「字段叫 frequency、传参叫 interval」（`us_stock.py:31`）就是这条要消灭的 |
| 数量上限 | `limit: int`（配套 `offset: int`） | `top_n` / `count` / `max_results` | 已占 40+ 处主流。**无例外**（websearch 的 `max_results` 也收敛为 `limit`，2026-07-09 三轮拍板） |

**执行方式**：迁移一个域时按本表逐参数改名；对外 HTTP API 字段名**冻结不改**（契约冻结决议），route 层做参数名适配。

---

## 2. 函数命名（动词规范）

| 动词 | 用途 | 现状 |
|---|---|---|
| `get_` | 取数（DB / 内存 / 网络均可） | 276 处，主流 |
| `fetch_` | **仅限 fetcher 层内**真正出网抓取 | 43 处，收敛用途 |
| `list_` | 集合列举 | 23 处 |
| `calculate_` | 计算（废弃 `calc_` / `compute_`） | 13 处 |
| `build_` / `create_` / `update_` / `delete_` | 构造 / CRUD | — |
| `is_` / `has_` / `can_` | 谓词，返回 bool | — |

**类内动词必须一致**：反例是三个 fetcher 类同时有 `fetch_daily` 和 `get_stock_list`（`a_share.py:20` vs `:157`）——迁移时统一为 `fetch_*`（它们都是出网抓取）。

---

## 3. 类型规范

1. **全量 type hints**：参数 + 返回值都要注解。补注解重灾区（覆盖率）：`backtest_engine/engine.py` 25%、`prediction_engine/engine.py` 38%、`review/service.py` 57%、`data_engine/daily_updater.py` 58%。
2. **工具层**：入参一律 Pydantic `args_model`；返回一律 `ToolEnvelope`（见 §4）。
3. **领域实体**：broker / backtest / risk 域沿用 **dataclass**（既有事实标准）；API 请求/响应用 **Pydantic**；同一域内不得两者混用。
4. **禁止 tuple 当错误通道**（`return [], None` 这类），改为抛异常或返回带 `ok` 字段的模型。
5. Python 3.10+ 语法：`X | None` 优于 `Optional[X]`（新代码），迁移时顺手换。

---

## 4. 返回值与错误处理（分层约定）★

**每层一种失败形态，跨层转换，同层不混**：

| 层 | 失败时 | 说明 |
|---|---|---|
| fetcher / engine | 返回**空结构**（`[]` / `{}` / 空 DataFrame），**不返回 None** | 避免下游 `None.xxx`；必须 `logger.warning/exception` 留痕 |
| agents/tools | `ToolEnvelope(ok=False, ...)` | 全部 20 个工具文件收敛到 ToolEnvelope。重灾区：`knowledge_tools.py`（7 个函数 0 采用）+ 6 个混用文件 |
| api/routes | `HTTPException` | 已是主流，保持 |

**异常纪律**：
- 禁裸 `except:`（现存 3 处，全部消灭）。
- `except Exception` 必须 `logger.exception(...)` 或有注释说明为何静默；**禁止裸 `pass` 吞**（现存 71 处待扫）。
- 捕获能具体则具体（`requests.RequestException`、`sqlalchemy.exc.OperationalError`…）。

---

## 5. async 边界

**硬规矩**：`async def` 内一切阻塞 IO——同步 SQLAlchemy `session.query`、`data_engine.get_*`、`requests`——必须走 `await loop.run_in_executor(...)` 或 `asyncio.to_thread(...)`。

- 正确模板：`api/routes/data.py:44`。
- 违规待修清单（同一操作隔壁文件是对的、这里是错的）：`api/routes/analysis.py:44,160,219`、`api/routes/automation.py:143,202,233,253`、`api/routes/history.py`（5 处）、`api/routes/backtest_cpp.py`（4 处）、`automation/scheduler.py`（4 处）。

---

## 6. LangGraph 使用纪律（薄用原则）★

1. **只用四个底层原语**：`StateGraph`、checkpointer（sqlite）、`interrupt`、subgraph。**禁止**引入 langchain 高层预制件（chains、预制 agent、花哨 memory 组件）；prompt 自己管，工具保持普通 Python 函数。
2. **四大安全件整件复用，禁止在图里重写**：确认门 `agents/confirm_gate.py`、TurnMonitor `agents/turn_monitor.py`、历史压缩 `agents/history.py`、会话锁 `agents/context.py::run_lock`。它们已解耦、各有测试，在图中以节点/中间件形态挂载。
3. **深任务子图统一形状**：`Plan → Execute（ReAct 叶子节点）→ Evaluate →（不及格 replan / 通过 done）`。Evaluator 用 cheap model + 明确 rubric，不用主力模型自由发挥。
4. 图状态（State）必须是显式 typed dict / Pydantic，禁止在 state 里塞不可序列化对象（断点恢复会坏）。
5. 下单类路径在图上必经**纯代码风控节点**（非 LLM 节点）。

---

## 7. 后台任务与调度

1. 单例后台 job（start/status/stop 三件套）一律继承 **`common/base_job.py::BaseSingletonJob`**（13.4 抽取，收编现有四份复制粘贴：cninfo / research_report / 深历史A股 / 深历史海外），子类只实现 `_run()` 与自有 state 字段；线程生命周期、stop_flag、snapshot 自愈由基类管。
2. 新脚本/后台任务的进度可视化一律接入 `/app/data-monitor` 现成面板体系，不新起独立工具。

---

## 8. 网络请求与数据获取（2026-07-09 二轮拍板后重写）★

**一切出网数据获取只走 `acquisition/` 统一层**（目标结构与迁移见计划文档 §3.5），职责三分：

| 类 | 职责 | 用在 |
|---|---|---|
| `BaseCrawler` | 通用抓取原语：bounded_get 流式封顶 50MB、代理策略（`Channel` 三选一）、重试、限速 | 一切普通 HTTP 抓取 |
| `ReverseApiCrawler`（逆向类） | 按 `crawler/sites/` 站点配置调用**已侦查好**的接口 | 雪球/股吧/Capital IQ 等已逆向站点 |
| `ApiDiscoverer`（探测类） | 侦查**新站点**的接口结构，产出站点配置 | 接入新数据源时 |

硬规矩：
1. **引擎层禁止散写 requests/httpx 出网**——要爬东西就调 acquisition 的三类之一；接新站先用探测类产配置，再用逆向类日常调用；站点配置只存 `acquisition/crawler/sites/` 一处。
2. 国内抓取**失败只换代理 IP 重试，任何场景禁止降级本地直连**（铁律，commit `7158f37`）；此策略实现在 BaseCrawler/net 层，调用方不自己写重试。

   **边界（2026-07-10 拍板并落地）**：铁律覆盖**全部**国内抓取，含逆向爬虫栈（股吧/雪球，`knowledge_engine/browser/proxy_route.py`）与手动登录（`manual_login.py`）。三种状态：

   | 状态 | 判定 | 行为 |
   |---|---|---|
   | `.env` 没配快代理（`get_proxy_manager()` 返回 None）| 没有代理服务可用 | 直连是**唯一选项**，不是降级 → 允许 |
   | 配了但取不到 IP（额度耗尽 / API 挂 / 超时）| 降级直连 | 抛 `ProxyExhaustedError`，**一个请求都不发** |
   | 配了、拿到 IP、请求失败 | 换下一个 IP | 重试到 `max_rounds` 耗尽再抛 |
   | 调用方显式传 `prefer_direct=True` | **前进式**：直连是第 0 轮 | 直连失败后必须上代理；代理取不到 IP 照样抛，绝不回头再直连 |

   `KNOWLEDGE_SCRAPER_PROXY_ENABLED=false` 是逆向爬虫栈的**明示直连开关**（等价于「没配代理」），不是静默降级。

   `prefer_direct=True`（2026-07-10 拍板）给低频接口省额度用，目前只有**新闻**路径在用（`news_engine/fetcher.py`、`report_engine/web_searcher.py`）。它和铁律不冲突：铁律禁的是「取不到 IP 就悄悄 `proxies=None`」，而这里直连是调用方显式点头的第一次尝试，且**默认 False**——忘了传参不会偷偷变成直连。

   ⚠️ 仍在绕过铁律的平行栈（13.4-2 `acquisition/` 收编时一并消灭）：`acquisition/markets/realtime.py`、`review/service.py`、`acquisition/markets/eastmoney_crawler.py` —— 它们各自手写重试，取不到 IP 时仍会静默直连。（`report_engine/web_searcher.py` 已改用 `get_proxy_manager()` 单例 + `prefer_direct` 语义。）

   门禁：`tests/net/test_no_direct_fallback.py`。

3. **一个 IP 服务所有国内抓取**（2026-07-10 拍板并实测）。三件事各司其职，别搞混：

   | 想要的性质 | 靠什么 | 何时生效 |
   |---|---|---|
   | 东财行情 / 个股 K 线 / 腾讯 / 雪球 / akshare **共用同一个 IP** | `get_proxy_manager()` 单例 + `get_proxy()` 复用 | **默认**，只要 IP 没过期（实测寿命 180s）。与站点无关，不需要调用方做任何事 |
   | 并发失败时不各买各的替补 | `ProxyManager.switch_proxy(stale=手上那个IP)` | 自动（`_rotate` 已用） |
   | 一次逻辑取数拆成 N 个请求要**整体重试** | `net.domestic_rotate(run, what=...)` | 调用方显式选用 |

   `switch_proxy(stale=…)` 的语义是「换掉**我手上这个**」，不是「随便买一个」：若 `current_proxy` 已经不是它了（别人先一步换过），直接复用那个新 IP 不扣额度。全程持锁，确定性。不传 `stale` 仍是无条件买（未收编的旧调用方靠这个保持语义不变）。**这条守卫作用在 IP 层而非站点层**，所以跨站点并发失败同样只买一个替补。

   `domestic_rotate` 管的**不是省 IP**（有了 stale 守卫，N 个独立 `domestic_get` 并发失败也只买 1 个替补），而是**原子性**：5 个子请求凑成一份快照，不能 3 个来自旧 IP、2 个来自新 IP。代价是重做已成功的子请求。

   实测（真快代理 + 真东财）：跨 5 站点/端点并发失败只买 2 个 IP；`domestic_rotate` 下 20 个并发请求跨 4 轮只买 3 个 IP。门禁：`tests/net/test_ip_sharing_and_rotation.py`（含变异验证：拆掉守卫会红三条）。

   ⚠️ **`_rotate` 把一切失败都归咎于 IP**。若某个端点对所有 IP 都失败（如 2026-07-10 实测 `push2.eastmoney.com/api/qt/stock/get` 已被东财封杀，而同一 Session 同一 IP 上 `ulist.np/get` 仍 200），每次调用会白烧 `max_rounds - 1` 个 IP 才放弃。新增抓取时先手工验一次端点是否还活着。

   `ProxyExhaustedError` 有**两个子类**，因为调用方的正确反应不同：

   | 子类 | 含义 | 批量任务该怎么办 |
   |---|---|---|
   | `ProxyQuotaExhaustedError` | 一个 IP 都取不到（额度耗尽 / 提取 API 挂）；**一个请求都没发** | 整体放弃——继续翻页拿不到 IP |
   | `ProxyRetriesExhaustedError` | 换满 `max_rounds` 个 IP，每个都失败了；IP 拿到了、请求也发了 | 跳过这一项继续；连续失败多次再判定池子挂了 |

   只 `except ProxyExhaustedError` 的调用方行为不变。范例见 `acquisition/markets/industry.py`。

4. 行情实时源的主备切换（东财→腾讯→新浪）只在 `acquisition/markets/quote_router.py` 一处实现，调用方无感。
5. 例外仅**三类**：localhost 内部服务（C++ 回测 :8002、订单簿撮合、MLX server）可裸 httpx/requests，不走代理层；LLM API 统一走 `llm_client.build_client()` + `llm_config.normalize_chat_params()`，流式循环用 `stream_chat()`（带工具）/ `stream_text()`（纯文本，13.4 新抽，消灭 4 处手抄）；**非 HTTP 协议数据源**（pytdx 走 TCP socket 连通达信，塞不进 HTTP 代理层）——它仍须包在 `acquisition/markets/` 门面里，引擎层不得直接 `import pytdx`。

---

## 9. 文档字符串与日志

- Google style docstring；对外工具函数的 docstring 就是模型看到的工具说明，写给 LLM 读。
- 日志用 loguru；关键操作（下单、风控触发、任务启停、外部请求失败）必须记录。
- 注释只写「代码本身表达不了的约束」，不写「这行干什么」。

---

## 10. 质量门禁（工具链强制）

| 工具 | 强度 | 机制 |
|---|---|---|
| **ruff** | 全仓开 | 规则集：`E,F,W,I(isort),N(命名),UP(pyupgrade),B(bugbear)`；`line-length=120`。存量豁免用 `per-file-ignores` 白名单，只减不增 |
| **mypy** | 渐进强检 | `pyproject.toml` 维护**强检名单**：迁完一个域就把该域加入名单（`disallow_untyped_defs=true`）；名单外模块仅基础检查。新文件默认强检 |
| **pytest** | 基线必过 | `tests/agents/`（21 个 harness 测试）+ 13.1 新增行为基线；迁移 commit 前必须全绿 |
| **commit** | 限定路径 | 一律 `git commit -- <path>`（仓库常有 Jason 预先 staged 的在制品，禁止全量提交） |

配置统一放 `backend/pyproject.toml`（13.0 落地，含 pytest 根配置——现状没有任何 pytest 配置文件，要补）。

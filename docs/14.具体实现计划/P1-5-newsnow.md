---
id: P1-5
title: NewsNow 资讯源接入
size: 小
depends: 无
paths_verified: 2026-07-17
status: 方案已勘察定稿，实测数据在手，可直接开工
---

# P1-5 NewsNow 资讯源接入

> **战略意义 > 战术价值**：这不只是「多几个新闻源」。本项目现在为了拿雪球/股吧在维护**逆向 API + `manual_login` + cookie 剥离**那一套；NewsNow 是开源聚合器（可自建），**它替我们扛掉了财联社/金十/格隆汇各自的反爬，吐出来是干净 JSON**。对照「目标商业化 + 合规雷」——这是一条合规得多、且不用养 cookie 的路。

## 四个真新闻源（2026-07-17 实测 shape）

接口：`GET {base}/api/s?id=<source_id>`

| source_id | 中文名 | 条数 | 时间字段 | category | 坑 |
|---|---|---|---|---|---|
| `cls-hot` | 财联社热门 | 13 | **无** | `domestic` | — |
| `gelonghui` | 格隆汇事件 | 15 | `extra.date`（毫秒） | `domestic` | — |
| `wallstreetcn-quick` | 华尔街见闻快讯 | 30 | `extra.date`（毫秒） | `global` | — |
| `jin10` | 金十数据 | 27 | `pubDate`（毫秒） | `global` | `extra.info` 是**布尔 `false`** 不是字符串 |

## ⚠️ `xueqiu-hotstock` 不是资讯源

`{"title":"中际旭创","url":".../SZ300308","extra":{"info":"-10.06% SZ"}}` —— **title 是股票名**。

当新闻入库会污染 `news_articles`（**该表无 retention、只增不减，脏数据永久**），且让 BERT 分析「中际旭创」四个字毫无意义。**单独做成热度信号工具。**

## 落点

| 类型 | 文件 | 要做什么 |
|---|---|---|
| **新增** | `backend/acquisition/markets/newsnow.py` | `fetch_newsnow(source_id, *, limit)` → 返回**原始** items（映射留给 news_engine，照抄 `finnhub_news.py` 的职责切分）。**`Channel.OVERSEAS`**（官方实例是 Cloudflare 海外节点，实测 cf-ray 落新加坡）+ `BaseCrawler.get_json()`（自动满足 bounded 铁律）。`NEWSNOW_BASE_URL` env 可切自建实例。**必须写在 `acquisition/` 下**，否则 `tests/net/test_egress_single_entry.py` 会咬 |
| **新增** | 同文件内 `fetch_xueqiu_hot()` + `backend/agents/tools/` 下的 `get_retail_hot_stocks` 工具 | 「现在散户在盯哪些票」。代码从 url 解析（`SZ300308`→`300308.SZ`），市场从 `extra.info` 后缀（`SZ`/`SH`/`HK`，实测 30 条 = 25 A股 + 5 港股）。**不入库、实时查**（同 `get_news_sentiment` 的无状态形态）。**必须归组 `tool_groups.py`** 否则 RuntimeError |
| **修改** | `backend/news_engine/fetcher.py` | 加 `_NEWSNOW_SOURCES` 目录 + `_newsnow_display(limit)`，在 `collect_market_news` **末尾** `all_news += ...`。**放末尾是有意的**：标题前 20 字去重时**东财优先**，NewsNow 只贡献独家标题，行为向后兼容 |
| **修改** | `backend/news_engine/news_scheduler.py`（:351-353） | **一行**：`"published_at": n.get("published_at") or datetime.now()`。这就是「只让 NewsNow 带真时间、不动其他源」的全部代价——东财/finnhub/DDG 的 display dict 没这个键 → 照旧 `now()`，**行为零变化**。（`news_tools.py:97` 已在读 `n.get("published_at") or n.get("time")`，键早预留好了） |
| 修改 | `.env.example` | `NEWS_ENABLE_NEWSNOW=true` 门控，照抄 `NEWS_ENABLE_WEBSEARCH` 的 `_env_bool` 模式（`news_scheduler.py:46-50`），给一个「源挂了就关」的应急开关 |
| **不改** | `api/routes/news.py` / `agents/tools/news_tools.py` | **复用 `domestic`/`global` 语义分组 → 消费方零改动**。这两处 category 分组是**硬编码三组**的，新起 category 会被静默丢弃。代价：晨报里看不出哪条来自 NewsNow——但 `source` 字段仍写「财联社」等真实源名，**溯源不丢** |

## 已知边界（心里有数，别当 bug 报）

`fetcher.py:343-357` 的 **48 小时同标题去重会吃掉转载**。NewsNow 聚合的这几家跟东财 350/356/351 频道大概率重叠（财联社的内容东财也转）。

**实际新增会低于抓取量，边际价值全在独家标题上**——接完不会看到「新闻量翻倍」，而是「多了金十/格隆汇/华尔街见闻的独家快讯」。**这是设计意图。**

## 已定稿的三个决策（不要重开讨论）

1. 四个真新闻源接入 + 雪球单独做热度信号。
2. 复用 `domestic`/`global` 分组。
3. `published_at` 只让 NewsNow 带真时间，不动其他源。

---

## 🪙 crypto 兼容（2026-07-27 重设计｜**分类 ②：要加 crypto 分支**）

> 先读 `00-PLAN.md` §4b 通用口径。

### 现状：crypto 已有一条**独立的**新闻链，本卡不能无视它

| 已有资产 | 干什么 |
|---|---|
| `acquisition/markets/crypto_news.py` | crypto 新闻/公告出网抓取 |
| `crypto_intel_engine/news.py`（16.7KB） | crypto 新闻 → 事件识别 → **事件硬否决**（见 [[crypto-decision-sources-v2]]） |
| `data_engine/crypto_updater.py:264` | 公告 + 新闻 RSS → `NewsArticle(market='crypto')` + FinBERT 情绪 |

**✅ 好消息：落库口径已经通** —— `news_articles` 表已有 `market='crypto'` 的行，NewsNow 的 crypto 源接进来不需要动表结构。

**⚠️ 真正的决策点：NewsNow 的 crypto 源并进哪条链？**

- 并进 `news_engine/fetcher.py:collect_market_news`（本卡主路径）→ 进通用新闻池，**但拿不到 `crypto_intel_engine/news.py` 的事件硬否决能力**
- 并进 crypto 自己那条链 → 能吃到事件否决，**但要在 crypto 侧再写一遍 NewsNow 接入**
- **倾向前者 + 让 crypto 链去读通用池**，但这依赖 crypto 链现在怎么取新闻 —— **开工时先勘察 `crypto_intel_engine/news.py` 的数据来源，再定**。别先写代码。

### ⚠️ 未经核实的部分（**开工时必须先实测，不许照抄下面这段当结论**）

本卡正文那四个源（cls-hot / gelonghui / wallstreetcn-quick / jin10）是 **2026-07-17 实测过 shape 的**，可信。但 crypto 相关的以下几点**全部只是推测**：

| 推测 | 要验什么 |
|---|---|
| NewsNow 有 crypto 分类的源（coindesk / odaily / blockbeats / 深潮之类） | **先 `GET {base}/api/s?id=<猜的id>` 挨个试**，拿到真实 source_id 和 shape 再写进卡。⛔ 别把「据说有」写成落点 |
| `jin10` / `wallstreetcn-quick` 里本来就混着 crypto 快讯 | 实测抓一批看有多少条是币圈内容 —— 若比例够高，**可能根本不需要新增 crypto 源**，只需在消费侧按关键词分流 |
| 雪球热度的 crypto 对应物（币安热搜 / CoinGecko trending） | CoinGecko 有**滚动窗口封禁**（见 [[crypto-decision-sources-v2]]），排雷类调用必须一天一次。热度信号若要高频，**换币安自己的接口** |

### crypto 分支的落点增量

| 类型 | 文件 | 要做什么 |
|---|---|---|
| 修改 | `backend/acquisition/markets/newsnow.py`（本卡新增的那个文件） | `_NEWSNOW_SOURCES` 目录加 crypto 组（**source_id 待实测填**）。`Channel.OVERSEAS` 不变 |
| 修改 | `backend/news_engine/fetcher.py` | crypto 源的 `market` 字段要落 `crypto`，**别混进 domestic/global 两组**——那两组是给股票消费方用的 |
| 待定 | `backend/crypto_intel_engine/news.py` | 是否改读通用新闻池（见上方「真正的决策点」）。**开工时勘察后决定，别提前动** |

### ⛔ 别踩的坑

- **`news_articles` 表无 retention、只增不减**（本卡正文已警告）。crypto 新闻源普遍**高频且噪声大**（各种 KOL 转发、交易所公告刷屏），比 A 股财经源更容易灌爆这张表。**接 crypto 源前先想清楚频率**。
- **48 小时同标题去重**对 crypto 更容易误伤：币圈同一条消息各家标题几乎一致，去重会吃掉大部分。这与股票侧「吃掉转载」是同一机制，但 crypto 上更严重。

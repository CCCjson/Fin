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

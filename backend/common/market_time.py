"""市场时区的单一真源 —— 「这个市场的『今天』是哪天」。

## 为什么需要它

项目里 `date.today()` / `datetime.now()` 全是**服务器本地时间**（机器在 UTC+8）。
A 股/港股恰好也是上海时间，所以从没出过事；另外两个市场不是：

- **美股**在美东。北京时间 07-22 上午 10 点，纽约还是 07-21 晚上 10 点 —— 拿本地
  `date.today()` 去判「美股今天的日线到了没 / 这条信号是不是今天的」，每天有约 12 小时
  在问一个**还没发生的交易日**。
- **crypto** 在 UTC，而且这不是我们能选的：币安日线 openTime/closeTime 是 UTC 00:00
  边界、资金费率 UTC 00/08/16 结算、账户对账单按 UTC。项目里 crypto 数据链
  （`daily_quotes`(crypto) / `crypto_bars` / `crypto_metrics` / `crypto_fills` /
  两个 APScheduler）**本来就全是 UTC**。

## 约定（2026-07-22 拍板）

| 市场 | 时区 | 依据 |
|---|---|---|
| `a_share` | Asia/Shanghai | 上交所/深交所 |
| `hk_stock` | Asia/Shanghai | 港交所在 UTC+8，与上海对本项目等价；同一个 tz 少一个变量 |
| `us_stock` | America/New_York | NYSE/NASDAQ，**自带夏令时**，绝不写死 -5/-4 |
| `crypto`   | UTC | 币安的既定口径，见上 |

## 两类时间，别混

- **市场日（market day）**：行情日期、「当日」聚合窗口、新鲜度判定 → 用本模块。
- **系统时刻（system instant）**：日志时间戳、缓存 TTL、重试退避、任务耗时 →
  继续用 `datetime.now()`，**不归本模块管**。

## 存储铁律

进库的 `DateTime` 列一律 **naive UTC**（`utc_now()`），序列化时带显式 offset
（`utc_iso()`），展示时由前端/调用方转本地。naive 本地时间进库 = 换台机器、换个时区
就全错，而且跨市场聚合时没有任何办法对齐。

`Column(Date)` 不受此约束 —— 那是「交易日」不是「时刻」，由本模块的市场时区决定。

## 不做交易日历

本模块只回答「今天是几号」，**不回答「今天开不开市」**。项目至今没有交易日历
（见 `data_engine/daily_pipeline_scheduler.py` 与 `recommend_engine/session.py:30`
的注释），本模块不引入 —— 两件事分开。
"""
from __future__ import annotations

from datetime import date, datetime, time, timedelta
from typing import Literal
from zoneinfo import ZoneInfo

from common.market import A_SHARE, CRYPTO, HK_STOCK, US_STOCK, normalize_market

UTC = ZoneInfo("UTC")

# canonical 市场 → 交易所所在时区。**唯一真源**，别在别处再写 ZoneInfo(...)。
MARKET_TZ: dict[str, ZoneInfo] = {
    A_SHARE: ZoneInfo("Asia/Shanghai"),
    HK_STOCK: ZoneInfo("Asia/Shanghai"),
    US_STOCK: ZoneInfo("America/New_York"),
    CRYPTO: UTC,
}

# 存储口径。迁移期（批 5 收口 → 批 6 翻转）有两种：
#   "utc"         列已存 naive UTC —— 最终形态，crypto 链路从批 2 起就是它
#   "naive_local" 列还存 naive 本地时间 —— **临时**，批 6 翻转后应降到 0 处调用
Storage = Literal["utc", "naive_local"]

# 服务器本地时区（只在 storage="naive_local" 的迁移期用到）
_LOCAL = datetime.now().astimezone().tzinfo


def tz_of(market: str | None) -> ZoneInfo:
    """市场 → 时区。任意写法（`us` / `美股` / `US_STOCK`）先经 `normalize_market` 归一。

    无法识别时随 `normalize_market` 落到 A 股（与全项目 fallback 口径一致）。
    """
    return MARKET_TZ[normalize_market(market)]


# ──────────────── 「现在」 ────────────────

def utc_now() -> datetime:
    """当前时刻的 **naive UTC** —— 所有写库的 `DateTime` 列都用它。

    刻意返回 naive 而非 aware：SQLite 的 DATETIME 列存不了 tz，存 aware 进去再读出来
    会变成 naive，口径反而更不清楚。统一「库里全是 naive UTC」这一条假设最省心。
    """
    return datetime.now(tz=UTC).replace(tzinfo=None)


def market_now(market: str | None) -> datetime:
    """当前时刻在该市场时区的 **aware** datetime。判「现在几点、在不在交易时段」用它。"""
    return datetime.now(tz=tz_of(market))


def market_today(market: str | None) -> date:
    """该市场的「今天」。替代一切 `date.today()`。"""
    return market_now(market).date()


# ──────────────── 时刻 ↔ 市场日 ────────────────

def _as_aware_utc(dt: datetime) -> datetime:
    """naive 一律按 UTC 解读（存储铁律），aware 原样。"""
    return dt.replace(tzinfo=UTC) if dt.tzinfo is None else dt


def to_market_tz(dt: datetime, market: str | None) -> datetime:
    """存储时刻 → 该市场时区的 aware datetime（展示用）。"""
    return _as_aware_utc(dt).astimezone(tz_of(market))


def market_day_of(dt: datetime, market: str | None) -> date:
    """一个存储时刻属于该市场的**哪一天**。

    ⛔ 别用 `dt.date()` 代替：那是 UTC 日，对美股会整体偏一天（美东下午的成交在 UTC
    已经是次日），对 crypto 才恰好相等。
    """
    return to_market_tz(dt, market).date()


def market_day_bounds(market: str | None, day: date | None = None, *,
                      storage: Storage = "utc") -> tuple[datetime, datetime]:
    """该市场某个自然日 → **存储口径的半开区间** `[start, end)`，两端都是 naive。

    所有「当日」聚合都该用它，替代这两种写法：

    - `DATE(col) = :today` —— 只在「列的时区恰好等于市场时区」时才对。crypto 实测：
      本地凌晨 3 点的成交按 `DATE(trade_time) = date.today()` 查**恒为 0**（列是 UTC，
      today 是本地），当日手续费/回撤熔断因此每天瞎 8 小时。
    - `datetime.combine(date.today(), time.min)` —— 同病，且更隐蔽（看起来很像对的）。

    Args:
        storage: 目标列的存储口径。`"utc"` = 列已存 naive UTC（最终形态）；
            `"naive_local"` = **迁移期临时**，列还存着 naive 本地时间。
            批 6 翻转后 `"naive_local"` 应降到 0 处调用，届时连同本参数一起删。

    ⚠️ `end` 必须由「**次日**本地零点」换算得出。可以写成 `本地起点 + timedelta(days=1)`
    （`ZoneInfo` 的加法是墙钟算术，夏令时那天会自己吸收掉那 1 小时），但**绝不能**拿
    换算完的 UTC 起点再 `+ timedelta(days=1)` —— 那是绝对 24 小时，2026-03-08 的纽约
    只有 23 小时、2026-11-01 有 25 小时，会各错 1 小时。

    ⚠️ 本函数假设该市场的当地零点既不重复也不缺失（上海无夏令时；美东在 02:00 切换）。
    若将来接入零点切换的时区，这里要补 fold 处理。
    """
    tz = tz_of(market)
    d = day if day is not None else market_today(market)
    start_local = datetime.combine(d, time.min, tzinfo=tz)
    end_local = datetime.combine(d + timedelta(days=1), time.min, tzinfo=tz)
    target = _LOCAL if storage == "naive_local" else UTC
    return (start_local.astimezone(target).replace(tzinfo=None),
            end_local.astimezone(target).replace(tzinfo=None))


# ──────────────── 序列化 ────────────────

def utc_iso(dt: datetime | None) -> str | None:
    """存储时刻 → 带显式 offset 的 ISO 串（`2026-07-22T02:24:53+00:00`）。

    **后端返给前端的时间字段一律走它**。带 offset 是关键：前端 `new Date(iso)` 会自动
    转成浏览器本地时区；不带 offset 的裸串（`str(dt)` / `dt.isoformat()` 的 naive 形态）
    会被 JS 当成本地时间解析，UTC 存储 + 裸串 = 整整差一个时区。
    """
    if dt is None:
        return None
    return _as_aware_utc(dt).isoformat()

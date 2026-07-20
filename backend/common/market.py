"""市场分类的单一真源（canonical = a_share / hk_stock / us_stock）。

全项目历史上有多套市场写法：短大写 `A/HK/US`、混合 `us/hk`、kebab `a-stock`、
中文 `沪深京` 等。为避免跨模块传值踩雷，统一约定：

- 任何外部/历史写法进入业务逻辑前，先经 `normalize_market()` 归一到 canonical。
- symbol 后缀 → 市场 的推断统一走 `infer_market_from_symbol()`，供 data_engine.factory、
  backtest portfolio、news_scheduler 等处委托，不再各写各的后缀判断。
"""
from typing import Literal

# canonical 市场标识
Market = Literal["a_share", "hk_stock", "us_stock", "crypto"]

A_SHARE = "a_share"
HK_STOCK = "hk_stock"
US_STOCK = "us_stock"
CRYPTO = "crypto"

CANONICAL_MARKETS = (A_SHARE, HK_STOCK, US_STOCK, CRYPTO)

# 股票三市场（工作日交易 + 固定收盘）。crypto 是 7×24 独立链，不属此列 ——
# 凡是「按交易日历/全局新鲜度聚合」的口径都该用这个，而非含 crypto 的 CANONICAL_MARKETS
# （否则空/滞后的 crypto 表会永久拉红全局 is_stale）。data_engine.market_refresh /
# health.get_freshness 聚合处复用本常量。
STOCK_MARKETS = (A_SHARE, HK_STOCK, US_STOCK)

# 各种历史/外部写法 → canonical 的别名表（比较前统一 strip + lower；中文无大小写）
_MARKET_ALIASES = {
    # A股
    "a_share": A_SHARE,
    "a-stock": A_SHARE,
    "a": A_SHARE,
    "ashare": A_SHARE,
    "cn": A_SHARE,
    "沪深京": A_SHARE,
    "沪深": A_SHARE,
    "a股": A_SHARE,
    # 港股
    "hk_stock": HK_STOCK,
    "hk-stock": HK_STOCK,
    "hk": HK_STOCK,
    "hkstock": HK_STOCK,
    "港股": HK_STOCK,
    # 美股
    "us_stock": US_STOCK,
    "us-stock": US_STOCK,
    "us": US_STOCK,
    "usstock": US_STOCK,
    "美股": US_STOCK,
    # 加密货币（币安现货）
    "crypto": CRYPTO,
    "binance": CRYPTO,
    "bn": CRYPTO,
    "币": CRYPTO,
    "加密货币": CRYPTO,
    "数字货币": CRYPTO,
}


def normalize_market(raw: str | None, default: str = A_SHARE) -> str:
    """把任意市场写法归一到 canonical（a_share/hk_stock/us_stock）。

    Args:
        raw: 任意来源的市场字符串（'A'/'us'/'hk-stock'/'沪深京'/None …）
        default: 无法识别时的兜底，默认 A股（与既有费率表 fallback 口径一致）

    Returns:
        canonical 市场标识
    """
    if raw is None:
        return default
    key = str(raw).strip().lower()
    if not key:
        return default
    return _MARKET_ALIASES.get(key, default)


# C++ 回测服务(backtest_cpp/src/server.cpp)分派用的是短写：us / hk / a_share，
# 与项目 canonical(us_stock/hk_stock)不同。所有发往 C++ 的请求 body 都要经本函数翻译，
# 把这个 wire 协议差异隔离在边界，业务层一律传 canonical。
_CPP_WIRE = {A_SHARE: "a_share", HK_STOCK: "hk", US_STOCK: "us", CRYPTO: "crypto"}


def to_cpp_market(raw: str | None) -> str:
    """把任意市场写法翻成 C++ 回测服务认的 wire 写法（us/hk/a_share）。"""
    return _CPP_WIRE.get(normalize_market(raw), "a_share")


def infer_market_from_symbol(symbol: str | None) -> str:
    """按 symbol 后缀推断市场：.SH/.SZ/.BJ = A股，.HK = 港股，.BN = 加密货币，其余（纯字母 ticker）= 美股。

    symbol 为空时兜底返回 a_share。全项目后缀推断的唯一实现，其余处委托本函数。

    加密货币统一带 `.BN` 后缀存（`BTCUSDT.BN`）——币安交易对本身无后缀，裸 `BTCUSDT`
    会落进美股兜底分支，故必须带后缀显式标识，调币安 API 前用 `to_binance_symbol()` 剥。
    """
    if not symbol:
        return A_SHARE
    s = str(symbol).strip().upper()
    if s.endswith(".HK"):
        return HK_STOCK
    if s.endswith((".SH", ".SZ", ".BJ")):
        return A_SHARE
    if s.endswith(".BN"):
        return CRYPTO
    return US_STOCK


# ── symbol 形态转换（全项目唯一实现，各处委托本模块，勿再手写）────────────
A_SHARE_SUFFIXES = (".SH", ".SZ", ".BJ")
KNOWN_SUFFIXES = A_SHARE_SUFFIXES + (".HK",)

Asset = Literal["stock", "index"]

# A股代码段 → 交易所。用 2 位前缀而非枚举 3 位段，才能覆盖新开的代码段
# （如 302xxx 中航成飞、689xxx 九号公司，都是后来才有的）。
#
# 匹配顺序有讲究：`900`(沪B) 必须先于 `92`(北交所) 命中，否则 920xxx 会被吞成 .SH。
_STOCK_RULES: tuple[tuple[tuple[str, ...], str], ...] = (
    (("900",), "SH"),                              # 沪B（长前缀优先，勿下移）
    (("200",), "SZ"),                              # 深B
    (("60", "68"), "SH"),                          # 沪主板 / 科创板
    (("00", "30"), "SZ"),                          # 深主板·中小 / 创业板
    (("43", "83", "87", "88", "920"), "BJ"),       # 北交所
)

# 指数与个股的代码段会撞车：000001.SH 是上证指数，000001.SZ 是平安银行。
# 光看代码无法区分，所以 add_exchange_suffix 必须由调用方声明 asset。
_INDEX_RULES: tuple[tuple[tuple[str, ...], str], ...] = (
    (("000",), "SH"),                              # 上证系列（000001/000300/…）
    (("399",), "SZ"),                              # 深证系列（399001/399006/…）
)


def to_bare_code(symbol: str) -> str:
    """剥掉交易所后缀取裸码：`600000.SH` → `600000`，`00700.HK` → `00700`（保前导零）。

    只剥已知后缀，**不做裸 split(".")** —— 美股 ticker 可以带点（`BRK.B`），
    盲切会把它砍成 `BRK`。美股无后缀，原样返回。
    """
    s = str(symbol).strip()
    up = s.upper()
    for suffix in KNOWN_SUFFIXES:
        if up.endswith(suffix):
            return s[: -len(suffix)]
    return s


def add_exchange_suffix(code: str, *, asset: Asset = "stock") -> str:
    """给 A股 6 位裸码补交易所后缀（.SH/.SZ/.BJ）。已带已知后缀的输入原样返回。

    只服务 A股：港股/美股不经本函数（美股是裸 ticker，港股后缀随数据源给定）。
    A股 ETF 已于 2026-07-09 全面停用，故不设 etf 档——其代码段（51x/15x 等）
    会走到未知分支抛 ValueError，这是期望行为。

    Args:
        code: 6 位纯数字裸码，如 `600000`。
        asset: `stock`（默认）或 `index`。用于消解同码歧义——`000001` 作个股是
            平安银行(.SZ)，作指数是上证指数(.SH)，光看代码无解。

    Returns:
        带后缀的 symbol，如 `600000.SH`。

    Raises:
        ValueError: code 不是 6 位纯数字，或代码段不属于任何已知交易所。
            不静默返回原样——脏数据要当场暴露，不要沿着管线往下漂。
    """
    s = str(code).strip()
    if s.upper().endswith(KNOWN_SUFFIXES):
        return s
    if len(s) != 6 or not s.isdigit():
        raise ValueError(f"A股裸码必须是 6 位纯数字，收到 {code!r}")

    rules = _INDEX_RULES if asset == "index" else _STOCK_RULES
    for prefixes, exchange in rules:
        if s.startswith(prefixes):
            return f"{s}.{exchange}"
    raise ValueError(f"未知的 A股{'指数' if asset == 'index' else '个股'}代码段: {code!r}")


def to_yf_symbol(symbol: str) -> str:
    """转成 yfinance 认的 symbol：港股 5 位 `00700.HK` → 4 位 `0700.HK`。

    项目内港股统一存 5 位，但 yfinance 只认 4 位，喂 5 位会返回
    "possibly delisted; no price data found"。只在实际调 yfinance 前转换，
    数据库/前端展示不受影响。A股/美股原样返回。
    """
    s = str(symbol).strip()
    if s.upper().endswith(".HK"):
        code = s[:-3]
        if len(code) == 5 and code.isdigit():
            return f"{code[1:]}.HK"
    return s


def to_binance_symbol(symbol: str) -> str:
    """转成币安 API 认的交易对：项目内 `BTCUSDT.BN` → 币安 `BTCUSDT`。

    加密货币项目内统一带 `.BN` 后缀（供市场推断），但币安 REST 只认裸交易对。
    只在实际调币安 API 前转换，数据库/前端展示不受影响。非加密 symbol 原样返回。
    与 `to_yf_symbol` 同一模式：symbol 双形态，出网前剥后缀。
    """
    s = str(symbol).strip()
    if s.upper().endswith(".BN"):
        return s[:-3]
    return s

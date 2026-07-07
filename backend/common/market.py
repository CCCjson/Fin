"""市场分类的单一真源（canonical = a_share / hk_stock / us_stock）。

全项目历史上有多套市场写法：短大写 `A/HK/US`、混合 `us/hk`、kebab `a-stock`、
中文 `沪深京` 等。为避免跨模块传值踩雷，统一约定：

- 任何外部/历史写法进入业务逻辑前，先经 `normalize_market()` 归一到 canonical。
- symbol 后缀 → 市场 的推断统一走 `infer_market_from_symbol()`，供 data_engine.factory、
  backtest portfolio、news_scheduler 等处委托，不再各写各的后缀判断。
"""
from typing import Optional, Literal

# canonical 市场标识
Market = Literal["a_share", "hk_stock", "us_stock"]

A_SHARE = "a_share"
HK_STOCK = "hk_stock"
US_STOCK = "us_stock"

CANONICAL_MARKETS = (A_SHARE, HK_STOCK, US_STOCK)

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
}


def normalize_market(raw: Optional[str], default: str = A_SHARE) -> str:
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
_CPP_WIRE = {A_SHARE: "a_share", HK_STOCK: "hk", US_STOCK: "us"}


def to_cpp_market(raw: Optional[str]) -> str:
    """把任意市场写法翻成 C++ 回测服务认的 wire 写法（us/hk/a_share）。"""
    return _CPP_WIRE.get(normalize_market(raw), "a_share")


def infer_market_from_symbol(symbol: Optional[str]) -> str:
    """按 symbol 后缀推断市场：.SH/.SZ/.BJ = A股，.HK = 港股，其余（纯字母 ticker）= 美股。

    symbol 为空时兜底返回 a_share。全项目后缀推断的唯一实现，其余处委托本函数。
    """
    if not symbol:
        return A_SHARE
    s = str(symbol).strip().upper()
    if s.endswith(".HK"):
        return HK_STOCK
    if s.endswith((".SH", ".SZ", ".BJ")):
        return A_SHARE
    return US_STOCK

"""港股正股/ETF vs 人民币柜台/债券 粗分类 —— 只有正股+ETF 值得抓行情。

对照 `us_filter.py`（同目录）：那边靠「代码正则 + 名称关键词」，港股这边**主要靠
代码段**，因为港交所的代码段划分是硬规矩、比名字可靠得多。

分类结果落到 `StockInfo.stock_type`（港股此前 4699 只**全是 `stock`**，从没被分过类），
让「重新分类」变成一次幂等的本地计算，不需要重新请求数据源。

## 代码段依据（2026-07-17 按库里真实数据核实，不是凭印象）

- **`4xxxx`（310 只）= 债券/票据/结构性产品**：`40939.HK SINOCHEM N2611`、
  `41533.HK XAGX金兑-U`。抓不到日线，白烧请求。
- **`8xxxx`（411 只）= 人民币柜台**：411 只里 409 只名字带 `-R`/`-WR`/`-SWR`/`BR`
  后缀，剩下 2 只（`87001.HK 汇贤产业信托`、`83168.HK 恒生人币金ETF`）同样是人民币
  柜台。它们分两种，**但都该排除**：
  - `89988.HK 阿里巴巴-WR` 这类是**港币柜台的重复**（= `09988.HK 阿里巴巴`），
    同一家公司抓两遍，还会让选股/统计重复计数
  - `89021.HK 国债四一零四-R` 这类是国债
- **`0xxxx`（3978 只）= 正股**（含创业板 GEM `08xxx`，它在 5 位归一化后也是 0 开头）。

## ⚠️ REIT 归 stock，不归 etf

`00823.HK 领展房产基金`、`02778.HK 冠君产业信托` 是 **REIT**，是正经权益资产
（领展还是港股大蓝筹）。**按「基金」「信托」关键词筛 ETF 会把它们误杀** ——
所以 ETF 关键词只认 `ETF` 本身。代价：`02800.HK 盈富基金`、`02821.HK 沛富基金`
这两只真 ETF 会被标成 `stock`。**无害** —— stock 和 etf 都在抓取范围内（口径同
`overseas_job` 的 `stock_type.in_(["stock", "etf"])`），这个标签只影响可读性，
真正承重的区分是「excluded 与否」。宁可把 ETF 标成 stock，不可把领展标成 ETF。
"""

from loguru import logger

from data_engine.storage.database import get_session
from data_engine.storage.models import StockInfo

STOCK_TYPE_STOCK = "stock"
STOCK_TYPE_ETF = "etf"
STOCK_TYPE_EXCLUDED_BOND_NOTE = "excluded_bond_note"        # 与 us_filter 同名，口径一致
STOCK_TYPE_EXCLUDED_RMB_COUNTER = "excluded_rmb_counter"    # 港股特有：人民币双柜台/R 股

# 见模块 docstring「REIT 归 stock」：只认 ETF 字样，不认「基金」「信托」
_ETF_KEYWORDS = ["ETF"]


def classify_hk_symbol(symbol: str, name: str) -> str:
    """单个港股代码+名称 -> 分类结果。

    Args:
        symbol: 库里的 5 位格式，如 `09988.HK`（**不是**喂 yfinance 的 4 位）
        name: 中文名，可能为 None
    """
    code = (symbol or "").split(".")[0]
    if code.startswith("4"):
        return STOCK_TYPE_EXCLUDED_BOND_NOTE
    if code.startswith("8"):
        return STOCK_TYPE_EXCLUDED_RMB_COUNTER
    if any(kw in (name or "") for kw in _ETF_KEYWORDS):
        return STOCK_TYPE_ETF
    return STOCK_TYPE_STOCK


def classify_and_persist_hk_universe() -> dict[str, int]:
    """对 StockInfo 里全部 hk_stock 重新分类并写回 stock_type，返回各分类计数。

    幂等：纯本地字符串匹配，重复调用无副作用；分类规则更新后重跑一次即可刷新。
    形状照抄 `us_filter.classify_and_persist_us_universe`。
    """
    session = get_session()
    counts = {
        STOCK_TYPE_STOCK: 0, STOCK_TYPE_ETF: 0,
        STOCK_TYPE_EXCLUDED_BOND_NOTE: 0, STOCK_TYPE_EXCLUDED_RMB_COUNTER: 0,
    }
    try:
        rows = session.query(StockInfo).filter(StockInfo.market == "hk_stock").all()
        for row in rows:
            stype = classify_hk_symbol(row.symbol, row.name)
            row.stock_type = stype
            counts[stype] += 1
        session.commit()
        logger.info(f"港股分类完成: {counts}")
    finally:
        session.close()
    return counts

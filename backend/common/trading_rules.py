"""**交易单位与结算规则** —— 按市场分叉的那几条硬规矩（S5 批次2）。

## 为什么要单独一层

这三条规则此前散在三个地方，各写各的：

| 规则 | 从前在哪 |
|---|---|
| 每手股数 | `backtest_cpp` 的 `MarketRules`（S8 才加）+ 8 个策略里各手写一份 `/100)*100` |
| T+1 | `BrokerPosition.available` 有这个概念，但 `PaperBroker` 明写「没有 T+1 限制」 |
| 涨跌停 | `common/limit_rules.py`（判定有了）**但执行层从来没用过** |

🔴 **散着写最危险的不是重复，是「A 股规则被套到美股头上」**：
美股 T+0、无涨跌停、1 股一手，三条**全都不适用**。全自动交易一旦把
A 股那套套上去，美股会莫名其妙地「买不进」（凑不满 100 股一手）
或者「卖不掉」（以为要 T+1）—— 而且不报错，只是不成交。

所以收在这一处，按市场查表。⛔ 别再在别处手写 100 或 9.9。

## ⚠️ 与 C++ 回测侧的关系

`backtest_cpp/include/backtest/types.h::MarketRules` 是**回测侧**的同一套规则。
两边必须一致，有门禁 `tests/test_trading_rules.py::test_lot_size_matches_cpp_engine` 盯着 ——
不一致的话「回测能成交、实盘成交不了」，而那种偏差会被当成策略问题查很久。
"""
from dataclasses import dataclass

from common.market import A_SHARE, CRYPTO, HK_STOCK, US_STOCK


@dataclass(frozen=True)
class TradingRules:
    """一个市场的交易单位与结算规则。"""

    lot_size: int
    """一手多少股/多少个。下单量必须是它的整数倍。

    ⚠️ crypto 是 1：BTC 单价 6 万+，按 100 一手算 $10 万本金**连一手都凑不齐**。
    """

    t_plus_one: bool
    """当日买入是否要到下一交易日才可卖。

    🔴 A 股是 True。`PaperBroker` 此前明写「Paper Trading 没有 T+1 限制」——
    那会让纸面成绩**系统性优于实盘**（纸面能当天来回做 T，实盘做不到），
    而「先 paper 跑一段再上实盘」这条护栏正是靠纸面成绩判断的。
    """

    has_price_limit: bool
    """有没有涨跌停。封板时挂单成交不了，回测/实盘都要认这件事。"""

    fractional: bool = False
    """支不支持小数量（现货 crypto 支持，但本引擎全程用整数，见下）。

    ⚠️ 引擎目前全程整数量，crypto 靠**价格缩放**绕过（见
    `crypto_intel_engine/backtest.py`）。这个字段只是把事实记下来，
    ⛔ 别拿它去开一条「小数量下单」的分支 —— 那要动 portfolio/engine/metrics/全部策略。
    """


_RULES: dict[str, TradingRules] = {
    A_SHARE: TradingRules(lot_size=100, t_plus_one=True, has_price_limit=True),
    # 美股：T+0、无涨跌停（有熔断但那是全市场级别，不是个股挂单能感知的）、1 股一手
    US_STOCK: TradingRules(lot_size=1, t_plus_one=False, has_price_limit=False),
    # ⚠️ 港股每手股数**按标的不同**（100/500/1000/2000…），引擎拿不到那张表。
    #    v1 按 1 处理：宁可粒度偏细，也不要凭空按 100 把小额单打掉。
    #    ⛔ 别把它改成 100 —— 那是拿 A 股的常识套港股。
    HK_STOCK: TradingRules(lot_size=1, t_plus_one=False, has_price_limit=False),
    CRYPTO: TradingRules(lot_size=1, t_plus_one=False, has_price_limit=False,
                         fractional=True),
}


def rules_for(market: str) -> TradingRules:
    """取一个市场的交易规则。

    ⛔ 未知市场直接抛，**不回落到 A 股** —— 回落的后果是美股/港股被套上
    「100 股一手 + T+1」，然后静默地买不进也卖不掉。
    """
    r = _RULES.get(market)
    if r is None:
        raise KeyError(f"没有 {market!r} 的交易规则（已知：{sorted(_RULES)}）。"
                       f"⛔ 不会回落到 A 股 —— 那会给这个市场套上 100 股一手 + T+1，"
                       f"然后静默地买不进也卖不掉。")
    return r


def lot_floor(market: str, raw_qty: float) -> int:
    """把理论数量向下取整到整手；不足一手返回 0。

    ⛔ 别在别处手写 `int(x/100)*100` —— 那是 A 股假设，对美股/crypto 直接失效
    （全项目已清零，C++ 侧有门禁）。
    """
    lot = max(1, rules_for(market).lot_size)
    if not (raw_qty > 0):
        return 0
    return int(raw_qty // lot) * lot


def can_sell_today(market: str, bought_today_qty: int, holding_qty: int) -> int:
    """今天最多能卖多少股（T+1 市场里当日买入的部分是冻结的）。

    Args:
        bought_today_qty: 今天买入的数量
        holding_qty: 当前总持仓
    """
    if not rules_for(market).t_plus_one:
        return max(0, holding_qty)
    return max(0, holding_qty - max(0, bought_today_qty))


def is_tradable_at(market: str, symbol: str, change_pct: float | None,
                   side: str, name: str | None = None) -> tuple[bool, str | None]:
    """封板时能不能成交。返回 `(可交易, 不可交易的原因)`。

    - 涨停板上**买不进**（排队买单成交不了）
    - 跌停板上**卖不出**
    ⚠️ 反过来是可以的：涨停可以卖、跌停可以买。

    ⛔ 判定走 `common/limit_rules`（全项目唯一实现），别在这里手写 9.9/10/20。
    北交所返回 None 阈值 = 本功能不覆盖 → 一律放行（不假装知道）。
    """
    if not rules_for(market).has_price_limit or change_pct is None:
        return True, None
    from common.limit_rules import is_limit_down, is_limit_up

    if side.upper() == "BUY" and is_limit_up(symbol, change_pct, name):
        return False, "涨停封板，买单排不进去"
    if side.upper() == "SELL" and is_limit_down(symbol, change_pct, name):
        return False, "跌停封板，卖单出不掉"
    return True, None

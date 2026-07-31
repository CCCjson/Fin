"""crypto 组合回测封装（S8 批次 2）。

## 这里真正要钉死的那件事

单标的 crypto 回测靠「价格 × k 缩到 $10 量级」绕开 C++ 引擎的整数股粒度。
组合回测里**每个币的 k 都不一样**（BTC 的 k 是 1.5e-4，DOGE 的可能是 60），
看上去像是把一份共享现金拿去跟一堆量纲不一的价格做加减。

它其实是对的，因为账本上参与加减的两个量都**缩放不变**：
  - 买入花的钱 `qty × (close×k) ≈ cash×w`（k 约掉）
  - 持仓市值 `qty × close_t × k ∝ close_t/close_0`（只反映这个币自己的涨跌）

但「我推导过所以它是对的」不算数 —— 下面那条 `test_returns_are_invariant_to_scale`
拿真的 C++ 引擎跑一遍：把某个币的价格整体乘 1000（于是它的 k 反过来除 1000），
组合的 total_return 必须一模一样。

⚠️ 需要 C++ 回测服务在 :8002 上活着，否则整个模块跳过。
"""
import pytest

from crypto_intel_engine.backtest import run_crypto_portfolio_backtest
from services.backtest_cpp_client import CPP_SERVICE_URL


def _cpp_alive() -> bool:
    try:
        import requests
        # ⚠️ `proxies={...: None}` 不能省：`.env` 里有 HTTP_PROXY 且没有 NO_PROXY，
        # 少了它连本机 8002 都要走 Clash（见 backtest_cpp_client._NO_PROXY）。
        requests.get(f"{CPP_SERVICE_URL}/api/strategies", timeout=1.5,
                     proxies={"http": None, "https": None})
        return True
    except Exception:  # noqa: BLE001
        return False


pytestmark = pytest.mark.skipif(
    not _cpp_alive(),
    reason="C++ 回测服务未启动（cd backtest_cpp/build && ./backtest_server）")


def _bars(prices: list[float], start_day: int = 5) -> list[dict]:
    return [{"date": f"2026-01-{start_day + i:02d}",
             "open": p, "high": p, "low": p, "close": p, "volume": 1_000_000}
            for i, p in enumerate(prices)]


# 一段温和上涨；两个币走势相同，只有价位量级差 1000 倍
_TREND = [100.0, 101.0, 103.0, 106.0, 110.0, 115.0, 121.0, 128.0]
_SIGNALS = [{"date": "2026-01-06", "action": "buy", "weight": 0.4}]


def test_result_is_invariant_to_raw_price_magnitude():
    """⭐ **这是生产上真正要的那条性质**：币的绝对价位不影响组合收益。

    真实 universe 里 BTC 是 6 万、DOGE 是 0.15 —— 差 5 个数量级。
    只要走势相同，组合收益就该相同。`_pick_scale` 逐币把价格归一到 $10 量级，
    正是为了让**账本永远见不到混合量纲**。

    对照组把两个币的价位换成 64000 和 0.15（走势一模一样），
    结果必须与「两个都在 100」一致。
    """
    both_100 = run_crypto_portfolio_backtest(
        {"AAA": _bars(_TREND), "BBB": _bars(_TREND)},
        {"AAA": _SIGNALS, "BBB": _SIGNALS}, initial_capital=100000.0)

    wild = run_crypto_portfolio_backtest(
        {"AAA": _bars([p * 640 for p in _TREND]),        # ≈ BTC
         "BBB": _bars([p * 0.0015 for p in _TREND])},    # ≈ DOGE
        {"AAA": _SIGNALS, "BBB": _SIGNALS}, initial_capital=100000.0)

    a, b = both_100["metrics"]["total_return"], wild["metrics"]["total_return"]
    assert a == pytest.approx(b, rel=1e-3), (
        f"价位从 100 换成 64000/0.0015，组合收益就从 {a} 变成 {b}")
    # 两条腿的 k 确实差了 5 个数量级（否则上面那条断言是废的）
    ks = wild["_price_scale"]
    assert ks["AAA"] / ks["BBB"] < 1e-4


def test_pick_scale_keeps_every_leg_in_the_same_magnitude():
    """🔴 **账本的安全边界就靠这一条**。

    ⚠️ 「每个币各用自己的 k、所以账本上混着不同量纲」这个说法**是错的** ——
    实测过：绕开 `_pick_scale` 直接送两条相差 1000 倍的腿进去，
    组合收益从 0.1968 变成 0.1729（**12% 偏差**），因为高价位那条腿
    一笔单只买得起个位数股，取整误差爆掉。

    所以缩放不变性不是「混合 k 无害」，而是「`_pick_scale` **不让**账本见到混合量纲」。
    ⛔ 别把 `_pick_scale` 改成「全场共用一个 k」或者去掉它。
    """
    from crypto_intel_engine.backtest import _pick_scale, _scale_bars

    raws = {"BTC": _bars([64000.0 * p / 100 for p in _TREND]),
            "DOGE": _bars([0.15 * p / 100 for p in _TREND]),
            "ETH": _bars([3200.0 * p / 100 for p in _TREND])}

    mids = []
    for bars in raws.values():
        k = _pick_scale(bars)
        closes = sorted(b["close"] for b in _scale_bars(bars, k))
        mids.append(closes[len(closes) // 2])

    assert max(mids) / min(mids) < 2.0, \
        f"缩放后各腿的量级没对齐：{mids} —— 账本会见到混合量纲"


def test_pick_scale_survives_a_dirty_first_bar():
    """🔴 一根脏的首 bar 曾经能让**整个币静默消失**。

    `_pick_scale` 旧实现按首根 close 定 k：`close=1e-8` 打头 → k=1e9 →
    后续价格全变 6e13 → 算出 0 股 → **一单不下、零异常、零日志**。
    在多币组合里它只表现为「收益率略低」，看不出是哪个币出了问题。
    """
    dirty = [{"date": "2026-01-05", "open": 1e-8, "high": 1e-8,
              "low": 1e-8, "close": 1e-8, "volume": 1.0}]
    dirty += _bars([64000.0 * (p / 100.0) for p in _TREND], start_day=6)

    res = run_crypto_portfolio_backtest(
        {"BTC": dirty},
        {"BTC": [{"date": "2026-01-07", "action": "buy", "weight": 0.9}]},
        initial_capital=100000.0)
    assert res["trades"], "首根 bar 是脏数据，但这个币不该因此整条静默"


def test_two_coins_actually_share_one_wallet():
    """两个币各要 95% 现金，账上只有一份 —— 花掉的合计不可能超过本金。"""
    res = run_crypto_portfolio_backtest(
        {"AAA": _bars(_TREND), "BBB": _bars(_TREND)},
        {"AAA": [{"date": "2026-01-06", "action": "buy", "weight": 0.95}],
         "BBB": [{"date": "2026-01-06", "action": "buy", "weight": 0.95}]},
        initial_capital=100000.0)

    spent = sum(t["price"] * t["quantity"] + t["commission"] for t in res["trades"])
    assert spent <= 100000.0 + 1e-6
    assert len(res["trades"]) == 2, "两个币都该买到一点（等比缩减，不是先到先得）"
    assert res["cash_contention"]["days"] >= 1, "抢钱这件事得报出来"


_FLAT = [100.0] * 8


def test_cash_floor_is_enforced_at_order_time():
    """🔒 传了 `max_total_position_pct=0.8` 就必须留住 20% 现金。

    ⚠️ 用**平盘**价格序列，因为这条上限管的是**下单那一刻**：
    见下面 `test_cap_does_not_force_selling_on_appreciation` —— 买完之后行情涨上去，
    持仓占比自然会漂过 80%，那不是违规。
    """
    res = run_crypto_portfolio_backtest(
        {"AAA": _bars(_FLAT), "BBB": _bars(_FLAT)},
        {"AAA": [{"date": "2026-01-06", "action": "buy", "weight": 0.95}],
         "BBB": [{"date": "2026-01-06", "action": "buy", "weight": 0.95}]},
        initial_capital=100000.0,
        risk_config={"enabled": True, "max_total_position_pct": 0.8})

    for snap in res["equity_curve"]:
        assert snap["market_value"] / snap["total_value"] <= 0.81, \
            f"{snap['date']} 持仓超过 80%，20% 现金保护没生效"


def test_cap_does_not_force_selling_on_appreciation():
    """⭐ 买完之后涨上去、持仓占比漂过 80% —— **不该强制卖出**。

    这条上限是**下单闸门**（不让你买到 80% 以上），不是**再平衡器**。
    真要因为升值就自动减仓，会在牛市里不停砍掉赢利仓位、白交手续费，
    而且那等于规则替 Jason 做了减仓决策（`00-PLAN §1.2` 第 3 条：
    规则是判据，不是决策者）。

    ⛔ 别看到「持仓 85% > 80%」就以为闸门漏了 —— 先看是不是涨出来的。
    """
    res = run_crypto_portfolio_backtest(
        {"AAA": _bars(_TREND)},
        {"AAA": [{"date": "2026-01-06", "action": "buy", "weight": 0.95}]},
        initial_capital=100000.0,
        risk_config={"enabled": True, "max_total_position_pct": 0.8})

    ratios = [s["market_value"] / s["total_value"] for s in res["equity_curve"]]
    assert max(ratios) > 0.81, "价格从 100 涨到 128，占比本来就该漂上去"
    sells = [t for t in res["trades"] if t["side"] == "SELL"]
    assert not sells, "升值触发了强制卖出 —— 这条上限被误当成再平衡器了"


def test_high_priced_coin_can_still_trade():
    """BTC 那种 6 万块的币也得买得进去。

    ⚠️ 别在这里写「一手 100 股的话就零成交」—— **那句话是错的**（审查证伪）：
    价格缩放已经把它压到 $10 量级，lot=100 也照样成交。
    「crypto 一手必须是 1 股」的真实理由是**取整误差**（资金被 N 个币摊薄后
    从 0.1% 放大到 5-10%），那件事由 C++ 侧的
    `PortfolioEngineTest.CryptoLotSizeIsOneSoHighPricedCoinsCanTrade` 钉着。
    """
    res = run_crypto_portfolio_backtest(
        {"BTC": _bars([64000.0 * (p / 100.0) for p in _TREND])},
        {"BTC": [{"date": "2026-01-06", "action": "buy", "weight": 0.9}]},
        initial_capital=100000.0)
    assert res["trades"], "六万块的币也得买得进去"


def test_signals_without_bars_raise_instead_of_being_dropped():
    """⛔ 有信号却没 bars 的币**直接报错**。

    静默丢掉的话，「10 个币的组合」会悄悄变成 3 个币，
    而 total_return 看上去一切正常 —— 这正是本项目反复踩的那类坑。
    """
    with pytest.raises(ValueError, match="ETHUSDT.BN"):
        run_crypto_portfolio_backtest(
            {"BTCUSDT.BN": _bars(_TREND)},
            {"BTCUSDT.BN": _SIGNALS, "ETHUSDT.BN": _SIGNALS})


def test_bars_without_signals_are_market_context_only():
    """只给 bars 不给 signals 的币参与行情但不下单（配对交易的另一条腿）。"""
    res = run_crypto_portfolio_backtest(
        {"AAA": _bars(_TREND), "BBB": _bars(_TREND)},
        {"AAA": _SIGNALS},
        initial_capital=100000.0)
    assert {t["symbol"] for t in res["trades"]} == {"AAA"}
    assert set(res["bar_coverage"]) == {"AAA", "BBB"}, "BBB 仍要作为行情参与"


def test_engine_version_is_reported():
    """口径变更要能被追溯：结果里带引擎版本。"""
    res = run_crypto_portfolio_backtest(
        {"AAA": _bars(_TREND)}, {"AAA": _SIGNALS})
    assert res["engine_version"] == "cpp-backtest-v2-portfolio"

"""crypto_intel_engine 纯函数（不出网）：BTC 大势判定 + 回测价格缩放。

出网/回测服务部分（btc_regime 拉币安、run_crypto_backtest 打 8002）不在单测跑，
靠手动/集成验收。这里只钉死可离线验证的逻辑。
"""
import pandas as pd

from crypto_intel_engine.backtest import _pick_scale, _scale_bars, _scale_signal_prices
from crypto_intel_engine.regime import compute_regime


class TestRegime:
    def test_bull_when_price_above_200ma(self):
        df = pd.DataFrame({"close": [100 + i for i in range(210)]})  # 单调升 → 末值远高于均线
        r = compute_regime(df)
        assert r["regime"] == "bull"
        assert r["above"] is True
        assert r["pct_from_ma"] > 0

    def test_bear_when_price_below_200ma(self):
        df = pd.DataFrame({"close": [500 - i for i in range(210)]})  # 单调降 → 末值低于均线
        r = compute_regime(df)
        assert r["regime"] == "bear"
        assert r["above"] is False
        assert r["pct_from_ma"] < 0

    def test_unknown_when_too_few_rows(self):
        df = pd.DataFrame({"close": [100 + i for i in range(50)]})
        assert compute_regime(df)["regime"] == "unknown"

    def test_empty_df(self):
        assert compute_regime(pd.DataFrame())["regime"] == "unknown"


class TestBacktestScaling:
    """价格缩放是经济等价变换：把高价币缩到 $10 量级让整数股 math 成立。"""

    def test_pick_scale_targets_ten_dollars(self):
        bars = [{"close": 64000.0}, {"close": 65000.0}]
        k = _pick_scale(bars)
        assert abs(64000.0 * k - 10.0) < 1e-9   # 首根缩到 $10

    def test_pick_scale_fallback_when_empty(self):
        assert _pick_scale([]) == 1.0
        assert _pick_scale([{"close": 0}]) == 1.0

    def test_scale_bars_multiplies_ohlc_not_volume(self):
        bars = [{"date": "2026-01-01", "open": 100, "high": 110, "low": 90, "close": 100, "volume": 5}]
        out = _scale_bars(bars, 0.1)
        assert out[0]["open"] == 10 and out[0]["high"] == 11 and out[0]["close"] == 10
        assert out[0]["volume"] == 5   # 成交量不缩放

    def test_scale_signal_prices_only_when_present(self):
        sigs = [{"date": "d", "action": "buy", "price": 200}, {"date": "d2", "action": "sell"}]
        out = _scale_signal_prices(sigs, 0.1)
        assert out[0]["price"] == 20
        assert "price" not in out[1]   # 没 price 的信号不动


class TestResolver:
    def test_base_asset_strips_bn_and_quote(self):
        from crypto_intel_engine.resolver import base_asset
        assert base_asset("BTCUSDT.BN") == "BTC"
        assert base_asset("ETHUSDT.BN") == "ETH"
        assert base_asset("SOLUSDC.BN") == "SOL"

    def test_curated_ids_no_network(self):
        from crypto_intel_engine.resolver import resolve_coingecko_id
        cid, ambiguous = resolve_coingecko_id("BTCUSDT.BN")
        assert cid == "bitcoin" and ambiguous is False


class TestRiskScore:
    """排雷扣分制（纯函数，不出网）：主流币高分、坑币被扣。"""

    def _md(self, circ, max_sup, mcap, fdv):
        return {"circulating_supply": circ, "max_supply": max_sup,
                "market_cap": {"usd": mcap}, "fully_diluted_valuation": {"usd": fdv}}

    def test_bitcoin_like_is_pristine(self):
        from crypto_intel_engine.scorer import score_dimensions, verdict_of
        dims, score, flags = score_dimensions(
            self._md(20e6, 21e6, 1.3e12, 1.3e12), {"commit_count_4_weeks": 108})
        assert score == 100 and not flags and verdict_of(score) == "pass"

    def test_no_max_supply_flagged(self):
        from crypto_intel_engine.scorer import score_dimensions
        _, score, flags = score_dimensions(
            self._md(120e6, None, 2e11, 2e11), {"commit_count_4_weeks": 40})
        assert score == 88
        assert any("无供应上限" in f for f in flags)

    def test_high_fdv_ratio_and_dead_dev_and_small_cap_avoid(self):
        from crypto_intel_engine.scorer import score_dimensions, verdict_of
        # FDV/市值=5(-25) + 零提交(-30) + 小市值(-20) + 无上限(-12) = 13
        dims, score, flags = score_dimensions(
            self._md(1e8, None, 5e7, 2.5e8), {"commit_count_4_weeks": 0})
        assert dims["fdv_mcap_ratio"] == 5.0
        assert score == 13 and verdict_of(score) == "avoid"
        assert any("解锁砸盘" in f for f in flags)
        assert any("开发停滞" in f for f in flags)


class TestStore:
    """crypto 情报落库幂等（in-memory db，不出网）。"""

    def _session(self, tmp_path, monkeypatch):
        monkeypatch.setenv("DATABASE_URL", f"sqlite:///{tmp_path / 's.db'}")
        import importlib

        import data_engine.storage.database as dbmod
        importlib.reload(dbmod)
        import data_engine.storage.models  # noqa: F401 -- 注册表
        importlib.reload(data_engine.storage.models)
        dbmod.init_db()
        return dbmod.get_session()

    def test_upsert_metric_idempotent(self, tmp_path, monkeypatch):
        from datetime import date

        from crypto_intel_engine.store import upsert_metric
        s = self._session(tmp_path, monkeypatch)
        d = date(2026, 7, 20)
        upsert_metric(s, "BTCUSDT.BN", d, "funding_rate", 0.0001, "binance")
        s.commit()
        upsert_metric(s, "BTCUSDT.BN", d, "funding_rate", 0.0002, "binance")  # 覆盖
        s.commit()
        from sqlalchemy import text
        rows = s.execute(text("SELECT value FROM crypto_metrics WHERE symbol='BTCUSDT.BN'")).fetchall()
        assert len(rows) == 1 and rows[0][0] == 0.0002   # 幂等：只一行、取新值
        s.close()

    def test_upsert_metric_skips_none(self, tmp_path, monkeypatch):
        from datetime import date

        from crypto_intel_engine.store import upsert_metric
        s = self._session(tmp_path, monkeypatch)
        upsert_metric(s, "X.BN", date(2026, 7, 20), "oi", None, "binance")  # None 不写空洞
        s.commit()
        from sqlalchemy import text
        assert s.execute(text("SELECT COUNT(*) FROM crypto_metrics")).fetchone()[0] == 0
        s.close()

    def test_upsert_asset_overwrites(self, tmp_path, monkeypatch):
        from crypto_intel_engine.store import upsert_asset
        s = self._session(tmp_path, monkeypatch)
        upsert_asset(s, "ETHUSDT.BN", {"base_asset": "ETH", "market_cap": 1.0, "inflation_flag": 1})
        upsert_asset(s, "ETHUSDT.BN", {"base_asset": "ETH", "market_cap": 2.0, "inflation_flag": 1})
        from sqlalchemy import text
        rows = s.execute(text("SELECT market_cap FROM crypto_assets WHERE symbol='ETHUSDT.BN'")).fetchall()
        assert len(rows) == 1 and rows[0][0] == 2.0
        s.close()


def test_crypto_tables_create(tmp_path, monkeypatch):
    """三张 crypto 表能被 create_all 建出来（ORM 定义有效）。"""
    db = tmp_path / "t.db"
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{db}")
    import importlib

    import data_engine.storage.database as dbmod
    importlib.reload(dbmod)
    import data_engine.storage.models as models
    importlib.reload(models)
    dbmod.init_db()
    import sqlite3
    con = sqlite3.connect(db)
    names = {r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    con.close()
    assert {"crypto_metrics", "token_unlocks", "crypto_assets"} <= names


# ════════════════════ 币版驾驶舱：融合打分 + 价位 + 仓位 ════════════════════
class TestCryptoCockpitScoring:
    """择时三维打分 + 排雷否决闸（纯函数，离线）。"""

    def test_derivatives_hot_vs_fear(self):
        from crypto_intel_engine.scorer import score_derivatives
        hot, _ = score_derivatives({"funding": {"funding_rate": 0.001},
                                    "long_short": {"ratio": 1.5}})
        fear, _ = score_derivatives({"funding": {"funding_rate": -0.001},
                                     "long_short": {"ratio": 0.7}})
        assert hot < 50 < fear                       # 过热偏空、恐慌偏多
        assert score_derivatives(None)[0] is None

    def test_regime_bull_vs_bear(self):
        from crypto_intel_engine.scorer import score_regime
        bull, _ = score_regime({"regime": "bull"}, {"fear_greed": {"value": 20}})
        bear, _ = score_regime({"regime": "bear"}, {"fear_greed": {"value": 80}})
        assert bull > 65 and bear < 35

    def test_screen_veto_caps_below_hold(self):
        """🔒 排雷 avoid = 否决闸：择时再高也压到 HOLD 线以下（不给买）。"""
        from crypto_intel_engine.scorer import SCREEN_VETO_CAP, score_crypto_cockpit
        high = {"technical": 85, "derivatives": 75, "regime": 80}
        no_veto = score_crypto_cockpit(high, {"verdict": "pass"})
        assert no_veto["recommendation"] == "BUY"
        veto = score_crypto_cockpit(high, {"verdict": "avoid"})
        assert veto["composite"] <= SCREEN_VETO_CAP
        assert veto["recommendation"] == "SELL"
        assert "capped_by_screen_veto" in veto["adjustments"]

    def test_caution_soft_penalty(self):
        from crypto_intel_engine.scorer import score_crypto_cockpit
        high = {"technical": 85, "derivatives": 75, "regime": 80}
        base = score_crypto_cockpit(high, {"verdict": "pass"})["composite"]
        caution = score_crypto_cockpit(high, {"verdict": "caution"})["composite"]
        assert caution < base                        # 温和扣分，不否决

    def test_missing_dimension_renormalizes(self):
        from crypto_intel_engine.scorer import score_crypto_cockpit
        r = score_crypto_cockpit({"technical": 70, "derivatives": None, "regime": None},
                                 {"verdict": "pass"})
        assert r["dimension_coverage"] < 1.0
        assert r["composite"] == 70.0                # 单维=该维分（重归一）


class TestCryptoLevels:
    """币专属价位：更宽止损 + 小币价格自适应精度。"""

    def test_wide_stop_3x_atr(self):
        from crypto_intel_engine.cockpit import crypto_dynamic_levels
        lv = crypto_dynamic_levels(100.0, [{"close": 100}] * 5, atr=2.0, signal_type="BUY")
        assert lv["atr_stop_loss"] == 94.0           # 100 - 3×2（股票是 2×）
        assert lv["sl_atr_mult"] == 3.0

    def test_small_coin_precision_kept(self):
        from crypto_intel_engine.cockpit import crypto_dynamic_levels
        lv = crypto_dynamic_levels(0.005, [{"close": 0.005}] * 5, atr=0.0002)
        assert lv["atr_stop_loss"] > 0               # 8 位精度，不被 round(,2) 抹成 0


class TestCryptoSizing:
    """币仓位换算：目标%→币量（LOT_SIZE 取整 + MIN_NOTIONAL + 风控）。"""

    def test_sizes_to_target_pct(self):
        from crypto_intel_engine.cockpit import size_crypto_position
        bi = {"cash": 1000.0, "market_value": 0, "total_value": 1000,
              "positions": {}, "recent_closed_pnls": [], "last_loss_date": None}
        sz = size_crypto_position("BTCUSDT.BN", 60000.0, 20.0, broker_info=bi,
                                  total_capital=1000.0, max_position_pct=0.2,
                                  step_size=0.00001, min_qty=0.00001, min_notional=5.0)
        assert sz["affordable"] is True
        assert sz["amount_usdt"] == 199.8            # ~20% of 1000
        assert sz["capped_by"] == "target"

    def test_below_min_notional_not_affordable(self):
        from crypto_intel_engine.cockpit import size_crypto_position
        bi = {"cash": 3.0, "market_value": 0, "total_value": 15,
              "positions": {}, "recent_closed_pnls": [], "last_loss_date": None}
        sz = size_crypto_position("BTCUSDT.BN", 60000.0, 20.0, broker_info=bi,
                                  total_capital=15.0, max_position_pct=0.2,
                                  step_size=0.00001, min_qty=0.00001, min_notional=5.0)
        assert sz["affordable"] is False

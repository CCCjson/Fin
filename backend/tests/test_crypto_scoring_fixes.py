"""批3 打分层误判：下架误伤 / 负止损 / fail-open / 缺数据当利多 / 分位并列。

共同主题：**「不知道」不等于「没问题」**，更不等于「利多」。
"""
import pytest


class TestDelistingFalsePositive:
    """币安退役小众计价币（BIDR/AEUR/TUSD）是例行操作，不该把 BTC/ETH 自己否决 14 天。"""

    @staticmethod
    def _ann(title):
        from datetime import datetime, timezone
        return [{"title": title, "published_at": datetime.now(timezone.utc), "url": "x"}]

    def test_quote_currency_retirement_does_not_veto_majors(self):
        from crypto_intel_engine.news import check_hard_events
        t = "Binance Will Remove BTC/BIDR, ETH/BIDR and USDT/BIDR Spot Trading Pairs"
        assert check_hard_events("BTCUSDT.BN", announcements=self._ann(t))["veto"] is False
        assert check_hard_events("ETHUSDT.BN", announcements=self._ann(t))["veto"] is False

    def test_real_delisting_still_vetoes(self):
        """别把修复做成放行：对主流计价币的交易对被摘 = 真下架，必须照样否决。"""
        from crypto_intel_engine.news import check_hard_events
        t = "Binance Will Delist XVG/USDT, XVG/BTC Spot Trading Pairs"
        r = check_hard_events("XVGUSDT.BN", announcements=self._ann(t))
        assert r["veto"] is True and r["events"][0]["event"] == "delisting"

    def test_bare_named_delisting_vetoes(self):
        from crypto_intel_engine.news import is_asset_delisted
        assert is_asset_delisted("Binance Will Delist ALPACA", "ALPACA") is True

    def test_asset_as_quote_leg_is_not_delisted(self):
        """`ALPACA/BTC` 说的是 ALPACA，不是 BTC（计价腿不算被点名）。"""
        from crypto_intel_engine.news import is_asset_delisted
        assert is_asset_delisted("Binance Will Remove ALPACA/BTC Spot Trading Pairs",
                                 "BTC") is False

    def test_non_delisting_events_unaffected(self):
        """只有下架类走这道额外闸，被盗/监管照旧直接否决。

        用 4 字符 ticker：≤3 字符的短 ticker 有「只认括号形式」的防误伤规则（既有设计），
        会让裸词标题匹配不上，那是另一条独立的保守规则，不该混进本用例。
        """
        from crypto_intel_engine.news import check_hard_events
        t = "CAKE protocol exploited, funds drained"
        assert check_hard_events("CAKEUSDT.BN", announcements=self._ann(t))["veto"] is True


class TestAtrStopFloor:
    """高波动币（meme/新币日 ATR 到价格 20-35%）不能算出负价止损。"""

    QUOTES = [{"close": 1.0, "high": 1.3, "low": 0.7, "open": 1.0} for _ in range(40)]

    def test_high_atr_stop_stays_positive_and_flagged(self):
        from crypto_intel_engine.cockpit import crypto_dynamic_levels
        r = crypto_dynamic_levels(1.0, self.QUOTES, atr=0.35)
        assert r["atr_stop_loss"] > 0            # 旧实现是 -0.05
        assert r["stop_clamped"] is True         # 且如实标出「这不是正常状态」

    def test_normal_atr_unchanged(self):
        from crypto_intel_engine.cockpit import crypto_dynamic_levels
        r = crypto_dynamic_levels(1.0, self.QUOTES, atr=0.02)
        assert r["atr_stop_loss"] == pytest.approx(0.94)
        assert r["stop_clamped"] is False

    def test_sell_side_take_profit_never_below_zero(self):
        from crypto_intel_engine.cockpit import crypto_dynamic_levels
        r = crypto_dynamic_levels(1.0, self.QUOTES, atr=0.35, signal_type="SELL")
        assert all(lv["price"] > 0 for lv in r["take_profit_levels"])


class TestEventVetoFailOpen:
    """⛔ 「源挂了」必须能和「查过了没问题」区分开。"""

    def test_fetch_failure_returns_none_not_empty_list(self, monkeypatch):
        from acquisition.markets import crypto_news
        monkeypatch.setattr(crypto_news, "_crawler",
                            lambda: type("C", (), {"get_json": lambda *a, **k: None})())
        assert crypto_news.fetch_binance_announcements() is None   # 旧实现返回 []

    def test_genuinely_empty_stays_empty_list(self, monkeypatch):
        """真的没公告仍然返回 []，别把修复做成「永远 None」。"""
        from acquisition.markets import crypto_news
        ok = {"code": "000000", "data": {"catalogs": []}}
        monkeypatch.setattr(crypto_news, "_crawler",
                            lambda: type("C", (), {"get_json": lambda *a, **k: ok})())
        assert crypto_news.fetch_binance_announcements() == []

    def test_check_hard_events_reports_not_checked(self, monkeypatch):
        from crypto_intel_engine import news
        monkeypatch.setattr(news, "recent_announcements", lambda page_size=20: None)
        r = news.check_hard_events("BTCUSDT.BN")
        assert r["checked"] is False and r["veto"] is False

    def test_empty_list_means_really_checked(self, monkeypatch):
        from crypto_intel_engine import news
        monkeypatch.setattr(news, "recent_announcements", lambda page_size=20: [])
        assert news.check_hard_events("BTCUSDT.BN")["checked"] is True

    def test_failures_are_not_cached(self, monkeypatch):
        """失败结果不进缓存——否则一次抖动把「没查成」钉死 600 秒覆盖整轮扫描。"""
        from crypto_intel_engine import context
        context.clear_cache()
        calls = {"n": 0}

        def _producer():
            calls["n"] += 1
            return None if calls["n"] == 1 else ["ok"]

        assert context._cached("k", _producer) is None
        assert context._cached("k", _producer) == ["ok"]      # 立刻重试而非吃缓存
        assert calls["n"] == 2


class TestScreenUnknownDowngrade:
    """排雷「压根没查成」不能和「体检满分」走同一条路。"""

    def test_unresolvable_coin_marks_not_checked(self, monkeypatch):
        # 这些符号在函数体内延迟 import，补丁要打到**源模块**上
        from crypto_intel_engine import resolver, scorer
        monkeypatch.setattr(resolver, "resolve_coingecko_id", lambda s: (None, False))
        r = scorer.screen_coin("WEIRDUSDT.BN")
        # cockpit 侧读的是 screen.get("hard_events_checked", True)，键缺失会默认 True
        assert r["hard_events_checked"] is False
        assert r["verdict"] == "unknown"

    def test_unknown_verdict_penalises_composite(self):
        from crypto_intel_engine.scorer import SCREEN_UNKNOWN_PENALTY, score_crypto_cockpit
        good = {"timing": 80.0, "derivatives": 80.0, "regime": 80.0}
        base = score_crypto_cockpit(good, screen_result={"verdict": "pass"})["composite"]
        unk = score_crypto_cockpit(good, screen_result={"verdict": "unknown"})["composite"]
        assert unk == pytest.approx(base - SCREEN_UNKNOWN_PENALTY)
        assert "screen_unknown_penalty" in unk_adjustments(good)

    def test_avoid_still_capped(self):
        from crypto_intel_engine.scorer import SCREEN_VETO_CAP, score_crypto_cockpit
        good = {"timing": 90.0, "derivatives": 90.0, "regime": 90.0}
        r = score_crypto_cockpit(good, screen_result={"verdict": "avoid"})
        assert r["composite"] == SCREEN_VETO_CAP


def unk_adjustments(dims):
    from crypto_intel_engine.scorer import score_crypto_cockpit
    return score_crypto_cockpit(dims, screen_result={"verdict": "unknown"})["adjustments"]


class TestMissingDataNotBullish:
    """缺数据被映射到量表极值端、方向还偏多——这类 bug 里最坏的一种。"""

    def test_empty_screen_data_is_not_pass(self):
        from crypto_intel_engine.scorer import score_dimensions, verdict_of
        dims, score, flags = score_dimensions({}, {}, {})
        assert verdict_of(score) != "pass"        # 旧实现 83 分 pass
        assert any("缺失" in f for f in flags)
        assert len(dims["core_fields_missing"]) >= 3

    def test_full_data_still_passes(self):
        """别把修复做成「谁都不给过」。"""
        from crypto_intel_engine.scorer import score_dimensions, verdict_of
        md = {"circulating_supply": 1e8, "max_supply": 1e8,
              "market_cap": {"usd": 5e9}, "fully_diluted_valuation": {"usd": 5e9}}
        _, score, _ = score_dimensions(md, {"commit_count_4_weeks": 50}, {})
        assert verdict_of(score) == "pass"

    def test_missing_fear_greed_is_neutral_not_extreme_fear(self):
        from crypto_intel_engine.scorer import score_regime
        missing, _ = score_regime({"regime": "bull"}, {"fear_greed": {"value": None}})
        extreme, _ = score_regime({"regime": "bull"}, {"fear_greed": {"value": 10}})
        assert missing < extreme          # 缺值不该拿到「极度恐惧」的 +8 看多加分

    def test_fear_greed_fetch_maps_missing_to_none(self, monkeypatch):
        from acquisition.markets import crypto_intel

        class _Resp:
            @staticmethod
            def raise_for_status():
                return None

            @staticmethod
            def json():
                return {"data": [{"value_classification": "x"}]}      # 没有 value 字段

        monkeypatch.setattr(crypto_intel, "make_session",
                            lambda ch: type("S", (), {"get": lambda *a, **k: _Resp()})())
        assert crypto_intel.fear_greed_index()[0]["value"] is None    # 旧实现是 0=极度恐惧

    def test_missing_btc_dominance_is_none_not_zero(self, monkeypatch):
        """0 是真值会被落库，次日算出 -54 个百分点的假摆动。"""
        from acquisition.markets import crypto_intel
        from crypto_intel_engine import scorer
        monkeypatch.setattr(crypto_intel, "coingecko_global",
                            lambda: {"market_cap_percentage": {}, "total_market_cap": {}})
        monkeypatch.setattr(crypto_intel, "fear_greed_index", lambda limit=1: [])
        assert scorer.market_context()["btc_dominance"] is None


class TestPercentileTies:
    """主流币资金费率长期钉在同一档，只数「严格小于」会把中性值打成极值。"""

    def test_all_ties_is_midpoint(self):
        from crypto_intel_engine.scorer import pctile_score
        assert pctile_score([0.0001] * 40, 0.0001, invert=True) == pytest.approx(50.0)
        assert pctile_score([0.0001] * 40, 0.0001, invert=False) == pytest.approx(50.0)

    def test_genuine_extreme_still_extreme(self):
        from crypto_intel_engine.scorer import pctile_score
        hist = [0.0001] * 39 + [0.0002]
        assert pctile_score(hist, 0.01, invert=True) == pytest.approx(0.0)   # 真的最高
        assert pctile_score(hist, 0.0, invert=True) == pytest.approx(100.0)  # 真的最低

    def test_insufficient_samples_returns_none(self):
        from crypto_intel_engine.scorer import pctile_score
        assert pctile_score([0.1] * 5, 0.1) is None


class TestRegimeFailureDoesNotKillCard:
    """大势取不到应降级成「大势未知」，而不是整张分析卡抛异常没了。"""

    def test_btc_regime_exception_is_contained(self):
        from crypto_intel_engine.cockpit import _safe_ctx

        def _boom():
            raise RuntimeError("binance unreachable")

        # 裸调用会把「大势取不到」升级成「整张分析卡没了」；过 _safe_ctx 只降级成 None
        assert _safe_ctx("BTC大势", _boom) is None

    def test_score_regime_tolerates_none(self):
        """降级后 score_regime 不抛异常，而是标该维不可用（会被排除出加权，不是记 0 分）。"""
        from crypto_intel_engine.scorer import score_regime
        score, detail = score_regime(None, None)
        assert score is None and detail["available"] is False

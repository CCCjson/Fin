"""决策层新信息源的纯函数单测（全部离线，不出网）。

覆盖 2026-07-22 扩容的五维打分与新数据源解析：历史分位、OI×价格四象限、资金流、
新闻事件、真解锁日程、多周期确认。所有被测函数都刻意做成「吃 dict/list 产结果」，
就是为了能这样离线钉死。
"""
from datetime import datetime, timezone

import pytest

from crypto_intel_engine.scorer import (
    NEWS_MIN_ARTICLES,
    _score_oi_price,
    active_weights,
    pctile_score,
    score_derivatives,
    score_dimensions,
    score_flow,
    score_regime,
    score_sentiment,
)

# ──────────────── 历史分位内核 ────────────────

class TestPctile:
    def test_high_value_inverted_scores_low(self):
        """资金费率处在历史高位 → invert 后得低分（多头拥挤=偏空）。"""
        hist = list(range(100))
        assert pctile_score(hist, 95, invert=True) < 20
        assert pctile_score(hist, 5, invert=True) > 80

    def test_non_inverted_follows_rank(self):
        hist = list(range(100))
        assert pctile_score(hist, 95) > 80

    def test_insufficient_samples_returns_none(self):
        """样本不足不硬凑——宁可说不知道，也别拿 5 个点算分位。"""
        assert pctile_score([1, 2, 3], 2) is None
        assert pctile_score(None, 2) is None
        assert pctile_score(list(range(100)), None) is None


# ──────────────── OI × 价格四象限 ────────────────

class TestOiPrice:
    @staticmethod
    def _hist(old, new):
        return [{"oi": old}, {"oi": new}]

    def test_oi_up_price_up_is_strongest(self):
        """新钱做多：趋势有燃料。"""
        s, d = _score_oi_price(self._hist(100, 110), price_change_pct=5.0)
        assert s > 60 and "新钱做多" in d["label"]

    def test_oi_up_price_down_is_weakest(self):
        """空头堆积：杠杆在做空。"""
        s, d = _score_oi_price(self._hist(100, 110), price_change_pct=-5.0)
        assert s < 40 and "空头堆积" in d["label"]

    def test_oi_down_price_up_is_hollow_rally(self):
        """空头回补撑起来的涨，缺新钱 → 低于中性。"""
        s, d = _score_oi_price(self._hist(110, 100), price_change_pct=5.0)
        assert s < 50 and "空头回补" in d["label"]

    def test_missing_inputs_return_none(self):
        assert _score_oi_price(None, 5.0)[0] is None
        assert _score_oi_price(self._hist(100, 110), None)[0] is None
        assert _score_oi_price([{"oi": 0}, {"oi": 5}], 1.0)[0] is None   # 除零保护


# ──────────────── 衍生品维（分位 vs 阈值 双档）────────────────

class TestDerivatives:
    def test_uses_percentile_when_history_available(self):
        hist = [0.0001] * 50            # 历史都很低
        s, d = score_derivatives({"funding": {"funding_rate": 0.01}}, funding_history=hist)
        assert d["funding_basis"] == "percentile"
        assert d["funding_pctile"] is not None
        assert s < 50                    # 当前远高于历史 → 拥挤 → 偏空

    def test_falls_back_to_threshold_and_flags_degraded(self):
        """历史不足时回落固定阈值，并**如实标 degraded**（不假装有分位）。"""
        s, d = score_derivatives({"funding": {"funding_rate": 0.001}}, funding_history=[0.0001])
        assert d["funding_basis"] == "threshold"
        assert d["degraded"] is True

    def test_top_trader_is_directional_long_short_is_contrarian(self):
        """大户持仓比顺向、散户账户比反向 —— 方向必须相反。

        断言看**子信号自己的分**（detail 里的 *_score）而不是维度合成分：这两个子信号权重
        分别只有 0.15 / 0.05，单独可得时不够代表整个衍生品维（合成分会返 None，见
        `_SUBSIGNAL_MIN_WEIGHT`）。本用例要验的是方向，不是覆盖度。
        """
        _, bull = score_derivatives({"top_trader": {"ratio": 2.0}})
        _, bear = score_derivatives({"top_trader": {"ratio": 0.5}})
        assert bull["top_trader_score"] > 50 > bear["top_trader_score"]
        _, retail = score_derivatives({"long_short": {"ratio": 2.0}})
        assert retail["long_short_score"] < 50    # 散户看多 → 反向指标 → 偏空

    def test_single_minor_subsignal_cannot_represent_dimension(self):
        """权重 0.05 的 basis 不许独占整个衍生品维（它占 composite 20%）。"""
        score, detail = score_derivatives({"basis": {"basis_rate": 0.001}})
        assert score is None and detail["available"] is False
        assert detail["weight_covered"] == pytest.approx(0.05)

    def test_enough_subsignal_weight_still_scores(self):
        """别把闸做成「谁都不给分」：funding 权重 0.30 达线，单独可得也要出分。"""
        score, detail = score_derivatives({"funding": {"funding_rate": 0.0001}})
        assert score is not None and detail["weight_covered"] == pytest.approx(0.30)

    def test_components_used_reported(self):
        s, d = score_derivatives({
            "funding": {"funding_rate": 0.0001},
            "taker_flow": {"ratio": 1.2},
            "basis": {"basis_rate": 0.001},
        })
        assert set(d["components_used"]) == {"funding", "taker", "basis"}

    def test_empty_snapshot_returns_none(self):
        assert score_derivatives(None)[0] is None
        assert score_derivatives({})[0] is None


# ──────────────── 资金流维 ────────────────

class TestFlow:
    def test_inflow_beats_outflow(self):
        """稳定币扩张 = 新钱进场，应显著高于收缩场景。"""
        up, _ = score_flow({"stablecoin_change_pct": 3.0})
        down, _ = score_flow({"stablecoin_change_pct": -3.0})
        assert up > 50 > down

    def test_spot_taker_buy_pressure(self):
        """现货主动买入占比 >0.5 = 买方主动。"""
        buy, _ = score_flow({"spot_taker_buy_ratio": 0.6})
        sell, _ = score_flow({"spot_taker_buy_ratio": 0.4})
        assert buy > 50 > sell

    def test_thin_liquidity_is_flag_not_score(self):
        """流动性是风险旗，不是多空信号——不该影响分数方向。"""
        _, d = score_flow({"spot_taker_buy_ratio": 0.5, "quote_volume_24h": 1000.0})
        assert d["thin_liquidity"] is True
        _, d2 = score_flow({"spot_taker_buy_ratio": 0.5, "quote_volume_24h": 1e9})
        assert d2["thin_liquidity"] is False

    def test_liquidity_alone_gives_no_score(self):
        """只有流动性、没有任何方向性信号 → 不给分（它不表达多空）。"""
        assert score_flow({"quote_volume_24h": 1e9})[0] is None

    def test_empty_returns_none(self):
        assert score_flow(None)[0] is None


# ──────────────── 新闻情绪维 ────────────────

class TestSentiment:
    def test_requires_minimum_articles(self):
        """一两篇的情绪是噪声不是信号。"""
        assert score_sentiment({"net_sentiment": 0.9,
                                "article_count": NEWS_MIN_ARTICLES - 1})[0] is None

    def test_positive_and_negative_split_around_50(self):
        pos, _ = score_sentiment({"net_sentiment": 0.5, "article_count": 10})
        neg, _ = score_sentiment({"net_sentiment": -0.5, "article_count": 10})
        assert pos > 50 > neg

    def test_empty_returns_none(self):
        assert score_sentiment(None)[0] is None


# ──────────────── 大势维：主导率方向对 BTC / 山寨相反 ────────────────

class TestRegimeDominance:
    def test_rising_dominance_helps_btc_hurts_alts(self):
        """主导率上升 = 钱往 BTC 集中 → 利好 BTC、利空山寨。"""
        btc, _ = score_regime({"regime": "bull"}, None, dominance_change_pct=3.0, is_btc=True)
        alt, _ = score_regime({"regime": "bull"}, None, dominance_change_pct=3.0, is_btc=False)
        assert btc > alt

    def test_strong_dollar_drags_crypto(self):
        """美元走强 = risk-off，压制加密。"""
        weak, _ = score_regime({"regime": "bull"}, None, macro={"dxy_change_pct": -3.0})
        strong, _ = score_regime({"regime": "bull"}, None, macro={"dxy_change_pct": 3.0})
        assert weak > strong

    def test_stacked_adjustments_cannot_lift_bear_over_midline(self):
        """⛔ 回归：微调项堆叠不得把熊市顶过 50 中轴，否则这道闸名存实亡。

        主导率(+8) + DXY(+6) + 纳指(+5) + 极度恐惧(+8) = +27 → 熊市 30 会变 57。
        总量封顶后最多 42，仍在中轴以下。
        """
        score, detail = score_regime(
            {"regime": "bear"}, {"fear_greed": {"value": 20}},
            dominance_change_pct=-4.0, is_btc=False,
            macro={"dxy_change_pct": -4.0, "nasdaq_change_pct": 10.0})
        assert score <= 42.0
        assert detail["total_adj"] == 12.0        # 分项加总 +27 被封到 +12
        # 牛市侧对称封顶
        bull, _ = score_regime(
            {"regime": "bull"}, {"fear_greed": {"value": 20}},
            dominance_change_pct=-4.0, is_btc=False,
            macro={"dxy_change_pct": -4.0, "nasdaq_change_pct": 10.0})
        assert bull <= 77.0


# ──────────────── 排雷层：真解锁取代 FDV 猜测 ────────────────

class TestScreenUnlock:
    _MD = {"circulating_supply": 1e9, "max_supply": 2e9,
           "market_cap": {"usd": 5e9}, "fully_diluted_valuation": {"usd": 1e10}}
    _DEV = {"commit_count_4_weeks": 50}

    def test_heavier_of_schedule_and_fdv_applies_once(self):
        """日程与 FDV 代理取更重的一个，且**只罚一次**（同一件事不罚两遍）。

        日程重（8% 解锁 → -28）时用日程，FDV 那条 flag 不出现。
        """
        dims, _, flags = score_dimensions(self._MD, self._DEV, {"unlock_pct_30d": 8.0})
        assert dims["unlock_basis"] == "schedule"
        assert not any("FDV/市值" in f for f in flags)
        assert sum(1 for f in flags if "解锁" in f or "未流通" in f) == 1

    def test_clean_30d_schedule_cannot_erase_structural_dilution(self):
        """⛔ 回归：30 天窗口内没解锁 ≠ 没有稀释风险。

        FDV/市值=6（仅 17% 流通）、悬崖落在第 35 天 → 日程给 0%。若「有日程就弃用代理」，
        -25 罚分整个消失、排雷分凭空跳 25 点、verdict 从 avoid 翻成 pass、SCREEN_VETO_CAP
        随之失效 —— 卡片会对一个即将被大幅稀释的币给出买入级结论。
        """
        md = {**self._MD, "market_cap": {"usd": 5e9},
              "fully_diluted_valuation": {"usd": 3e10}}     # FDV/市值 = 6
        dims, score, flags = score_dimensions(md, self._DEV, {"unlock_pct_30d": 0.0})
        assert dims["unlock_basis"] == "fdv_proxy"
        assert any("未流通" in f for f in flags)
        _, no_dilution, _ = score_dimensions(
            {**md, "fully_diluted_valuation": {"usd": 5e9}}, self._DEV,
            {"unlock_pct_30d": 0.0})
        assert no_dilution - score == 25

    def test_fdv_proxy_when_no_schedule(self):
        dims, _, flags = score_dimensions(self._MD, self._DEV)
        assert dims["unlock_basis"] == "fdv_proxy"
        assert any("FDV/市值" in f for f in flags)      # FDV/市值=2.0 → 触发代理扣分

    def test_big_unlock_penalised_hard(self):
        _, light, _ = score_dimensions(self._MD, self._DEV, {"unlock_pct_30d": 0.1})
        _, heavy, flags = score_dimensions(self._MD, self._DEV, {"unlock_pct_30d": 8.0})
        assert heavy < light
        assert any("解锁" in f for f in flags)

    def test_tvl_bleed_and_thin_book_penalised(self):
        _, base, _ = score_dimensions(self._MD, self._DEV, {"unlock_pct_30d": 0.0})
        _, bleed, _ = score_dimensions(self._MD, self._DEV,
                                       {"unlock_pct_30d": 0.0, "tvl_change_pct": -40.0})
        _, thin, _ = score_dimensions(self._MD, self._DEV,
                                      {"unlock_pct_30d": 0.0, "thin_liquidity": True})
        assert bleed < base and thin < base


# ──────────────── 事件硬否决：命中即 avoid，且不误伤 ────────────────

class TestHardEvents:
    def test_classifies_delisting_and_hacks(self):
        from crypto_intel_engine.news import classify_event
        assert classify_event("Notice of Removal of Spot Trading Pairs")["event"] == "delisting"
        assert classify_event("Protocol X exploited for $200M")["event"] == "security"
        assert classify_event("SEC sues Foo Labs over token sales")["event"] == "regulatory"
        assert classify_event("Bitcoin rallies to new highs") is None

    def test_short_tickers_need_parentheses(self):
        """OP/ID 这类短 ticker 只认括号形式，否则误伤率高到不可用。"""
        from crypto_intel_engine.news import mentions_asset
        assert mentions_asset("Removal of Spot Trading Pairs (OP/USDT)", "OP") is True
        assert mentions_asset("Binance opens new options desk", "OP") is False

    def test_long_tickers_allow_word_boundary(self):
        from crypto_intel_engine.news import mentions_asset
        assert mentions_asset("AERGO will be delisted", "AERGO") is True
        assert mentions_asset("Unrelated news about markets", "AERGO") is False

    def test_pair_form_matches(self):
        from crypto_intel_engine.news import mentions_asset
        assert mentions_asset("Delist USDⓈ-M AERGOUSDT Perpetual", "AERGO") is True

    def test_aliases_catch_coin_names(self):
        """媒体标题写「Bitcoin」不写「BTC」——没别名的话主流币新闻一条都匹配不上。"""
        from crypto_intel_engine.news import asset_aliases, mentions_asset
        title = "Bitcoin rallies past $70k as ETF inflows resume"
        assert mentions_asset(title, "BTC") is False                    # 只靠 ticker 匹配不上
        assert mentions_asset(title, "BTC", aliases=["bitcoin"]) is True
        assert asset_aliases("BTCUSDT.BN") == ["bitcoin"]

    def test_hard_veto_stays_ticker_strict(self):
        """🔒 硬否决**不吃别名**：避免「Bitcoin ETF 报道里出现 hack」误否决 BTC。"""
        from crypto_intel_engine.news import check_hard_events
        anns = [{"title": "Bitcoin bridge exploited for $200M",
                 "published_at": datetime.now(tz=timezone.utc), "url": "u"}]
        assert check_hard_events("BTCUSDT.BN", anns)["veto"] is False

    def test_veto_only_on_matching_asset(self):
        """公告说的是别的币 → 不该否决本币。"""
        from crypto_intel_engine.news import check_hard_events
        anns = [{"title": "Notice of Removal of Spot Trading Pairs (AERGO/USDT)",
                 "published_at": datetime.now(tz=timezone.utc), "url": "u"}]
        assert check_hard_events("AERGOUSDT.BN", anns)["veto"] is True
        assert check_hard_events("BTCUSDT.BN", anns)["veto"] is False

    def test_stale_announcement_ignored(self):
        """两个月前的下架公告不该继续否决。"""
        from datetime import timedelta

        from crypto_intel_engine.news import check_hard_events
        old = datetime.now(tz=timezone.utc) - timedelta(days=60)
        anns = [{"title": "Removal of Spot Trading Pairs (AERGO/USDT)",
                 "published_at": old, "url": "u"}]
        assert check_hard_events("AERGOUSDT.BN", anns)["veto"] is False

    def test_quote_leg_of_pair_is_not_a_mention(self):
        """⛔ 回归：`ALPACA/BTC` 说的是 ALPACA，不是 BTC。

        币安每月一次的「移除 BTC/ETH 计价交易对」例行公告，此前会把 BTC 和 ETH 自己
        硬否决掉 14 天 —— Jason 最常交易的两个币被打成回避。
        """
        from crypto_intel_engine.news import mentions_asset
        title = "Binance Will Remove ALPACA/BTC, VIDT/BTC and WING/ETH Spot Trading Pairs"
        assert mentions_asset(title, "BTC") is False       # 报价腿 ≠ 提及
        assert mentions_asset(title, "ETH") is False
        assert mentions_asset(title, "ALPACA") is True     # 基础腿才是被说的币
        # 真的下架 BTC 交易对时仍要认出来（BTC 在斜杠左边）
        assert mentions_asset("Binance Will Remove BTC/TUSD Spot Trading Pair", "BTC") is True

    def test_delisting_notice_of_btc_pairs_does_not_veto_btc(self):
        """端到端：报价腿误判会一路走到 veto=True，这里锁死整条链。"""
        from crypto_intel_engine.news import check_hard_events
        anns = [{"title": "Binance Will Remove ALPACA/BTC and WING/ETH Spot Trading Pairs",
                 "published_at": datetime.now(tz=timezone.utc), "url": "u"}]
        assert check_hard_events("BTCUSDT.BN", anns)["veto"] is False
        assert check_hard_events("ETHUSDT.BN", anns)["veto"] is False
        assert check_hard_events("ALPACAUSDT.BN", anns)["veto"] is True

    def test_fetch_failure_marks_check_not_run(self, monkeypatch):
        """⛔ 回归：fail-open 可以，但「压根没查」必须留痕，不能与「查过了没问题」同形。"""
        from crypto_intel_engine import news
        monkeypatch.setattr(news, "recent_announcements", lambda **k: None)
        res = news.check_hard_events("BTCUSDT.BN")
        assert res["veto"] is False and res["checked"] is False
        # 取到数（哪怕是空列表）才算查过
        monkeypatch.setattr(news, "recent_announcements", lambda **k: [])
        assert news.check_hard_events("BTCUSDT.BN")["checked"] is True

    def test_screen_flags_unchecked_veto_gate(self, monkeypatch):
        """排雷卡要把「否决闸没跑」写进 flags，供 cockpit reasons 透给 Jason。"""
        from crypto_intel_engine import scorer
        # screen_coin 内部是延迟 import，必须打真源模块而不是 scorer 的属性
        monkeypatch.setattr("crypto_intel_engine.resolver.resolve_coingecko_id",
                            lambda s: ("bitcoin", False))
        monkeypatch.setattr("acquisition.markets.crypto_intel.coingecko_coin", lambda cid: {
            "market_data": {"circulating_supply": 1e7, "max_supply": 2e7,
                            "market_cap": {"usd": 5e9},
                            "fully_diluted_valuation": {"usd": 5e9}},
            "developer_data": {"commit_count_4_weeks": 50}, "categories": []})
        monkeypatch.setattr("crypto_intel_engine.news.recent_announcements", lambda **k: None)
        res = scorer.screen_coin("BTCUSDT.BN", extras={"unlock_pct_30d": 0.0})
        assert res["hard_events_checked"] is False
        assert any("事件否决检查未执行" in f for f in res["flags"])


# ──────────────── 解锁数据解析（DefiLlama 累计曲线差分）────────────────

class TestUnlockParsing:
    _NOW = 1_784_620_800.0     # 固定参考时刻，避免测试依赖真实时间

    def _payload(self):
        day = 86400
        return {
            "documentedData": {"data": [
                {"label": "Investors", "data": [
                    {"timestamp": self._NOW - day, "unlocked": 100.0},
                    {"timestamp": self._NOW + 10 * day, "unlocked": 150.0},
                    {"timestamp": self._NOW + 40 * day, "unlocked": 900.0},   # 30天窗外
                ]},
                {"label": "Team", "data": [
                    {"timestamp": self._NOW - day, "unlocked": 50.0},
                    {"timestamp": self._NOW + 20 * day, "unlocked": 70.0},
                ]},
            ]},
            "metadata": {"events": [
                {"timestamp": self._NOW - day, "noOfTokens": [5.0], "category": "past",
                 "unlockType": "cliff"},
                {"timestamp": self._NOW + 5 * day, "noOfTokens": [30.0], "category": "insiders",
                 "unlockType": "cliff"},
                {"timestamp": self._NOW + 9 * day, "noOfTokens": [10.0, 25.0],
                 "category": "team", "unlockType": "linear"},
            ]},
        }

    def test_upcoming_is_cumulative_diff_within_window(self):
        """累计曲线差分：窗口内 (150-100) + (70-50) = 70，40 天后那笔不算。"""
        from acquisition.markets.crypto_onchain import parse_upcoming_unlock_tokens
        got = parse_upcoming_unlock_tokens(self._payload(), days=30, now_ts=self._NOW)
        assert got == pytest.approx(70.0)

    def test_no_data_returns_none(self):
        from acquisition.markets.crypto_onchain import parse_upcoming_unlock_tokens
        assert parse_upcoming_unlock_tokens({}, now_ts=self._NOW) is None

    def test_events_only_future_and_take_last_token_value(self):
        """linear 变更事件的 noOfTokens 是 [旧值, 新值] → 取新值。"""
        from acquisition.markets.crypto_onchain import parse_unlock_events
        evs = parse_unlock_events(self._payload(), now_ts=self._NOW)
        assert len(evs) == 2                      # 过去那条被过滤
        assert [e["category"] for e in evs] == ["insiders", "team"]
        assert evs[1]["amount"] == 25.0


# ──────────────── 多周期确认 ────────────────

class TestTimeframeAlignment:
    @staticmethod
    def _bars(trend_up: bool, n: int = 40):
        return [{"close": (100 + i) if trend_up else (100 - i)} for i in range(n)]

    def test_aligned_boosts_diverged_cuts(self):
        from crypto_intel_engine.cockpit import score_timeframe_alignment
        up, d_up = score_timeframe_alignment(70.0, self._bars(True))
        down, d_down = score_timeframe_alignment(70.0, self._bars(False))
        assert d_up["aligned"] is True and up > 70.0        # 日线看多 + 4h 看多 → 加分
        assert d_down["aligned"] is False and down < 70.0   # 背离 → 打折

    def test_insufficient_bars_passes_through_untouched(self):
        """4h 数据不够就原样返回日线分，绝不瞎调。"""
        from crypto_intel_engine.cockpit import score_timeframe_alignment
        s, d = score_timeframe_alignment(70.0, self._bars(True, n=5))
        assert s == 70.0 and d["available"] is False

    def test_both_bearish_must_not_add_points(self):
        """⛔ 回归：日线看空 + 4h 也看空曾被判「同向」而**加分**，把 46 抬到 54 越过中轴。

        技术分是「看多程度」，4h 只能往它自己的方向推 —— 4h 确认下跌必须压分。
        上面那条只测了 daily_score=70，看空同向这个分支从没被覆盖过。
        """
        from crypto_intel_engine.cockpit import score_timeframe_alignment
        score, detail = score_timeframe_alignment(46.0, self._bars(False))
        assert score < 46.0
        assert detail["aligned"] is True          # 「同向」仍如实记录，只是不再当加号用
        assert detail["adjustment"] < 0

    def test_bullish_4h_lifts_even_when_daily_bearish(self):
        """反向对称：日线看空但 4h 转多 → 加分（背离，但方向由 4h 定）。"""
        from crypto_intel_engine.cockpit import score_timeframe_alignment
        score, detail = score_timeframe_alignment(46.0, self._bars(True))
        assert score > 46.0 and detail["aligned"] is False


# ──────────────── 权重口径与回滚开关 ────────────────

class TestWeights:
    def test_v2_is_five_dimensions_and_sums_to_one(self):
        w = active_weights()
        assert set(w) == {"technical", "derivatives", "regime", "flow", "sentiment"}
        assert sum(w.values()) == pytest.approx(1.0)

    def test_env_rollback_to_v1(self, monkeypatch):
        """🔒 出事一键退回三维旧口径。"""
        monkeypatch.setenv("CRYPTO_SCORER", "v1")
        assert set(active_weights()) == {"technical", "derivatives", "regime"}

    def test_v1_rollback_ignores_new_dimensions(self, monkeypatch):
        """回滚后即便传入 flow/sentiment 也不该 KeyError，直接忽略。"""
        from crypto_intel_engine.scorer import score_crypto_cockpit
        monkeypatch.setenv("CRYPTO_SCORER", "v1")
        r = score_crypto_cockpit(
            {"technical": 70, "derivatives": 60, "regime": 50, "flow": 90, "sentiment": 90},
            {"verdict": "pass"})
        assert set(r["available_dimensions"]) == {"technical", "derivatives", "regime"}

    def test_new_dimensions_participate_in_v2(self):
        from crypto_intel_engine.scorer import score_crypto_cockpit
        low = score_crypto_cockpit({"technical": 70, "flow": 10, "sentiment": 10},
                                   {"verdict": "pass"})["composite"]
        high = score_crypto_cockpit({"technical": 70, "flow": 90, "sentiment": 90},
                                    {"verdict": "pass"})["composite"]
        assert high > low                      # 新维度真的影响结论

    def test_screen_veto_still_wins_over_new_dimensions(self):
        """🔒 红线不变：排雷否决压过一切高分，新维度也不能把它顶回买入区。"""
        from crypto_intel_engine.scorer import SCREEN_VETO_CAP, score_crypto_cockpit
        r = score_crypto_cockpit(
            {"technical": 95, "derivatives": 95, "regime": 95, "flow": 95, "sentiment": 95},
            {"verdict": "avoid"})
        assert r["composite"] <= SCREEN_VETO_CAP
        assert r["recommendation"] != "BUY"

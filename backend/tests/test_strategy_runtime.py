"""股票全自动执行链路（S5 批次2）。

🔒 **这批测试的重点只有一个：证明硬风控绕不过去。**

Jason 2026-08-02 拍板股票走全自动（人只监控），所以下单前**没有人会在中间拦一手**。
CLAUDE.md 那四条红线（总仓位 ≤80% / 单日亏损 ≤3% / 每笔必须止损 / 连亏 3 次暂停）
从此全靠代码把关。
"""
from crypto_intel_engine.dsl import CryptoStrategySpec
from strategy_runtime.executor import decide_symbol, run_tick

# ──────────────────── 测试替身 ────────────────────

class _FakeBroker:
    def __init__(self, positions=None):
        self.positions = positions or {}
        self.submitted = []

    def get_position(self, symbol):
        return self.positions.get(symbol)

    def submit_order(self, symbol, action, quantity, price):
        self.submitted.append((symbol, action, quantity, price))

        class _Status:
            value = "FILLED"

        class _O:
            status = _Status()
            error_msg = None
        return _O()


class _Pos:
    def __init__(self, quantity, available=None):
        self.quantity = quantity
        self.available = quantity if available is None else available


class _FakeAdapter:
    """把分析卡直接喂进来，绕开 CockpitAggregator（本批测的是执行链路不是分析层）。"""

    def __init__(self, cards, broker=None, market="a_share", info=None):
        self.cards = cards
        self.broker = broker or _FakeBroker()
        self.market = market
        self._info = info or {"cash": 1_000_000, "market_value": 0,
                              "total_value": 1_000_000, "positions": {},
                              "recent_closed_pnls": [], "unrealized_pnl": 0}

    def analyze(self, symbol):
        return self.cards.get(symbol)

    def broker_info(self):
        return self._info

    def held_quantity(self, symbol):
        p = self.broker.get_position(symbol)
        return p.quantity if p else 0

    def sellable_quantity(self, symbol):
        p = self.broker.get_position(symbol)
        return p.available if p else 0

    def submit(self, symbol, side, quantity, price):
        return self.broker.submit_order(symbol, side, quantity, price)


class _AllowAll:
    def check_order(self, *a, **kw):
        return True, []


class _DenyAll:
    def __init__(self, msg="风控拒绝：总仓位会超过 80%"):
        self.msg = msg
        self.calls = []

    def check_order(self, symbol, side, qty, price, info):
        self.calls.append((symbol, side, qty, price))

        class _R:
            passed = False
        _R.message = self.msg
        return False, [_R()]


def _card(price=100.0, composite=70.0, change_pct=1.0, suggested=0.2):
    return {"price": {"latest": price, "change_pct": change_pct},
            "composite": composite,
            "suggested": {"target_pct": suggested},
            "name": "测试股"}


def _spec(market="a_share", symbols=("600519.SH",), entry_gte=60, exit_lt=40):
    return CryptoStrategySpec(
        name="t", market=market, universe={"symbols": list(symbols)},
        entry_rules={"when": {"all_of": [
            {"field": "composite", "op": "gte", "value": entry_gte}]}},
        exit_rules={"when": {"all_of": [
            {"field": "composite", "op": "lt", "value": exit_lt}]}},
        guardrails={"per_order_notional_usdt": 100, "max_orders_per_day": 5})


# ──────────────────── 🔒 风控绕不过去 ────────────────────

class TestRiskGateCannotBeBypassed:
    def test_risk_rejection_blocks_the_order(self):
        adapter = _FakeAdapter({"600519.SH": _card()})
        out = run_tick(_spec(), adapter, capital=1_000_000, risk_manager=_DenyAll())
        assert out["tally"].get("blocked_risk") == 1
        assert adapter.broker.submitted == [], "风控拒绝了却还是下单了"

    def test_risk_sees_the_actual_quantity_not_the_wish(self):
        """🔴 风控必须按**实际要下的量**判。

        取整（100 股一手）和封板都会改变实际下单量 —— 按「策略想要的量」判会漏。
        这里 100 万 × 20% ÷ 100 元 = 2000 股，取整后仍是 2000；
        换成 33 元的价格就是 6060.6 → 取整成 6000，风控要看到 6000。
        """
        rm = _DenyAll()
        adapter = _FakeAdapter({"600519.SH": _card(price=33.0)})
        run_tick(_spec(), adapter, capital=1_000_000, risk_manager=rm)
        assert rm.calls, "风控根本没被调用"
        _, _, qty, _ = rm.calls[0]
        assert qty == 6000, f"风控看到的是 {qty}，不是取整后的实际下单量"
        assert qty % 100 == 0, "A 股必须整手"

    def test_risk_gate_runs_for_sells_too(self):
        """卖出同样过闸 —— ⛔ 别以为「卖出总是安全的」。"""
        rm = _DenyAll("风控拒绝")
        broker = _FakeBroker({"600519.SH": _Pos(1000)})
        adapter = _FakeAdapter({"600519.SH": _card(composite=20.0)}, broker=broker)
        out = run_tick(_spec(), adapter, capital=1_000_000, risk_manager=rm)
        assert out["tally"].get("blocked_risk") == 1
        assert rm.calls[0][1] == "SELL"
        assert broker.submitted == []


# ──────────────────── 市场规则 ────────────────────

class TestMarketRules:
    def test_a_share_rounds_to_whole_lots(self):
        adapter = _FakeAdapter({"600519.SH": _card(price=33.0)})
        run_tick(_spec(), adapter, capital=1_000_000, risk_manager=_AllowAll())
        assert adapter.broker.submitted[0][2] == 6000

    def test_us_stock_keeps_odd_lots(self):
        """⛔ 别把 A 股的整手规则套到美股。"""
        adapter = _FakeAdapter({"AAPL": _card(price=33.0)}, market="us_stock")
        run_tick(_spec(market="us_stock", symbols=("AAPL",)), adapter,
                 capital=1_000_000, risk_manager=_AllowAll())
        assert adapter.broker.submitted[0][2] == 6060

    def test_limit_up_blocks_buying(self):
        """涨停封板买不进去。"""
        adapter = _FakeAdapter({"600519.SH": _card(change_pct=10.0)})
        out = run_tick(_spec(), adapter, capital=1_000_000, risk_manager=_AllowAll())
        assert out["tally"].get("blocked_market") == 1
        assert "涨停" in out["decisions"][0]["reason"]
        assert adapter.broker.submitted == []

    def test_limit_down_blocks_selling(self):
        broker = _FakeBroker({"600519.SH": _Pos(1000)})
        adapter = _FakeAdapter({"600519.SH": _card(composite=20.0, change_pct=-10.0)},
                               broker=broker)
        out = run_tick(_spec(), adapter, capital=1_000_000, risk_manager=_AllowAll())
        assert "跌停" in out["decisions"][0]["reason"]
        assert broker.submitted == []

    def test_us_stock_ignores_price_limits(self):
        """美股涨 30% 照买 —— ⛔ 别拿 A 股的 10% 卡它。"""
        adapter = _FakeAdapter({"AAPL": _card(change_pct=30.0)}, market="us_stock")
        run_tick(_spec(market="us_stock", symbols=("AAPL",)), adapter,
                 capital=1_000_000, risk_manager=_AllowAll())
        assert adapter.broker.submitted, "美股被 A 股的涨跌停规则拦下了"

    def test_frozen_t1_position_cannot_be_sold(self):
        """🔴 A 股当日买入是冻结的 —— 退出条件命中也卖不掉，而且要说清原因。"""
        broker = _FakeBroker({"600519.SH": _Pos(1000, available=0)})
        adapter = _FakeAdapter({"600519.SH": _card(composite=20.0)}, broker=broker)
        out = run_tick(_spec(), adapter, capital=1_000_000, risk_manager=_AllowAll())
        assert "T+1" in out["decisions"][0]["reason"]
        assert broker.submitted == []

    def test_missing_change_pct_does_not_block(self):
        """⛔ 别把「取不到涨跌幅」当成「封板了」——那会让策略在数据缺失时静默停摆。"""
        card = _card()
        card["price"].pop("change_pct")
        adapter = _FakeAdapter({"600519.SH": card})
        run_tick(_spec(), adapter, capital=1_000_000, risk_manager=_AllowAll())
        assert adapter.broker.submitted


# ──────────────────── 决策语义 ────────────────────

class TestDecisionSemantics:
    def test_exit_wins_over_entry(self):
        """持仓时先看退出（与 crypto 引擎同口径）。"""
        broker = _FakeBroker({"600519.SH": _Pos(1000)})
        # 两边都命中：composite=70 既 ≥60（进）又…把 exit 阈值抬到 80 让它也命中
        adapter = _FakeAdapter({"600519.SH": _card(composite=70.0)}, broker=broker)
        out = run_tick(_spec(exit_lt=80), adapter, capital=1_000_000,
                       risk_manager=_AllowAll())
        assert out["decisions"][0]["side"] == "SELL"

    def test_missing_card_is_skipped_with_a_reason(self):
        """⛔ 分析卡取不到 ≠ 条件没命中。两者的下一步动作完全不同。"""
        adapter = _FakeAdapter({"600519.SH": None})
        out = run_tick(_spec(), adapter, capital=1_000_000, risk_manager=_AllowAll())
        assert out["decisions"][0]["status"] == "skipped"
        assert "分析卡" in out["decisions"][0]["reason"]

    def test_sub_strategy_weight_scales_the_budget(self):
        """⭐ 组合策略：这条子策略只分到 `weight × 总资金`。"""
        def _sub(name, w, syms):
            return {"name": name, "weight": w, "universe": {"symbols": syms},
                    "entry_rules": {"when": {"all_of": [
                        {"field": "composite", "op": "gte", "value": 60}]}},
                    "exit_rules": {"when": {"all_of": [
                        {"field": "composite", "op": "lt", "value": 40}]}}}

        spec = CryptoStrategySpec(
            name="组合", market="a_share",
            universe={"symbols": ["600519.SH", "000001.SZ"]},
            sub_strategies=[_sub("大头", 0.8, ["600519.SH"]),
                            _sub("小头", 0.2, ["000001.SZ"])],
            guardrails={"per_order_notional_usdt": 100, "max_orders_per_day": 5})
        adapter = _FakeAdapter({"600519.SH": _card(price=10.0),
                                "000001.SZ": _card(price=10.0)})
        run_tick(spec, adapter, capital=1_000_000, risk_manager=_AllowAll())
        by_sym = {s: q for s, _, q, _ in adapter.broker.submitted}
        # 100万 × weight × 20% ÷ 10元
        assert by_sym["600519.SH"] == 16000
        assert by_sym["000001.SZ"] == 4000

    def test_dry_run_decides_but_does_not_order(self):
        adapter = _FakeAdapter({"600519.SH": _card()})
        out = run_tick(_spec(), adapter, capital=1_000_000,
                       risk_manager=_AllowAll(), dry_run=True)
        assert out["tally"].get("ready") == 1
        assert adapter.broker.submitted == []

    def test_one_bad_symbol_does_not_stop_the_round(self):
        """一个标的炸掉不该让整轮停摆（全自动下这会让别的标的整天不交易）。"""
        class _Boom(_FakeAdapter):
            def analyze(self, symbol):
                if symbol == "600519.SH":
                    raise RuntimeError("分析炸了")
                return _card()

        adapter = _Boom({}, market="a_share")
        out = run_tick(_spec(symbols=("600519.SH", "000001.SZ")), adapter,
                       capital=1_000_000, risk_manager=_AllowAll())
        assert out["tally"].get("skipped") == 1
        assert out["tally"].get("ordered") == 1


def test_decide_symbol_is_pure_enough_to_unit_test():
    """决策与下单分离 —— `decide_symbol` 不碰 broker.submit。"""
    adapter = _FakeAdapter({"600519.SH": _card()})
    d = decide_symbol(_spec(), "600519.SH", adapter, 1_000_000, _AllowAll())
    assert d.status == "ready" and d.side == "BUY"
    assert adapter.broker.submitted == []

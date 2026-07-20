"""币安交易层的可离线测部分：LOT_SIZE 取整 + 无 key 安全路径。

真实下单/账户（需 key + 出网）不在单测里跑，靠 Jason 配 key 后手动小额验收。
"""


class TestRoundStep:
    """LOT_SIZE 向下取整（币安拒单最常见原因）。"""

    def test_round_down_to_step(self):
        from acquisition.markets.binance_trade import round_step
        assert round_step(0.123456, 0.001) == 0.123      # 截到 3 位
        assert round_step(1.9999, 0.01) == 1.99
        assert round_step(15.7, 1.0) == 15.0             # 整数步长

    def test_step_none_or_zero_passthrough(self):
        from acquisition.markets.binance_trade import round_step
        assert round_step(0.123, None) == 0.123
        assert round_step(0.123, 0) == 0.123

    def test_non_power_of_ten_step_never_rounds_up(self):
        """🔒 非 10 幂步长（如 0.005）不许把 floor 结果又抬回上一格。

        旧实现用 log10 推小数位：0.005→2 位，round(0.025,2) 可能变 0.03 越过 floor
        甚至超余额被币安拒。新实现全程 Decimal，floor 到 step 整数倍不反弹。
        """
        from acquisition.markets.binance_trade import round_step
        assert round_step(0.028, 0.005) == 0.025      # 5×0.005，不抬到 0.03
        assert round_step(0.999, 0.005) == 0.995
        assert round_step(2.5, 0.25) == 2.5


class TestRoundPrice:
    """PRICE_FILTER tickSize 对齐（限价单不对齐会被币安 -1013 拒）。"""

    def test_align_to_tick(self):
        from acquisition.markets.binance_trade import round_price
        assert round_price(63456.789, 0.01) == 63456.79   # 对齐到最近格
        assert round_price(100.037, 0.05) == 100.05
        assert round_price(1.23456, 0.0001) == 1.2346

    def test_tick_none_or_zero_passthrough(self):
        from acquisition.markets.binance_trade import round_price
        assert round_price(123.456, None) == 123.456
        assert round_price(123.456, 0) == 123.456


# ── 假 broker（A1/A3 用）──────────────────────────────────────────────────
class _FakeBroker:
    def __init__(self, order=None, account=None, positions=None, price=50000.0):
        self._order = order
        self._account = account or {}
        self._positions = positions or []
        self._price = price
        self._connected = True

    def connect(self):
        return True

    def get_current_price(self, symbol):
        return self._price

    def get_account_info(self):
        return self._account

    def get_positions(self):
        return self._positions

    def submit_order(self, symbol, action, qty, price):
        return self._order


class TestOrderRecording:
    """A1：挂单未成交绝不谎报成交（不发 ORDER_FILLED、不落台账）。"""

    def _run(self, monkeypatch, order):
        """跑 place_crypto_order，风控/留痕全 stub，只验分支逻辑。返回 (env, recorded)。"""
        from acquisition.markets import binance_trade as bt
        from agents.tools import crypto_tools
        from trading_engine.brokers import binance_broker

        monkeypatch.setattr(bt, "has_credentials", lambda: True)
        monkeypatch.setattr(binance_broker, "get_binance_broker",
                            lambda: _FakeBroker(order=order))
        monkeypatch.setattr(crypto_tools, "_crypto_broker_info", lambda broker: {})
        monkeypatch.setattr(crypto_tools, "_risk_check", lambda *a, **k: (True, [], []))

        recorded = {"trade": [], "resting": []}
        monkeypatch.setattr(crypto_tools, "_record_crypto_trade",
                            lambda *a, **k: recorded["trade"].append((a, k)))
        monkeypatch.setattr(crypto_tools, "_record_crypto_resting",
                            lambda *a, **k: recorded["resting"].append((a, k)))
        env = crypto_tools.place_crypto_order("BTCUSDT.BN", "buy", quantity=0.01, price=49000)
        return env, recorded

    def test_resting_limit_order_not_recorded_as_filled(self, monkeypatch):
        from trading_engine.brokers.base import BrokerOrder, OrderStatus
        order = BrokerOrder(order_id="42", symbol="BTCUSDT.BN", action="BUY",
                            quantity=0.01, price=49000, filled_quantity=0,
                            status=OrderStatus.SUBMITTED)
        env, recorded = self._run(monkeypatch, order)
        assert env.data["executed"] is False
        assert env.data["resting"] is True
        assert recorded["trade"] == []          # 绝不落成交台账
        assert len(recorded["resting"]) == 1    # 只记挂单留痕

    def test_full_fill_recorded(self, monkeypatch):
        from trading_engine.brokers.base import BrokerOrder, OrderStatus
        order = BrokerOrder(order_id="43", symbol="BTCUSDT.BN", action="BUY",
                            quantity=0.01, price=49000, filled_quantity=0.01,
                            filled_price=49000, status=OrderStatus.FILLED)
        env, recorded = self._run(monkeypatch, order)
        assert env.data["executed"] is True
        assert env.data["partial"] is False
        assert len(recorded["trade"]) == 1
        assert recorded["trade"][0][0][3] == 0.01   # 记的是成交量

    def test_partial_fill_records_only_filled_qty(self, monkeypatch):
        from trading_engine.brokers.base import BrokerOrder, OrderStatus
        order = BrokerOrder(order_id="44", symbol="BTCUSDT.BN", action="BUY",
                            quantity=0.01, price=49000, filled_quantity=0.004,
                            filled_price=49000, status=OrderStatus.PARTIAL_FILLED)
        env, recorded = self._run(monkeypatch, order)
        assert env.data["executed"] is True
        assert env.data["partial"] is True
        assert recorded["trade"][0][0][3] == 0.004   # 只记已成交那部分


class TestConsecutiveLossReplay:
    """A2：crypto 成交台账回放 → 连亏字段能喂进硬风控（不再硬编码空）。"""

    def _mem_db(self, monkeypatch):
        from sqlalchemy import create_engine
        from sqlalchemy.orm import sessionmaker

        from data_engine.storage import database as db
        from data_engine.storage.models import Base
        engine = create_engine("sqlite:///:memory:")
        Base.metadata.create_all(engine)
        make = sessionmaker(bind=engine)
        monkeypatch.setattr(db, "get_session", lambda: make())
        return make

    def test_three_losing_trades_replayed_newest_first(self, monkeypatch):
        from datetime import date

        from data_engine.storage.models import CryptoTrade
        make = self._mem_db(monkeypatch)
        s = make()
        # 买 1，卖 3 次每次都亏（卖价 < 买入均价）
        s.add(CryptoTrade(symbol="ETHUSDT.BN", side="BUY", price=100, quantity=3,
                          amount=300, commission=0, trade_date=date(2026, 7, 1)))
        for px, d in [(90, 2), (85, 3), (80, 4)]:
            s.add(CryptoTrade(symbol="ETHUSDT.BN", side="SELL", price=px, quantity=1,
                              amount=px, commission=0, trade_date=date(2026, 7, d)))
        s.commit()
        s.close()

        from agents.tools.crypto_tools import get_recent_crypto_closed_pnls
        pnls, last_loss = get_recent_crypto_closed_pnls(limit=10)
        assert len(pnls) == 3
        assert all(p < 0 for p in pnls)          # 三笔全亏
        assert pnls[0] < 0                        # 最新在前
        assert last_loss == "2026-07-04"          # 最近一笔亏损日期

    def test_empty_ledger_returns_neutral(self, monkeypatch):
        self._mem_db(monkeypatch)
        from agents.tools.crypto_tools import get_recent_crypto_closed_pnls
        assert get_recent_crypto_closed_pnls() == ([], None)


class TestAccountStablecoins:
    """A3：稳定币算现金/权益，只有非稳定币才算持仓市值。"""

    def test_locked_usdt_and_other_stables_are_cash_not_positions(self, monkeypatch):
        from acquisition.markets import binance_trade as bt
        from trading_engine.brokers.binance_broker import BinanceBroker

        balances = [
            {"asset": "USDT", "free": "5000", "locked": "2000"},
            {"asset": "USDC", "free": "3000", "locked": "0"},
        ]
        monkeypatch.setattr(bt, "account", lambda: {"balances": balances})
        broker = BinanceBroker()
        broker._connected = True
        acct = broker.get_account_info()
        assert acct["market_value"] == 0.0        # 无币 → 零持仓敞口（原来会虚增）
        assert acct["cash"] == 8000.0             # USDT+USDC 的 free
        assert acct["total_value"] == 10000.0     # 含 2000 锁定 USDT

    def test_coins_counted_at_market(self, monkeypatch):
        from acquisition.markets import binance_trade as bt
        from trading_engine.brokers.binance_broker import BinanceBroker

        balances = [
            {"asset": "USDT", "free": "1000", "locked": "0"},
            {"asset": "BTC", "free": "0.1", "locked": "0"},
        ]
        monkeypatch.setattr(bt, "account", lambda: {"balances": balances})
        monkeypatch.setattr(bt, "ticker_price", lambda sym: 50000.0)
        broker = BinanceBroker()
        broker._connected = True
        acct = broker.get_account_info()
        assert acct["market_value"] == 5000.0     # 0.1 BTC * 50000
        assert acct["cash"] == 1000.0
        assert acct["total_value"] == 6000.0


class TestRealtimeOnDemand:
    """C3：按需拉行情，不空参拉全市场。"""

    def test_single_symbol_uses_symbol_param(self, monkeypatch):
        from acquisition.markets.crypto import CryptoFetcher
        calls = {}

        def fake_get(self, path, params):
            calls["params"] = params
            return {"symbol": "BTCUSDT", "price": "50000"}

        monkeypatch.setattr(CryptoFetcher, "_get", fake_get)
        out = CryptoFetcher().fetch_realtime(["BTCUSDT.BN"])
        assert calls["params"] == {"symbol": "BTCUSDT"}   # 不是空参
        assert out[0]["symbol"] == "BTCUSDT.BN"
        assert out[0]["price"] == 50000.0

    def test_multi_symbol_uses_symbols_param(self, monkeypatch):
        from acquisition.markets.crypto import CryptoFetcher
        calls = {}

        def fake_get(self, path, params):
            calls["params"] = params
            return [{"symbol": "BTCUSDT", "price": "50000"},
                    {"symbol": "ETHUSDT", "price": "3000"}]

        monkeypatch.setattr(CryptoFetcher, "_get", fake_get)
        out = CryptoFetcher().fetch_realtime(["BTCUSDT.BN", "ETHUSDT.BN"])
        assert "symbols" in calls["params"]               # 批量走 symbols=[...]
        assert calls["params"]["symbols"] == '["BTCUSDT","ETHUSDT"]'
        assert len(out) == 2

    def test_empty_returns_empty(self, monkeypatch):
        from acquisition.markets.crypto import CryptoFetcher
        called = {"n": 0}
        monkeypatch.setattr(CryptoFetcher, "_get",
                            lambda self, p, q: called.__setitem__("n", called["n"] + 1))
        assert CryptoFetcher().fetch_realtime([]) == []
        assert called["n"] == 0                            # 空列表不发请求


class TestNoKeySafety:
    """未配 key 时：所有交易类路径诚实拒绝，绝不崩、绝不静默下单。"""

    def test_has_credentials_false_without_env(self, monkeypatch):
        monkeypatch.delenv("BINANCE_API_KEY", raising=False)
        monkeypatch.delenv("BINANCE_API_SECRET", raising=False)
        from acquisition.markets import binance_trade as bt
        assert bt.has_credentials() is False

    def test_place_crypto_order_negative_without_key(self, monkeypatch):
        monkeypatch.delenv("BINANCE_API_KEY", raising=False)
        monkeypatch.delenv("BINANCE_API_SECRET", raising=False)
        from agents.tools.crypto_tools import place_crypto_order
        env = place_crypto_order("BTCUSDT.BN", "buy", quote_amount=100)
        assert env.business_result == "negative"
        assert "key" in env.message.lower() or "未配置" in env.message

    def test_place_crypto_order_requires_confirmation(self):
        """下单工具必须挂二次确认 + 预览（人工审核硬约束的代码承载）。"""
        from agents.registry import REGISTRY
        td = REGISTRY.get("place_crypto_order")
        assert td.requires_confirmation is True
        assert td.preview_fn is not None
        assert td.group == "core"   # 确认续跑依赖它常驻可见

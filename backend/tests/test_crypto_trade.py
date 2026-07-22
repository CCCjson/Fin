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

    def submit_order(self, symbol, action, qty, price, client_order_id=None):
        self.last_client_order_id = client_order_id
        return self._order


class TestOrderRecording:
    """A1：挂单未成交绝不谎报成交（不发 ORDER_FILLED、不落台账）。"""

    def _run(self, monkeypatch, order):
        """跑 place_crypto_order，风控/留痕全 stub，只验分支逻辑。返回 (env, recorded)。"""
        from acquisition.markets import binance_trade as bt
        from agents.tools import crypto_tools
        from trading_engine.brokers import binance_broker

        monkeypatch.setattr(bt, "has_credentials", lambda: True)
        # 现货买力充足 → 不触发买入自动补足（本组只验成交/挂单记账分支）
        monkeypatch.setattr(binance_broker, "get_binance_broker",
                            lambda: _FakeBroker(order=order, account={"spot_cash": 1e9}))
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

    def test_partial_fill_on_terminal_status_still_recorded(self, monkeypatch):
        """币安 EXPIRED→REJECTED 但 executedQty>0：只看 status 会把已买到的部分判成完全失败。

        回归「幽灵持仓 + 重复买入」：不落台账 → 系统以为没花钱 → 账上却真多了币。
        """
        from trading_engine.brokers.base import BrokerOrder, OrderStatus
        order = BrokerOrder(order_id="45", symbol="BTCUSDT.BN", action="BUY",
                            quantity=0.01, price=49000, filled_quantity=0.006,
                            filled_price=49000, status=OrderStatus.REJECTED)
        env, recorded = self._run(monkeypatch, order)
        assert env.data["executed"] is True
        assert env.data["partial"] is True
        assert recorded["trade"][0][0][3] == 0.006   # 成交那部分必须落账

    def test_true_rejection_still_reported_failed(self, monkeypatch):
        """真拒单（零成交 + 终态）仍判失败——别把止血改成放行。"""
        from trading_engine.brokers.base import BrokerOrder, OrderStatus
        order = BrokerOrder(order_id="", symbol="BTCUSDT.BN", action="BUY",
                            quantity=0.01, price=49000, filled_quantity=0,
                            status=OrderStatus.REJECTED, error_msg="余额不足")
        env, recorded = self._run(monkeypatch, order)
        assert env.data["executed"] is False
        assert recorded["trade"] == [] and recorded["resting"] == []

    def test_unknown_state_never_reported_as_failure(self, monkeypatch):
        """状态未知不能说「失败了」——Jason 照着重下就会成交两次。"""
        from trading_engine.brokers.base import BrokerOrder, OrderStatus
        order = BrokerOrder(order_id="", symbol="BTCUSDT.BN", action="BUY",
                            quantity=0.01, price=49000, filled_quantity=0,
                            status=OrderStatus.UNKNOWN, error_msg="回查也超时")
        env, recorded = self._run(monkeypatch, order)
        assert env.data["state_unknown"] is True
        assert recorded["trade"] == [] and recorded["resting"] == []
        assert "可能已经成交" in env.message      # 明确提示别重下

    def test_idempotency_key_is_passed_down(self, monkeypatch):
        """聊天下单也必须带幂等键，否则超时重试会重复成交。"""
        from acquisition.markets import binance_trade as bt
        from agents.tools import crypto_tools
        from trading_engine.brokers import binance_broker
        from trading_engine.brokers.base import BrokerOrder, OrderStatus
        order = BrokerOrder(order_id="46", symbol="BTCUSDT.BN", action="BUY",
                            quantity=0.01, price=49000, filled_quantity=0.01,
                            filled_price=49000, status=OrderStatus.FILLED)
        broker = _FakeBroker(order=order, account={"spot_cash": 1e9})
        monkeypatch.setattr(bt, "has_credentials", lambda: True)
        monkeypatch.setattr(binance_broker, "get_binance_broker", lambda: broker)
        monkeypatch.setattr(crypto_tools, "_crypto_broker_info", lambda b: {})
        monkeypatch.setattr(crypto_tools, "_risk_check", lambda *a, **k: (True, [], []))
        monkeypatch.setattr(crypto_tools, "_record_crypto_trade", lambda *a, **k: None)
        crypto_tools.place_crypto_order("BTCUSDT.BN", "buy", quantity=0.01, price=49000)
        assert broker.last_client_order_id and broker.last_client_order_id.startswith("FINCHAT-")


class TestIdempotentRecovery:
    """下单请求失联后的定性：查得到→按真实回执；确认没有→FAILED；查不出来→UNKNOWN。

    ⛔ 最后一种绝不能乐观判 FAILED —— 那正是「同一笔成交两次」的来源。
    """

    @staticmethod
    def _broker(monkeypatch, probe):
        """构造一个 place_order 必抛、query_order_by_client_id 行为可控的 broker。"""
        from acquisition.markets import binance_trade as bt
        from trading_engine.brokers.binance_broker import BinanceBroker

        def _boom(*a, **k):
            raise TimeoutError("read timeout")

        monkeypatch.setattr(bt, "symbol_filters", lambda s: {})
        monkeypatch.setattr(bt, "place_order", _boom)
        monkeypatch.setattr(bt, "query_order_by_client_id", probe)
        monkeypatch.setattr(bt, "ticker_price", lambda s: 50000.0)
        broker = BinanceBroker()
        broker._connected = True          # 跳过 connect 的真实探活
        return broker

    def test_probe_finds_order_so_not_failed(self, monkeypatch):
        """币安其实受理了：按真实回执填充，绝不重下。"""
        from trading_engine.brokers.base import OrderStatus
        receipt = {"orderId": 777, "status": "FILLED", "executedQty": "0.01",
                   "cummulativeQuoteQty": "500"}
        broker = self._broker(monkeypatch, lambda s, c: receipt)
        o = broker.submit_order("BTCUSDT.BN", "BUY", 0.01, None, client_order_id="CPO-x-1")
        assert o.status == OrderStatus.FILLED
        assert o.filled_quantity == 0.01
        assert o.filled_price == 50000.0          # 500 / 0.01 反推
        assert o.order_id == "777"

    def test_probe_confirms_absent_so_failed(self, monkeypatch):
        """回查明确「不存在」→ 判 FAILED，上层可安全重排。"""
        from trading_engine.brokers.base import OrderStatus
        broker = self._broker(monkeypatch, lambda s, c: None)
        o = broker.submit_order("BTCUSDT.BN", "BUY", 0.01, None, client_order_id="CPO-x-2")
        assert o.status == OrderStatus.FAILED

    def test_probe_also_fails_so_unknown(self, monkeypatch):
        """回查本身也挂了 → UNKNOWN，交人工对账。"""
        from trading_engine.brokers.base import OrderStatus

        def _probe_boom(s, c):
            raise ConnectionError("still down")

        broker = self._broker(monkeypatch, _probe_boom)
        o = broker.submit_order("BTCUSDT.BN", "BUY", 0.01, None, client_order_id="CPO-x-3")
        assert o.status == OrderStatus.UNKNOWN
        assert "核对" in (o.error_msg or "")

    def test_no_idempotency_key_is_unknown_not_failed(self, monkeypatch):
        """没传幂等键就无从回查 → 只能 UNKNOWN，不许猜「失败了」。"""
        from trading_engine.brokers.base import OrderStatus
        broker = self._broker(monkeypatch, lambda s, c: None)
        o = broker.submit_order("BTCUSDT.BN", "BUY", 0.01, None)
        assert o.status == OrderStatus.UNKNOWN


class TestSafeClientOrderId:
    """币安 clientOrderId 约束 `^[\\.A-Z\\:/a-z0-9_-]{1,36}$`。"""

    def test_order_ref_passes_through(self):
        from acquisition.markets.binance_trade import safe_client_order_id
        ref = "CPO-20260722102453-abc123"
        assert safe_client_order_id(ref) == ref

    def test_illegal_chars_replaced(self):
        from acquisition.markets.binance_trade import safe_client_order_id
        assert safe_client_order_id("a b#c") == "a-b-c"

    def test_truncates_from_tail_keeping_entropy(self):
        """超长要截尾部保留：随机后缀才是区分度，截头会让同秒两张单撞键。"""
        from acquisition.markets.binance_trade import safe_client_order_id
        out = safe_client_order_id("X" * 40 + "-tail99")
        assert len(out) == 36 and out.endswith("-tail99")


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


def _stub_empty_wallets(monkeypatch, bt):
    """把 Funding/Earn 三个新钱包端点 stub 成空（避免单测打真网；import 时 .env 有真 key）。"""
    monkeypatch.setattr(bt, "funding_asset", lambda: [])
    monkeypatch.setattr(bt, "earn_flexible_positions", lambda *a, **k: [])
    monkeypatch.setattr(bt, "earn_locked_positions", lambda *a, **k: [])


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
        _stub_empty_wallets(monkeypatch, bt)
        broker = BinanceBroker()
        broker._connected = True
        acct = broker.get_account_info()
        assert acct["market_value"] == 0.0        # 无币 → 零持仓敞口（原来会虚增）
        assert acct["cash"] == 8000.0             # USDT+USDC 的 free
        assert acct["spot_cash"] == 8000.0
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
        _stub_empty_wallets(monkeypatch, bt)
        broker = BinanceBroker()
        broker._connected = True
        acct = broker.get_account_info()
        assert acct["market_value"] == 5000.0     # 0.1 BTC * 50000
        assert acct["cash"] == 1000.0
        assert acct["total_value"] == 6000.0

    def test_flexible_earn_btc_counted_as_holding(self, monkeypatch):
        """回归（Jason 拍板口径）：币安自动申购活期理财里的 BTC = **你的持仓**（可秒赎、App
        「持仓币种」带成本价展示），要算进 positions + market_value + 建议。

        现货余额里的 LDBTC（理财镜像复制项）由 `_is_earn_mirror` **显式跳过**（LD 前缀识别，
        不再靠 LDBTCUSDT 定价失败去重），真实数量由 earn API 的 asset=BTC 提供。持仓带
        wallet_breakdown 标明这仓在理财、available=0（现货不可直接卖）。
        """
        from acquisition.markets import binance_trade as bt
        from trading_engine.brokers.binance_broker import BinanceBroker

        monkeypatch.setattr(bt, "account", lambda: {"balances": [
            {"asset": "USDT", "free": "0", "locked": "14.9"},
            {"asset": "LDBTC", "free": "0.0003", "locked": "0"},   # 理财镜像，_is_earn_mirror 跳过
        ]})
        monkeypatch.setattr(bt, "funding_asset", lambda: [])
        monkeypatch.setattr(bt, "earn_flexible_positions", lambda *a, **k: [
            {"asset": "BTC", "totalAmount": "0.0003"},
            {"asset": "USDT", "totalAmount": "43.9"},
        ])
        monkeypatch.setattr(bt, "earn_locked_positions", lambda *a, **k: [])
        monkeypatch.setattr(bt, "ticker_price", lambda sym: 66000.0)

        broker = BinanceBroker()
        broker._connected = True

        # 活期理财 BTC 算持仓（LDBTC 镜像跳过，只一条 BTC），且明细归到 earn_flexible
        pos = broker.get_positions()
        assert len(pos) == 1 and pos[0].symbol == "BTCUSDT.BN"
        assert pos[0].quantity == 0.0003
        assert round(pos[0].market_value, 2) == 19.8
        assert pos[0].available == 0.0                          # 全在理财 → 现货可直接卖=0
        assert pos[0].wallet_breakdown == {"earn_flexible": 0.0003}

        acct = broker.get_account_info()
        assert round(acct["market_value"], 2) == 19.8         # 币持仓市值含理财 BTC
        # earn_coins 仍单列（供卖前赎回识别 + 展示"这仓在理财里"）
        assert acct["earn_coins"] == [
            {"asset": "BTC", "quantity": 0.0003, "value": 19.8, "redeemable": True}]
        wallet = next(w for w in acct["wallets"] if w["name"] == "理财活期")
        assert round(wallet["coins_value"], 2) == 19.8
        assert round(wallet["stable"], 2) == 43.9             # 稳定币仍算可赎买力


class TestWalletBreakdown:
    """钱包归属真源：LD 镜像显式去重（不靠定价失败）+ 现货/理财同持一币的明细拆分。"""

    def test_ld_mirror_skipped_even_when_priceable(self, monkeypatch):
        """回归：LD 镜像靠 `_is_earn_mirror` 前缀显式跳过。即便 LDBTCUSDT **能定价**，
        也绝不把镜像再算一份（旧实现靠定价失败去重，一旦可定价就双计）。"""
        from acquisition.markets import binance_trade as bt
        from trading_engine.brokers.binance_broker import BinanceBroker

        monkeypatch.setattr(bt, "account", lambda: {"balances": [
            {"asset": "BTC", "free": "0.001", "locked": "0"},       # 真现货 BTC
            {"asset": "LDBTC", "free": "0.0003", "locked": "0"},    # 理财镜像（这次给它定价）
        ]})
        monkeypatch.setattr(bt, "funding_asset", lambda: [])
        monkeypatch.setattr(bt, "earn_flexible_positions", lambda *a, **k: [
            {"asset": "BTC", "totalAmount": "0.0003"}])
        monkeypatch.setattr(bt, "earn_locked_positions", lambda *a, **k: [])
        # 关键：所有 symbol 都能定价（含 LDBTCUSDT）→ 旧去重机制会失效
        monkeypatch.setattr(bt, "ticker_price", lambda sym: 66000.0)

        broker = BinanceBroker()
        broker._connected = True
        pos = broker.get_positions()
        assert len(pos) == 1 and pos[0].symbol == "BTCUSDT.BN"
        # 现货 0.001 + 理财 0.0003 = 0.0013；LDBTC 0.0003 **不再**被算第三份
        assert round(pos[0].quantity, 8) == 0.0013
        assert round(pos[0].available, 8) == 0.001
        assert pos[0].wallet_breakdown == {"spot": 0.001, "earn_flexible": 0.0003}

    def test_spot_and_earn_split_in_breakdown(self, monkeypatch):
        """同一币现货+理财同持 → 一条持仓，available=现货分量，明细分列。"""
        from acquisition.markets import binance_trade as bt
        from trading_engine.brokers.binance_broker import BinanceBroker

        monkeypatch.setattr(bt, "account", lambda: {"balances": [
            {"asset": "ETH", "free": "0.5", "locked": "0"}]})
        monkeypatch.setattr(bt, "funding_asset", lambda: [{"asset": "ETH", "free": "0.2"}])
        monkeypatch.setattr(bt, "earn_flexible_positions", lambda *a, **k: [
            {"asset": "ETH", "totalAmount": "0.3"}])
        monkeypatch.setattr(bt, "earn_locked_positions", lambda *a, **k: [])
        monkeypatch.setattr(bt, "ticker_price", lambda sym: 2000.0)

        broker = BinanceBroker()
        broker._connected = True
        pos = broker.get_positions()
        assert len(pos) == 1
        assert round(pos[0].quantity, 8) == 1.0          # 0.5+0.2+0.3
        assert round(pos[0].available, 8) == 0.5         # 只有现货可直接卖
        assert pos[0].wallet_breakdown == {"spot": 0.5, "funding": 0.2, "earn_flexible": 0.3}

    def test_locked_excluded_from_available(self, monkeypatch):
        """⛔ 回归：挂单锁定量不是「能立刻卖」的。

        available 若含 locked，`ensure_spot_for_sell` 会误判现货够卖→跳过赎回→交易所拒单。
        """
        from acquisition.markets import binance_trade as bt
        from trading_engine.brokers.binance_broker import BinanceBroker

        # 0.5 BTC 挂在限价卖单里（free=0/locked=0.5）+ 0.5 BTC 在活期理财
        monkeypatch.setattr(bt, "account", lambda: {"balances": [
            {"asset": "BTC", "free": "0", "locked": "0.5"}]})
        monkeypatch.setattr(bt, "funding_asset", lambda: [])
        monkeypatch.setattr(bt, "earn_flexible_positions", lambda *a, **k: [
            {"asset": "BTC", "totalAmount": "0.5"}])
        monkeypatch.setattr(bt, "earn_locked_positions", lambda *a, **k: [])
        monkeypatch.setattr(bt, "ticker_price", lambda sym: 66000.0)

        broker = BinanceBroker()
        broker._connected = True
        pos = broker.get_positions()
        assert len(pos) == 1
        assert round(pos[0].quantity, 8) == 1.0          # 总敞口仍含锁定量
        assert round(pos[0].available, 8) == 0.0         # 但一个都卖不出去
        # spot_locked 是 spot 的子集，不重复计入 quantity
        assert pos[0].wallet_breakdown == {"spot": 0.5, "spot_locked": 0.5,
                                           "earn_flexible": 0.5}

    def test_real_ld_prefixed_coin_not_swallowed(self, monkeypatch):
        """⛔ 回归：LDO（Lido DAO）是真实现货标的，不能被当成 LD 理财镜像抹掉。

        裸 `startswith("LD")` 会让 LDO 不进持仓/不进市值/占比算 0 → 满仓还能再买、
        止损永不触发。真镜像 LDBTC 仍须跳过（它的去前缀名 BTC 在理财持仓里）。
        """
        from acquisition.markets import binance_trade as bt
        from trading_engine.brokers.binance_broker import BinanceBroker

        monkeypatch.setattr(bt, "account", lambda: {"balances": [
            {"asset": "LDO", "free": "500", "locked": "0"},      # 真币
            {"asset": "LDBTC", "free": "0.0003", "locked": "0"},  # 理财镜像
            {"asset": "BTC", "free": "0.001", "locked": "0"}]})
        monkeypatch.setattr(bt, "funding_asset", lambda: [])
        monkeypatch.setattr(bt, "earn_flexible_positions", lambda *a, **k: [
            {"asset": "BTC", "totalAmount": "0.0003"}])
        monkeypatch.setattr(bt, "earn_locked_positions", lambda *a, **k: [])
        monkeypatch.setattr(bt, "ticker_price", lambda sym: 66000.0)

        broker = BinanceBroker()
        broker._connected = True
        by_symbol = {p.symbol: p for p in broker.get_positions()}
        assert set(by_symbol) == {"LDOUSDT.BN", "BTCUSDT.BN"}   # LDBTC 被跳过
        assert round(by_symbol["LDOUSDT.BN"].quantity, 8) == 500.0
        assert round(by_symbol["BTCUSDT.BN"].quantity, 8) == 0.0013   # 现货+理财，无第三份

        acct = broker.get_account_info()
        assert acct["market_value"] > 0                          # LDO 计入账户市值

    def test_earn_read_failure_keeps_ld_coin(self, monkeypatch):
        """理财 API 挂了 → earn 集合为空 → LD 开头的一律当真币（宁可多显示，不可抹掉）。"""
        from acquisition.markets import binance_trade as bt
        from trading_engine.brokers.binance_broker import BinanceBroker

        def _boom(*a, **k):
            raise RuntimeError("earn api down")

        monkeypatch.setattr(bt, "account", lambda: {"balances": [
            {"asset": "LDO", "free": "500", "locked": "0"}]})
        monkeypatch.setattr(bt, "funding_asset", lambda: [])
        monkeypatch.setattr(bt, "earn_flexible_positions", _boom)
        monkeypatch.setattr(bt, "earn_locked_positions", lambda *a, **k: [])
        monkeypatch.setattr(bt, "ticker_price", lambda sym: 2.0)

        broker = BinanceBroker()
        broker._connected = True
        pos = broker.get_positions()
        assert [p.symbol for p in pos] == ["LDOUSDT.BN"]


class TestSellFromEarn:
    """卖出自动腾挪：现货不足按缺口赎理财 + 划资金钱包、到账放行；补不齐拒单。"""

    class _SellBroker:
        def __init__(self, avail, earn_qty, redeem_ok=True,
                     funding_qty=0.0, transfer_ok=True, locked=0.0):
            from trading_engine.brokers.base import BrokerPosition
            wal = {"spot": avail + locked, "earn_flexible": earn_qty}
            if locked:
                wal["spot_locked"] = locked
            if funding_qty:
                wal["funding"] = funding_qty
            qty = avail + locked + earn_qty + funding_qty
            self._pos = BrokerPosition(
                symbol="BTCUSDT.BN", quantity=qty, avg_cost=66000.0,
                current_price=66000.0, market_value=qty * 66000.0,
                unrealized_pnl=0.0, available=avail, wallet_breakdown=wal)
            self._redeem_ok = redeem_ok
            self._transfer_ok = transfer_ok
            self.redeemed = []
            self.transferred = []

        def get_position(self, symbol):
            return self._pos

        def earn_redeem_flexible(self, asset, amount):
            self.redeemed.append((asset, amount))
            return self._redeem_ok

        def transfer_funding_to_spot(self, asset, amount):
            self.transferred.append((asset, amount))
            return self._transfer_ok

    def test_enough_spot_no_redeem(self, monkeypatch):
        from crypto_intel_engine.execution import ensure_spot_for_sell
        broker = self._SellBroker(avail=0.01, earn_qty=0.0)
        assert ensure_spot_for_sell(broker, "BTCUSDT.BN", 0.005) == (True, None)
        assert broker.redeemed == []                     # 现货够卖，不赎回

    def test_redeem_shortfall_then_arrives(self, monkeypatch):
        import time

        from acquisition.markets import binance_trade as bt
        from crypto_intel_engine import execution
        monkeypatch.setattr(time, "sleep", lambda *_: None)
        # 赎回后现货 BTC 到账 0.01（≥卖量）
        monkeypatch.setattr(bt, "account",
                            lambda: {"balances": [{"asset": "BTC", "free": "0.01"}]})
        broker = self._SellBroker(avail=0.002, earn_qty=0.008)
        ok, reason = execution.ensure_spot_for_sell(broker, "BTCUSDT.BN", 0.01)
        assert ok is True and reason is None
        # 缺口 = 0.01-0.002 = 0.008，从理财赎回 BTC
        assert broker.redeemed == [("BTC", 0.008)]

    def test_redeem_fail_rejects(self, monkeypatch):
        from crypto_intel_engine.execution import ensure_spot_for_sell
        broker = self._SellBroker(avail=0.0, earn_qty=0.01, redeem_ok=False)
        ok, reason = ensure_spot_for_sell(broker, "BTCUSDT.BN", 0.01)
        assert ok is False and "赎回" in reason

    def test_funding_wallet_also_transferred(self, monkeypatch):
        """⛔ 回归：quantity 含资金钱包但只赎理财 → 缺口永远补不齐、SELL 被永久堵死。

        0.5 现货 / 0.2 资金 / 0.3 理财，卖 1.0：理财 0.3 全赎 + 资金划 0.2 才够。
        """
        import time

        from acquisition.markets import binance_trade as bt
        from crypto_intel_engine import execution
        monkeypatch.setattr(time, "sleep", lambda *_: None)
        monkeypatch.setattr(bt, "account",
                            lambda: {"balances": [{"asset": "BTC", "free": "1.0"}]})
        broker = self._SellBroker(avail=0.5, earn_qty=0.3, funding_qty=0.2)
        ok, reason = execution.ensure_spot_for_sell(broker, "BTCUSDT.BN", 1.0)
        assert ok is True and reason is None
        assert broker.redeemed == [("BTC", 0.3)]         # 理财先赎（能赎多少赎多少）
        assert broker.transferred == [("BTC", 0.2)]      # 余下缺口从资金钱包划转

    def test_locked_shortfall_reason_names_the_wallet(self, monkeypatch):
        """补不齐时拒单，理由要说清卡在挂单里（不部分卖、不替 Jason 撤单）。"""
        from crypto_intel_engine.execution import ensure_spot_for_sell
        broker = self._SellBroker(avail=0.0, earn_qty=0.0, locked=1.0)
        ok, reason = ensure_spot_for_sell(broker, "BTCUSDT.BN", 1.0)
        assert ok is False
        assert "挂单锁定" in reason and "撤单" in reason
        assert broker.redeemed == [] and broker.transferred == []


class TestAccountAggregation:
    """需求1：账户聚合四钱包 → 三档买力（现货可用 + 活期可赎 + 资金可划）。"""

    def test_funding_and_earn_counted_into_buypower(self, monkeypatch):
        from acquisition.markets import binance_trade as bt
        from trading_engine.brokers.binance_broker import BinanceBroker

        # 现货几乎空、钱在 Funding 与理财活期（正是 Jason 的真实处境）
        monkeypatch.setattr(bt, "account",
                            lambda: {"balances": [{"asset": "USDT", "free": "10", "locked": "0"}]})
        monkeypatch.setattr(bt, "funding_asset",
                            lambda: [{"asset": "USDT", "free": "53.76", "locked": "0"}])
        monkeypatch.setattr(bt, "earn_flexible_positions",
                            lambda *a, **k: [{"asset": "USDT", "totalAmount": "100"}])
        monkeypatch.setattr(bt, "earn_locked_positions",
                            lambda *a, **k: [{"asset": "USDT", "amount": "500"}])
        broker = BinanceBroker()
        broker._connected = True
        acct = broker.get_account_info()
        assert acct["spot_cash"] == 10.0
        assert acct["transferable_cash"] == 53.76      # 资金钱包可划
        assert acct["redeemable_cash"] == 100.0        # 理财活期可赎
        assert acct["cash"] == 163.76                  # 三档买力之和
        # 定期 500 不计入买力，但进总资产
        assert acct["total_value"] == 663.76
        names = {w["name"] for w in acct["wallets"]}
        assert names == {"现货", "资金", "理财活期", "理财定期"}


class TestFundingPlan:
    """需求1：买入现货 USDT 不足时，按「活期→资金」规划补足步骤。"""

    def test_shortfall_split_redeem_then_transfer(self):
        from agents.tools.crypto_tools import _plan_funding
        acct = {"spot_cash": 10.0, "redeemable_cash": 30.0, "transferable_cash": 100.0}
        steps, short = _plan_funding(acct, need_usdt=50.0)   # 缺 40：先赎 30 再划 10
        assert short == 0.0
        assert steps[0]["action"] == "redeem" and steps[0]["amount"] == 30.0
        assert steps[1]["action"] == "transfer" and steps[1]["amount"] == 10.0

    def test_enough_spot_no_steps(self):
        from agents.tools.crypto_tools import _plan_funding
        steps, short = _plan_funding({"spot_cash": 100.0}, need_usdt=50.0)
        assert steps == [] and short == 0.0

    def test_insufficient_everywhere_reports_short(self):
        from agents.tools.crypto_tools import _plan_funding
        acct = {"spot_cash": 5.0, "redeemable_cash": 10.0, "transferable_cash": 5.0}
        steps, short = _plan_funding(acct, need_usdt=50.0)   # 5+10+5=20，缺 30
        assert short == 30.0
        assert len(steps) == 2


class TestAutoSweepToEarn:
    """需求1.5：卖出后闲置 USDT 全自动申购活期（只扫闲置/尊重最小额/留痕不进台账）。"""

    class _SweepBroker:
        def __init__(self, spot_cash):
            self._spot = spot_cash
            self.subscribed = []

        def get_account_info(self):
            return {"spot_cash": self._spot}

        def earn_subscribe_flexible(self, asset, amount):
            self.subscribed.append((asset, amount))
            return True

    def test_idle_usdt_swept(self, monkeypatch):
        monkeypatch.setenv("CRYPTO_AUTO_EARN_ENABLED", "true")
        from agents.tools import crypto_tools
        from crypto_intel_engine import execution
        recorded = []
        monkeypatch.setattr(execution, "record_earn_sweep",
                            lambda a, amt: recorded.append((a, amt)))
        broker = self._SweepBroker(spot_cash=250.0)
        crypto_tools._auto_sweep_to_earn(broker)
        assert broker.subscribed == [("USDT", 250.0)]
        assert recorded == [("USDT", 250.0)]        # 留痕（非交易，不进 CryptoTrade 台账）

    def test_dust_not_swept(self, monkeypatch):
        monkeypatch.setenv("CRYPTO_AUTO_EARN_ENABLED", "true")
        monkeypatch.setenv("CRYPTO_EARN_DUST_MIN", "1")
        from agents.tools import crypto_tools
        broker = self._SweepBroker(spot_cash=0.3)   # 低于最小额
        crypto_tools._auto_sweep_to_earn(broker)
        assert broker.subscribed == []

    def test_disabled_skips(self, monkeypatch):
        monkeypatch.setenv("CRYPTO_AUTO_EARN_ENABLED", "false")
        from agents.tools import crypto_tools
        broker = self._SweepBroker(spot_cash=500.0)
        crypto_tools._auto_sweep_to_earn(broker)
        assert broker.subscribed == []


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


class TestPriceMapAvoidsDuplicateTickerCalls:
    """`get_account_info` 在风控链路上每单都跑，同一个币的价不许拉两遍。

    此前 `_value_coins`（三处调用）与 `_unrealized` 各自逐币 `ticker_price`，
    而该函数零缓存 —— 请求数直接翻倍。
    """

    def test_each_asset_priced_exactly_once(self):
        from trading_engine.brokers.binance_broker import BinanceBroker

        calls: list[str] = []

        class _BT:
            @staticmethod
            def ticker_price(pair):
                calls.append(pair)
                return 100.0

        prices = BinanceBroker._price_map(_BT(), ["BTC", "ETH", "BTC", "ETH", "SOL"])
        assert sorted(prices) == ["BTC", "ETH", "SOL"]
        assert sorted(calls) == ["BTCUSDT", "ETHUSDT", "SOLUSDT"], "去重后每币恰好一次"

    def test_unpriceable_asset_is_skipped_not_fatal(self):
        from trading_engine.brokers.binance_broker import BinanceBroker

        class _BT:
            @staticmethod
            def ticker_price(pair):
                if pair.startswith("WEIRD"):
                    raise ValueError("no such pair")
                return 7.0

        prices = BinanceBroker._price_map(_BT(), ["BTC", "WEIRD"])
        assert prices == {"BTC": 7.0}
        # 价取不到的币在估值时被跳过，而不是让整个账户查询炸掉
        assert BinanceBroker._value_coins({"BTC": 2.0, "WEIRD": 999.0}, prices) == 14.0


class TestNotSentIsFailedNotUnknown:
    """请求压根没发出去时判 UNKNOWN 是过度保守 —— 会逼人去币安查一笔不存在的单。"""

    def test_preflight_failure_is_failed(self, monkeypatch):
        from acquisition.markets import binance_trade as bt
        from trading_engine.brokers.base import OrderStatus
        from trading_engine.brokers.binance_broker import BinanceBroker

        probed: list = []
        monkeypatch.setattr(bt, "symbol_filters",
                            lambda s: (_ for _ in ()).throw(TimeoutError("交易规则拉取超时")))
        monkeypatch.setattr(bt, "query_order_by_client_id",
                            lambda s, c: probed.append(c))

        b = BinanceBroker()
        b._connected = True
        order = b.submit_order("BTCUSDT.BN", "BUY", 0.01, None, client_order_id="CPO-x")
        assert order.status == OrderStatus.FAILED, "没发出去就该能安全重下"
        assert "尚未发出" in (order.error_msg or "")
        assert probed == [], "都没下单，不该去回查"

    def test_failure_after_send_still_probes(self, monkeypatch):
        """反向：真发出去了还是要回查，别把幂等保护一起改没了。"""
        from acquisition.markets import binance_trade as bt
        from trading_engine.brokers.base import OrderStatus
        from trading_engine.brokers.binance_broker import BinanceBroker

        monkeypatch.setattr(bt, "symbol_filters", lambda s: {})
        monkeypatch.setattr(bt, "round_step", lambda q, st: q)
        monkeypatch.setattr(bt, "round_price", lambda p, t: p)
        monkeypatch.setattr(bt, "place_order",
                            lambda *a, **k: (_ for _ in ()).throw(TimeoutError("断连")))
        monkeypatch.setattr(bt, "query_order_by_client_id",
                            lambda s, c: (_ for _ in ()).throw(TimeoutError("回查也断")))

        b = BinanceBroker()
        b._connected = True
        order = b.submit_order("BTCUSDT.BN", "BUY", 0.01, None, client_order_id="CPO-x")
        assert order.status == OrderStatus.UNKNOWN, "发出去了且查不到 = 必须人工对账"

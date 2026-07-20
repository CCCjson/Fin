"""币安现货实盘 broker —— 继承 BaseBroker，走 acquisition 签名交易门面。

## 与 EasyTraderBroker 同范式

`connect()` 惰性检查凭证 + `_ensure_connected()` 每方法开头调 + `os.getenv` 读 key
（顶部 `load_dotenv` 指向 backend `.env`）。实际出网在 `acquisition.markets.binance_trade`。

## 加密特有

- **币量是小数**（0.0015 BTC）：基类 `quantity: int` 注解面向 A 股整手，这里用 float，
  运行时不强制。下单前按交易对 `LOT_SIZE` 的 stepSize 向下取整，并校验 `MIN_NOTIONAL`，
  否则币安拒单。
- **无成本价**：币安现货账户不返回持仓成本，`avg_cost` 只能置当前价（`unrealized_pnl=0`）——
  真实盈亏靠 Jason 系统里的成交记录/对账另算，不从交易所猜。
- **symbol 双形态**：对外用 `BTCUSDT.BN`，门面内部剥后缀调币安。

## 安全边界（不可移除）

本 broker 只负责「把订单发给币安」。**是否允许下单、逐笔人工确认、风控**在上层
（`agents/tools/crypto_tools.place_crypto_order` 的 confirm_gate + RiskManager）。
永不在此做自动下单。
"""
from datetime import datetime
from pathlib import Path

from loguru import logger

from .base import BaseBroker, BrokerOrder, BrokerPosition, OrderStatus

try:
    from dotenv import load_dotenv
    load_dotenv(Path(__file__).resolve().parent.parent.parent / ".env")
except Exception:  # noqa: BLE001 — dotenv 缺失不致命，仍可从进程 env 读
    pass

# 币安订单状态 → 项目 OrderStatus
_STATUS_MAP = {
    "NEW": OrderStatus.SUBMITTED, "PARTIALLY_FILLED": OrderStatus.PARTIAL_FILLED,
    "FILLED": OrderStatus.FILLED, "CANCELED": OrderStatus.CANCELLED,
    "REJECTED": OrderStatus.REJECTED, "EXPIRED": OrderStatus.REJECTED,
}


class BinanceBroker(BaseBroker):
    """币安现货实盘交易接口。"""

    def __init__(self):
        super().__init__(name="Binance-Spot")
        self._connected = False

    # ── 连接 ────────────────────────────────────────────────────────────
    def connect(self) -> bool:
        """检查凭证 + ping 账户验证连通。"""
        from acquisition.markets import binance_trade as bt
        if not bt.has_credentials():
            logger.error("BINANCE_API_KEY/SECRET 未配置，无法连接币安")
            self._connected = False
            return False
        try:
            bt.account()   # 探一次，签名/权限/网络任一不对都会抛
            self._connected = True
            logger.info("币安现货连接成功")
            return True
        except Exception as e:  # noqa: BLE001
            logger.error(f"币安连接失败: {e}")
            self._connected = False
            return False

    def _ensure_connected(self):
        if not self._connected:
            if not self.connect():
                raise ConnectionError("币安未连接（检查 .env 的 BINANCE_API_KEY/SECRET）")

    @property
    def is_connected(self) -> bool:
        return self._connected

    # ── 账户 / 持仓 ─────────────────────────────────────────────────────
    def get_account_info(self) -> dict:
        """账户资金：cash=稳定币可用买力，market_value=非稳定币持仓市值，total=全额权益。

        稳定币（USDT/USDC/BUSD/FDUSD）都是现金等价物，不是持仓：free 部分算 cash（可用
        买力），locked 部分（挂在买单里）算权益但不算 cash。**只有非稳定币才计入 market_value**，
        否则锁定 USDT + 非 USDT 稳定币会被误当成币持仓，虚增敞口、误触 80%/20% 仓位风控。
        """
        self._ensure_connected()
        from acquisition.markets import binance_trade as bt
        acct = bt.account()
        balances = acct.get("balances", [])
        cash = 0.0            # 稳定币 free（可用买力）
        stable_locked = 0.0   # 稳定币 locked（挂在买单里，算权益不算可用）
        market_value = 0.0    # 仅非稳定币持仓市值
        for b in balances:
            asset = b.get("asset", "")
            free = float(b.get("free", 0))
            locked = float(b.get("locked", 0))
            if free + locked <= 0:
                continue
            if asset in ("USDT", "USDC", "BUSD", "FDUSD"):
                cash += free
                stable_locked += locked
            else:
                try:
                    price = bt.ticker_price(f"{asset}USDT")
                    market_value += (free + locked) * price
                except Exception:  # noqa: BLE001 — 没有 USDT 对的小币跳过计价
                    continue
        return {
            "cash": round(cash, 2),
            "market_value": round(market_value, 2),
            "total_value": round(cash + stable_locked + market_value, 2),
            "unrealized_pnl": 0.0,   # 交易所不给成本价，盈亏靠系统成交记录另算
        }

    def get_positions(self) -> list[BrokerPosition]:
        """非稳定币的非零余额 → 持仓。avg_cost 置当前价（交易所无成本价）。"""
        self._ensure_connected()
        from acquisition.markets import binance_trade as bt
        acct = bt.account()
        out: list[BrokerPosition] = []
        for b in acct.get("balances", []):
            asset = b.get("asset", "")
            if asset in ("USDT", "USDC", "BUSD", "FDUSD"):
                continue
            free = float(b.get("free", 0))
            locked = float(b.get("locked", 0))
            qty = free + locked
            if qty <= 0:
                continue
            try:
                price = bt.ticker_price(f"{asset}USDT")
            except Exception:  # noqa: BLE001
                continue
            out.append(BrokerPosition(
                symbol=f"{asset}USDT.BN", quantity=qty, avg_cost=price,
                current_price=price, market_value=qty * price,
                unrealized_pnl=0.0, available=free,
            ))
        return out

    def get_position(self, symbol: str) -> BrokerPosition | None:
        for pos in self.get_positions():
            if pos.symbol == symbol:
                return pos
        return None

    def get_current_price(self, symbol: str) -> float:
        from acquisition.markets import binance_trade as bt
        try:
            return bt.ticker_price(symbol)
        except Exception as e:  # noqa: BLE001
            logger.warning(f"取币安现价失败 {symbol}: {e}")
            return 0.0

    # ── 下单 / 撤单 / 查单 ──────────────────────────────────────────────
    def submit_order(self, symbol: str, action: str, quantity: float,
                     price: float | None = None) -> BrokerOrder:
        """下单。**下单前按 LOT_SIZE 取整 + 校验 MIN_NOTIONAL**，否则币安拒单。"""
        self._ensure_connected()
        from acquisition.markets import binance_trade as bt
        order = BrokerOrder(order_id="", symbol=symbol, action=action.upper(),
                            quantity=quantity, price=price, status=OrderStatus.PENDING,
                            submit_time=datetime.now())
        try:
            filt = bt.symbol_filters(symbol)
            qty = bt.round_step(float(quantity), filt.get("step_size"))
            if filt.get("min_qty") and qty < filt["min_qty"]:
                order.status = OrderStatus.REJECTED
                order.error_msg = f"数量 {qty} 低于最小下单量 {filt['min_qty']}"
                return order
            ref_price = price or self.get_current_price(symbol)
            notional = qty * (ref_price or 0)
            if filt.get("min_notional") and notional < filt["min_notional"]:
                order.status = OrderStatus.REJECTED
                order.error_msg = f"金额 {notional:.2f} 低于最小名义额 {filt['min_notional']}"
                return order

            # 限价单 price 必须对齐 PRICE_FILTER 的 tickSize，否则币安 -1013 拒单
            send_price = bt.round_price(price, filt.get("tick_size")) if price else None
            order.price = send_price
            result = bt.place_order(symbol, action, qty, send_price)
            order.order_id = str(result.get("orderId", ""))
            order.status = _STATUS_MAP.get(result.get("status", ""), OrderStatus.SUBMITTED)
            executed_qty = float(result.get("executedQty", 0) or 0)
            order.filled_quantity = executed_qty
            fills = result.get("fills", [])
            if fills:
                total_q = sum(float(f["qty"]) for f in fills)
                total_v = sum(float(f["qty"]) * float(f["price"]) for f in fills)
                order.filled_price = total_v / total_q if total_q else 0.0
                order.commission = sum(float(f.get("commission", 0)) for f in fills)
            elif price:
                order.filled_price = price
            logger.info(f"币安下单: {action} {symbol} {qty} @ {price or 'MARKET'} "
                        f"→ order {order.order_id} status={order.status.value}")
        except Exception as e:  # noqa: BLE001
            order.status = OrderStatus.FAILED
            order.error_msg = str(e)
            logger.error(f"币安下单异常: {e}")
        return order

    def _split_id(self, order_id: str) -> tuple[str | None, str]:
        """币安撤单/查单需 symbol+orderId；基类签名只有 order_id，故支持 'SYMBOL.BN:orderId' 复合。"""
        if ":" in order_id:
            sym, oid = order_id.split(":", 1)
            return sym, oid
        return None, order_id

    def cancel_order(self, order_id: str) -> bool:
        self._ensure_connected()
        from acquisition.markets import binance_trade as bt
        sym, oid = self._split_id(order_id)
        if not sym:
            logger.error("币安撤单需要 symbol，请用 'SYMBOL.BN:orderId' 复合 id")
            return False
        try:
            bt.cancel_order(sym, oid)
            return True
        except Exception as e:  # noqa: BLE001
            logger.error(f"币安撤单失败: {e}")
            return False

    def get_order(self, order_id: str) -> BrokerOrder | None:
        self._ensure_connected()
        from acquisition.markets import binance_trade as bt
        sym, oid = self._split_id(order_id)
        if not sym:
            return None
        try:
            r = bt.query_order(sym, oid)
            return BrokerOrder(
                order_id=str(r.get("orderId", "")), symbol=f"{r.get('symbol', '')}.BN",
                action=r.get("side", ""), quantity=float(r.get("origQty", 0)),
                price=float(r.get("price", 0)) or None,
                filled_quantity=float(r.get("executedQty", 0)),
                filled_price=float(r.get("price", 0)),
                status=_STATUS_MAP.get(r.get("status", ""), OrderStatus.SUBMITTED),
            )
        except Exception as e:  # noqa: BLE001
            logger.error(f"币安查单失败: {e}")
            return None

    def get_orders(self, symbol: str | None = None) -> list[BrokerOrder]:
        self._ensure_connected()
        from acquisition.markets import binance_trade as bt
        try:
            rows = bt.open_orders(symbol)
            return [BrokerOrder(
                order_id=str(r.get("orderId", "")), symbol=f"{r.get('symbol', '')}.BN",
                action=r.get("side", ""), quantity=float(r.get("origQty", 0)),
                price=float(r.get("price", 0)) or None,
                filled_quantity=float(r.get("executedQty", 0)),
                filled_price=float(r.get("price", 0)),
                status=_STATUS_MAP.get(r.get("status", ""), OrderStatus.SUBMITTED),
            ) for r in rows]
        except Exception as e:  # noqa: BLE001
            logger.error(f"币安查委托失败: {e}")
            return []


# 模块级单例（与 get_paper_broker 一致的复用方式）
_binance_broker: BinanceBroker | None = None


def get_binance_broker() -> BinanceBroker:
    global _binance_broker
    if _binance_broker is None:
        _binance_broker = BinanceBroker()
    return _binance_broker

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

# 稳定币视为现金等价物（不算币持仓敞口）
_STABLES = ("USDT", "USDC", "BUSD", "FDUSD")


def _is_earn_mirror(asset: str, earn_assets: set[str]) -> bool:
    """现货 `account()` 里 `LD` 前缀项 = 活期理财在现货账户的**镜像复制项**（Binance
    约定，LD=Lending Daily）。真实理财数量只认 `earn_flexible_positions()`，故现货侧一律
    跳过这些镜像，避免与理财 API 双重计数。

    此前靠「查 `LDBTCUSDT` 无交易对→定价失败→跳过」的巧合去重，脆弱（LD 项一旦可定价即双计、
    ticker 抖动即误伤）。

    ⛔ **裸前缀判断也不行**：`LDO`（Lido DAO）是真实币安现货标的（有 LDOUSDT 交易对），
    裸判 `startswith("LD")` 会把它整条抹掉——不进持仓、不进市值、占比算 0，于是满仓还能
    再买、止损永不触发。故必须二次校验：**去掉 LD 前缀后的名字确实在活期理财持仓集合里**
    （`LDBTC` → `BTC` ∈ earn_assets = 镜像；`LDO` → `O` ∉ earn_assets = 真币）。

    Args:
        earn_assets: `earn_flexible_positions()` 里的真实 asset 名集合。取数失败时传空集
            → 一律判为真实持仓（失败方向安全：宁可多显示一条，不可凭空抹掉持仓）。
    """
    return asset.startswith("LD") and asset[2:] in earn_assets

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
    @staticmethod
    def _value_coins(bt, holdings: dict[str, float]) -> float:
        """把 {非稳定币: 数量} 按现价折成 USDT 市值。无 USDT 对的小币跳过。"""
        mv = 0.0
        for asset, qty in holdings.items():
            if qty <= 0:
                continue
            try:
                mv += qty * bt.ticker_price(f"{asset}USDT")
            except Exception:  # noqa: BLE001 — 没有 USDT 对的小币跳过计价
                continue
        return mv

    def get_account_info(self) -> dict:
        """聚合**四个钱包**（现货/资金/理财活期/理财定期）→ 资金全貌 + 三档买力。

        「账户读不到钱」的根因是旧实现只查现货 `account()`。Jason 的 USDT 常在资金钱包
        或理财里，必须单独查。买力分三档（都是稳定币，现金等价物）：
          - `spot_cash`：现货可用（现在就能下单）。
          - `redeemable_cash`：理财**活期**（可秒赎回现货再下单）。
          - `transferable_cash`：资金钱包 free（可划到现货再下单）。
          - `cash` = 三者之和 = **真实可动用买力**（下单时自动赎/划补足，见 crypto_tools）。
        理财**定期**稳定币锁仓期内不可动，只进 `total_value` 展示，**不计入买力**。
        `market_value` 只含非稳定币持仓（现货+资金），避免锁定 USDT 虚增敞口误触仓位风控。
        """
        self._ensure_connected()
        from acquisition.markets import binance_trade as bt

        spot_cash = spot_stable_locked = 0.0
        spot_coins: dict[str, float] = {}
        funding_stable = 0.0
        funding_coins: dict[str, float] = {}
        earn_flex_stable = earn_locked_stable = 0.0

        # ① 理财活期（稳定币=可赎回买力；**非稳定币如 BTC=活期里的币持仓**，此前漏读致
        #    「明明有 BTC 却报没持仓」。asset 字段是干净的真名 BTC/USDT，非现货里的 LD 前缀）
        #    **必须先读**：现货侧要拿它的 asset 集合去二次校验 LD 镜像（见 `_is_earn_mirror`）
        earn_flex_coins: dict[str, float] = {}
        earn_assets: set[str] = set()
        try:
            for r in bt.earn_flexible_positions():
                asset = r.get("asset", "")
                if asset:
                    earn_assets.add(asset)
                amt = float(r.get("totalAmount", 0) or 0)
                if amt <= 0:
                    continue
                if asset in _STABLES:
                    earn_flex_stable += amt
                else:
                    earn_flex_coins[asset] = earn_flex_coins.get(asset, 0.0) + amt
        except Exception as e:  # noqa: BLE001
            logger.warning(f"币安理财活期读取失败: {e}")

        # ② 现货（LD 镜像项显式跳过——真实理财数量由 ① earn API 唯一提供，
        #    否则 LDUSDT 会被静默吞、LDBTC 靠定价失败去重，都脆）
        try:
            for b in bt.account().get("balances", []):
                asset = b.get("asset", "")
                if _is_earn_mirror(asset, earn_assets):
                    continue
                free, locked = float(b.get("free", 0)), float(b.get("locked", 0))
                if free + locked <= 0:
                    continue
                if asset in _STABLES:
                    spot_cash += free
                    spot_stable_locked += locked
                else:
                    spot_coins[asset] = spot_coins.get(asset, 0.0) + free + locked
        except Exception as e:  # noqa: BLE001
            logger.warning(f"币安现货余额读取失败: {e}")

        # ③ 资金钱包（Funding）
        try:
            for b in bt.funding_asset():
                asset = b.get("asset", "")
                free = float(b.get("free", 0))
                if free <= 0:
                    continue
                if asset in _STABLES:
                    funding_stable += free
                else:
                    funding_coins[asset] = funding_coins.get(asset, 0.0) + free
        except Exception as e:  # noqa: BLE001 — 权限/网络问题按空处理，不炸账户读取
            logger.warning(f"币安资金钱包读取失败: {e}")

        # ④ 理财定期（仅展示，不可动用）
        try:
            for r in bt.earn_locked_positions():
                if r.get("asset") in _STABLES:
                    earn_locked_stable += float(r.get("amount", 0) or r.get("totalAmount", 0) or 0)
        except Exception as e:  # noqa: BLE001
            logger.warning(f"币安理财定期读取失败: {e}")

        spot_coins_value = self._value_coins(bt, spot_coins)
        funding_coins_value = self._value_coins(bt, funding_coins)
        # 理财里的币逐个估值 + 明细（单列，不混进可交易持仓）
        earn_coins: list[dict] = []
        earn_flex_coins_value = 0.0
        for a, q in earn_flex_coins.items():
            v = self._value_coins(bt, {a: q})
            earn_coins.append({"asset": a, "quantity": q, "value": round(v, 2), "redeemable": True})
            earn_flex_coins_value += v
        # market_value = 币持仓市值 = 现货 + 资金 + 活期理财里的币（币安把自动申购活期币
        # 也算你的持仓）。earn_coins 另存明细，供「卖前需从理财赎回」的执行侧识别。
        market_value = spot_coins_value + funding_coins_value + earn_flex_coins_value
        cash = spot_cash + earn_flex_stable + funding_stable
        total_value = (cash + spot_stable_locked + earn_locked_stable + market_value)

        wallets = [
            {"name": "现货", "stable": round(spot_cash + spot_stable_locked, 2),
             "coins_value": round(spot_coins_value, 2)},
            {"name": "资金", "stable": round(funding_stable, 2),
             "coins_value": round(funding_coins_value, 2)},
            {"name": "理财活期", "stable": round(earn_flex_stable, 2),
             "coins_value": round(earn_flex_coins_value, 2)},
            {"name": "理财定期", "stable": round(earn_locked_stable, 2), "coins_value": 0.0},
        ]
        return {
            "cash": round(cash, 2),
            "spot_cash": round(spot_cash, 2),
            "redeemable_cash": round(earn_flex_stable, 2),
            "transferable_cash": round(funding_stable, 2),
            "market_value": round(market_value, 2),
            "total_value": round(total_value, 2),
            "unrealized_pnl": 0.0,   # 交易所不给成本价，盈亏靠系统成交记录另算
            "wallets": wallets,
            "earn_coins": earn_coins,   # 理财里的币（如活期 BTC）——单列，不是可交易持仓，可赎回现货
        }

    def get_positions(self) -> list[BrokerPosition]:
        """持仓 = **现货 + 活期理财 + 资金**三钱包的非稳定币，每个 asset 合成一条持仓并**保留
        分钱包明细** `wallet_breakdown`。

        币安「自动申购」活期理财 canRedeem=true、可秒赎、在 App「持仓币种」里带成本价展示
        —— 就是你的币持仓，只是边持有边吃息。故按真实 asset 合并三钱包余额：
          - `quantity` = 三钱包合计 = **总敞口**（喂风控/占比正确）；
          - `available` = **现货 free** = 能立刻卖的部分（理财/资金里的需先赎回/划转）；
          - `wallet_breakdown` = 各钱包非零分量（卖出时据此算「需从理财赎回/从资金划转多少」）。

        ⚠️ `spot` 分量 = free + locked（总敞口口径），但 `available` **只取 free**。
        挂单锁定的币不是「能立刻卖」的：把 locked 算进 available 会让 `ensure_spot_for_sell`
        误判「现货本就够卖」→ 跳过赎回 → 交易所以余额不足拒掉已确认的卖单。breakdown 里
        另列 `spot_locked`，让上层能说清「差的那部分卡在挂单里」（本 broker 绝不替 Jason 撤单）。

        现货里的 LD 镜像项由 `_is_earn_mirror` 跳过（需先读理财持仓集合做二次校验，
        否则 LDO 这种真币会被误杀）。avg_cost 置当前价（交易所无成本价）。
        """
        self._ensure_connected()
        from acquisition.markets import binance_trade as bt

        # asset → {"spot": q, "spot_locked": q, "earn_flexible": q, "funding": q}（只累加非零）
        breakdown: dict[str, dict[str, float]] = {}
        spot_free: dict[str, float] = {}     # 能立刻卖的量（available 的唯一真源）

        def _add(asset: str, wallet: str, qty: float) -> None:
            if qty <= 0:
                return
            wal = breakdown.setdefault(asset, {})
            wal[wallet] = wal.get(wallet, 0.0) + qty

        # ① 活期理财（**必须先读**：现货侧要用它的 asset 集合校验 LD 镜像）
        earn_assets: set[str] = set()
        try:
            for r in bt.earn_flexible_positions():
                asset = r.get("asset", "")
                if asset:
                    earn_assets.add(asset)
                if asset in _STABLES:
                    continue
                _add(asset, "earn_flexible", float(r.get("totalAmount", 0) or 0))
        except Exception as e:  # noqa: BLE001
            logger.warning(f"币安活期理财持仓读取失败: {e}")
        # ② 现货（跳过 LD 镜像与稳定币）—— free 与 locked 分开记
        for b in bt.account().get("balances", []):
            asset = b.get("asset", "")
            if asset in _STABLES or _is_earn_mirror(asset, earn_assets):
                continue
            free, locked = float(b.get("free", 0)), float(b.get("locked", 0))
            _add(asset, "spot", free + locked)
            _add(asset, "spot_locked", locked)
            if free > 0:
                spot_free[asset] = spot_free.get(asset, 0.0) + free
        # ③ 资金钱包（此前漏读——资金里的币曾进 market_value 却从不出现在持仓列表）
        try:
            for b in bt.funding_asset():
                asset = b.get("asset", "")
                if asset in _STABLES:
                    continue
                _add(asset, "funding", float(b.get("free", 0)))
        except Exception as e:  # noqa: BLE001
            logger.warning(f"币安资金钱包持仓读取失败: {e}")

        out: list[BrokerPosition] = []
        for asset, wal in breakdown.items():
            try:
                price = bt.ticker_price(f"{asset}USDT")
            except Exception:  # noqa: BLE001 — 无 USDT 对的小币跳过（无法计价）
                continue
            # spot_locked 是 spot 的**子集**，不能再进合计（否则锁定量被重复计敞口）
            qty = sum(q for w, q in wal.items() if w != "spot_locked")
            out.append(BrokerPosition(
                symbol=f"{asset}USDT.BN", quantity=qty, avg_cost=price,
                current_price=price, market_value=qty * price,
                unrealized_pnl=0.0, available=spot_free.get(asset, 0.0),
                wallet_breakdown=dict(wal),
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

    # ── 资金腾挪：划转 / 理财申赎（买入自动补足、卖出自动扫归用）──────────
    def transfer_funding_to_spot(self, asset: str, amount: float) -> bool:
        """资金钱包 → 现货划转。异常吞成 False（动钱失败不炸主流程，由上层判断）。"""
        if amount <= 0:
            return True
        from acquisition.markets import binance_trade as bt
        try:
            bt.universal_transfer("FUNDING_MAIN", asset, amount)
            logger.info(f"币安划转 资金→现货 {amount} {asset}")
            return True
        except Exception as e:  # noqa: BLE001
            logger.error(f"币安资金→现货划转失败 {amount} {asset}: {e}")
            return False

    def best_flexible_product(self, asset: str) -> dict | None:
        """选「最优活期」理财产品：可申购的里 APR 最高的那个。查不到返回 None。"""
        from acquisition.markets import binance_trade as bt
        try:
            rows = [r for r in bt.earn_flexible_list(asset) if r.get("canPurchase")]
        except Exception as e:  # noqa: BLE001
            logger.warning(f"币安活期产品列表读取失败 {asset}: {e}")
            return None
        if not rows:
            return None
        return max(rows, key=lambda r: float(r.get("latestAnnualPercentageRate", 0) or 0))

    def earn_subscribe_flexible(self, asset: str, amount: float) -> bool:
        """把 amount 的 asset 申购进最优活期理财。异常/无产品吞成 False。"""
        if amount <= 0:
            return True
        prod = self.best_flexible_product(asset)
        if not prod:
            logger.warning(f"无可申购活期产品 {asset}，跳过扫归")
            return False
        from acquisition.markets import binance_trade as bt
        try:
            bt.earn_flexible_subscribe(str(prod.get("productId")), amount)
            apr = prod.get("latestAnnualPercentageRate")
            logger.info(f"币安申购活期理财 {amount} {asset} @ APR{apr} ({prod.get('productId')})")
            return True
        except Exception as e:  # noqa: BLE001
            logger.error(f"币安申购活期理财失败 {amount} {asset}: {e}")
            return False

    def earn_redeem_flexible(self, asset: str, amount: float) -> bool:
        """从活期理财赎回 amount 的 asset 到现货。找该资产的持仓 productId 赎回。异常吞成 False。"""
        if amount <= 0:
            return True
        from acquisition.markets import binance_trade as bt
        try:
            rows = [r for r in bt.earn_flexible_positions(asset) if r.get("asset") == asset]
            if not rows:
                logger.warning(f"无活期理财持仓 {asset}，无法赎回")
                return False
            row = rows[0]
            held = float(row.get("totalAmount", 0) or 0)
            redeem_all = amount >= held
            bt.earn_flexible_redeem(str(row.get("productId")),
                                    None if redeem_all else amount, redeem_all=redeem_all)
            logger.info(f"币安赎回活期理财 {'全部' if redeem_all else amount} {asset}")
            return True
        except Exception as e:  # noqa: BLE001
            logger.error(f"币安赎回活期理财失败 {amount} {asset}: {e}")
            return False

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

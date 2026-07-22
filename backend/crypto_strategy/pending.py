"""crypto 半自动待确认单服务 —— 引擎排单 / Jason 确认(此刻重新决策) / 拒绝 / 自动清理。

## 关键：确认 = 「此刻重新决策」，不是复述旧快照（crypto 秒级行情，隔久了价格早变）

`create_pending` 存的是**意图**（BUY 冻结目标 USDT 金额、SELL 冻结平仓币量 + 决策价仅供算漂移）。
`confirm` 时**全部重来一遍**：重拉现价 → 重跑策略触发条件（理由不成立就拦）→ 算漂移（超阈值拦）
→ 按现价重算币量 → 重跑成本闸 + 风控 → 按现价市价成交。这样隔多久确认都安全。

## 清理：终态行不堆积（Jason 拍板）
拒绝立即删；过期 PENDING 立即删；成交/失败留短期(retain_hours)后删。审计在 run 日志 +
CryptoTrade + DecisionLog，本表只当「活的工作队列」，始终干净。⛔ 逐笔人工确认红线保留。
"""
import json
from datetime import datetime, timedelta
from typing import Any

from loguru import logger

_DEFAULT_EXPIRE_MIN = 60
_DEFAULT_DRIFT = 0.02       # 无 spec 兜底漂移阈值
_RETAIN_HOURS = 24          # 成交/失败单保留多久供前端展示后删
# EXECUTING 超过这么久还没收尾 = 后端在成交途中挂了（restart.sh 就够），转 STALE 催人工对账
_EXECUTING_STUCK_MIN = 15

# 「未了结」状态集合：这些状态下交易所侧**可能已有仓位**，引擎不许再排同向单（见 has_open）。
# STALE 只能由 Jason 对账后手动了结，系统绝不自己判它成没成交。
_OPEN_STATUSES = ("PENDING", "EXECUTING", "STALE")
# 终态：可以过保留期后清理掉
_TERMINAL_STATUSES = ("FILLED", "FAILED", "UNFILLED")


class PendingError(Exception):
    """业务拒绝（单不存在/状态不对/条件已变/漂移超阈值/风控未过）——路由转 400。"""


def _gen_ref() -> str:
    import secrets
    return f"CPO-{datetime.now().strftime('%Y%m%d%H%M%S')}-{secrets.token_hex(3)}"


def summary(row) -> dict[str, Any]:
    return {
        "order_ref": row.order_ref, "strategy_id": row.strategy_id,
        "symbol": row.symbol, "side": row.side, "quantity": row.quantity,
        "quote_amount": row.quote_amount, "price": row.price,
        "est_notional": row.est_notional, "status": row.status, "net_edge": row.net_edge,
        "reason": json.loads(row.reason) if row.reason else None,
        "created_at": str(row.created_at) if row.created_at else None,
        "expires_at": str(row.expires_at) if row.expires_at else None,
        "executed_order_id": row.executed_order_id,
        "fill_price": row.fill_price, "fill_quantity": row.fill_quantity,
        "error_message": row.error_message,
    }


class CryptoPendingOrderService:
    def _session(self):
        from data_engine.storage.database import get_session
        return get_session()

    # ──────────────── 引擎侧：排单 + 去重 ────────────────

    def has_open(self, strategy_id: str, symbol: str, side: str) -> bool:
        """该策略在该币上是否已有**未了结**的同向单（引擎据此去重，不重复排单）。

        ⛔ 「未了结」必须包含 `EXECUTING` 和 `STALE`，不能只看 `PENDING`：
          - `EXECUTING` = 正在成交中，或后端在成交途中挂掉留下的搁浅行；
          - `STALE` = 下单后失联、成交与否未知，等 Jason 去币安对账。
        这两种状态下交易所侧**可能已经有仓位**，此时再排一张同向单 → Jason 确认 → 重复买入。
        宁可漏排一次（下 tick 条件仍成立会补），也不能重复下单。
        """
        from data_engine.storage.models import CryptoPendingOrder
        session = self._session()
        try:
            return session.query(CryptoPendingOrder).filter(
                CryptoPendingOrder.strategy_id == strategy_id,
                CryptoPendingOrder.symbol == symbol,
                CryptoPendingOrder.side == side,
                CryptoPendingOrder.status.in_(_OPEN_STATUSES)).first() is not None
        finally:
            session.close()

    def create_pending(self, strategy_id: str, symbol: str, side: str, quantity: float,
                       price: float | None, *, quote_amount: float | None = None,
                       net_edge: float | None = None, reason: dict | None = None,
                       expire_minutes: int = _DEFAULT_EXPIRE_MIN) -> dict:
        """排一张待确认单。BUY 应给 quote_amount（目标 USDT，确认时按现价重算币量）。"""
        from data_engine.storage.models import CryptoPendingOrder
        session = self._session()
        try:
            row = CryptoPendingOrder(
                order_ref=_gen_ref(), strategy_id=strategy_id, symbol=symbol, side=side,
                quantity=quantity, quote_amount=quote_amount, price=price,
                est_notional=round(quote_amount, 2) if quote_amount
                else (round((price or 0) * quantity, 2) if price else None),
                status="PENDING", net_edge=net_edge,
                reason=json.dumps(reason, ensure_ascii=False, default=str) if reason else None,
                expires_at=datetime.now() + timedelta(minutes=expire_minutes))
            session.add(row)
            session.commit()
            data = summary(row)
        finally:
            session.close()
        self._notify(data)
        return data

    def _notify(self, data: dict) -> None:
        try:
            from business_events import publish_event
            publish_event("crypto_order_pending", source="crypto_strategy",
                          symbol=data["symbol"],
                          title=f"待确认：{data['side']} {data['symbol']}"
                                f"（策略 {data['strategy_id']}，点确认时按现价重新核算）",
                          order_ref=data["order_ref"], side=data["side"],
                          quote_amount=data["quote_amount"], price=data["price"])
        except Exception:  # noqa: BLE001
            pass

    # ──────────────── 读 ────────────────

    def list_pending(self, *, status: str | None = "PENDING", limit: int = 100) -> list[dict]:
        """列单。`status` 支持逗号分隔多状态（如 `"PENDING,STALE"`）；传 None 列全部。

        前端默认拉 `PENDING,STALE`：**STALE（成交与否未知）必须让 Jason 看得见**，
        否则它只在后台挡着重排、人却不知道有一笔单要去币安对账。
        """
        from data_engine.storage.models import CryptoPendingOrder
        session = self._session()
        try:
            q = session.query(CryptoPendingOrder)
            if status:
                wanted = [s.strip() for s in status.split(",") if s.strip()]
                q = q.filter(CryptoPendingOrder.status.in_(wanted))
            rows = q.order_by(CryptoPendingOrder.created_at.desc()).limit(limit).all()
            return [summary(r) for r in rows]
        finally:
            session.close()

    def get(self, order_ref: str) -> dict:
        from data_engine.storage.models import CryptoPendingOrder
        session = self._session()
        try:
            row = session.query(CryptoPendingOrder).filter(
                CryptoPendingOrder.order_ref == order_ref).first()
            if not row:
                raise PendingError(f"待确认单不存在：{order_ref}")
            return summary(row)
        finally:
            session.close()

    # ──────────────── 清理（引擎每 tick 先跑）：终态不堆积 ────────────────

    def cleanup(self, retain_hours: int = _RETAIN_HOURS) -> dict:
        """过期 PENDING 立即删；搁浅 EXECUTING 转 STALE；成交/失败留 retain_hours 后删。

        ⛔ **搁浅的 EXECUTING 绝不能删、也绝不能当失败**：后端在「已置 EXECUTING、还没收尾」
        之间挂掉（`restart.sh --backend` 就够）时，币安那边到底成没成交无人知道。删了它
        `has_open` 就不再挡，引擎会重排 → 重复下单；判失败同理。只能转 STALE 挂着催人工对账。
        """
        from data_engine.storage.models import CryptoPendingOrder
        session = self._session()
        stranded: list = []
        try:
            now = datetime.now()
            expired = session.query(CryptoPendingOrder).filter(
                CryptoPendingOrder.status == "PENDING",
                CryptoPendingOrder.expires_at.isnot(None),
                CryptoPendingOrder.expires_at < now).delete(synchronize_session=False)

            # 搁浅的 EXECUTING → STALE（confirmed_at 为空的老行按 created_at 兜底判龄）
            stuck_before = now - timedelta(minutes=_EXECUTING_STUCK_MIN)
            stuck_rows = session.query(CryptoPendingOrder).filter(
                CryptoPendingOrder.status == "EXECUTING").all()
            for r in stuck_rows:
                marker = r.confirmed_at or r.created_at
                if marker and marker < stuck_before:
                    r.status = "STALE"
                    r.error_message = (f"确认后 {_EXECUTING_STUCK_MIN} 分钟未收尾"
                                       f"（后端可能中途重启），成交与否未知，请去币安核对")
                    stranded.append({"order_ref": r.order_ref, "symbol": r.symbol,
                                     "side": r.side})

            cutoff = now - timedelta(hours=retain_hours)
            terminal = session.query(CryptoPendingOrder).filter(
                CryptoPendingOrder.status.in_(_TERMINAL_STATUSES),
                CryptoPendingOrder.created_at < cutoff).delete(synchronize_session=False)
            session.commit()
        finally:
            session.close()
        for s in stranded:      # session 外发告警，写库失败不影响清理结果
            self._alert_needs_reconcile(s["order_ref"], s, "确认后未收尾（后端可能中途重启）")
        return {"expired_deleted": expired, "terminal_deleted": terminal,
                "stranded_marked": len(stranded)}

    # 向后兼容旧名（引擎旧调用点）
    def expire_stale(self) -> int:
        return self.cleanup().get("expired_deleted", 0)

    # ──────────────── Jason 侧：拒绝（立即删）/ 确认（此刻重新决策）────────────────

    def reject(self, order_ref: str) -> dict:
        from data_engine.storage.models import CryptoPendingOrder
        session = self._session()
        try:
            row = session.query(CryptoPendingOrder).filter(
                CryptoPendingOrder.order_ref == order_ref).first()
            if not row:
                raise PendingError(f"待确认单不存在：{order_ref}")
            # STALE 也允许在此了结 —— 那是「Jason 已去币安对完账，把这张单清掉」的唯一出口。
            # 不开这个口子，待对账单会永远挂着并一直挡住引擎重排。
            if row.status not in ("PENDING", "STALE"):
                raise PendingError(f"单状态为 {row.status}，不可拒绝")
            was = row.status
            session.delete(row)          # 拒绝立即删，不堆积
            session.commit()
            return {"order_ref": order_ref, "status": "REJECTED", "deleted": True,
                    "was": was}
        finally:
            session.close()

    def confirm(self, order_ref: str) -> dict:
        """确认成交 = **此刻重新决策**：重拉现价→重验触发条件→算漂移→现价重算币量→重跑风控→市价成交。"""
        # 1) 原子抢占 PENDING → EXECUTING（真正防并发双确认）
        intent = self._claim(order_ref)

        # 2) 此刻重新决策 + 成交（慢操作，session 外）
        try:
            result = self._revalidate_and_execute(intent, order_ref)
        except Exception as e:  # noqa: BLE001
            self._finalize(order_ref, "FAILED", error=str(e))
            raise PendingError(f"成交异常：{e}") from e

        if result.get("stale"):
            # 行情已变/条件不再成立 → 删掉这张陈单（引擎下 tick 若仍成立会重排新单）
            self._delete(order_ref)
            raise PendingError(result["reason"])
        if result.get("unknown"):
            # ⚠️ 下单请求发出后失联且回查未果 —— 既不能当成交也不能当失败（当失败会被重排 →
            # 同一笔成交两次）。落 STALE 待人工对账，`has_open` 会继续挡住重排。
            self._finalize(order_ref, "STALE", order_id=result.get("order_id"),
                           error=result.get("reason"))
            self._alert_needs_reconcile(order_ref, intent, result.get("reason") or "")
            raise PendingError(result.get("reason") or "订单状态未知，请去币安核对")
        if not result.get("ok"):
            self._finalize(order_ref, "FAILED", error=result.get("reason"))
            raise PendingError(result.get("reason") or "成交失败")
        # 零成交 ≠ 成交：单列 UNFILLED，绝不复用 FILLED（否则前端弹「已确认成交」但一分钱没动）
        status = "FILLED" if (result.get("fill_qty") or 0) > 0 else "UNFILLED"
        self._finalize(order_ref, status, order_id=result.get("order_id"),
                       fill_price=result.get("fill_price"), fill_qty=result.get("fill_qty"),
                       drift=result.get("drift"), error=result.get("note"))
        return self.get(order_ref)

    def _claim(self, order_ref: str) -> dict:
        """把单从 PENDING 原子抢占为 EXECUTING，返回下单意图。抢不到就抛。

        ⛔ **必须是条件更新**，不能「先 query 判状态、再赋值 commit」——那中间有窗口，
        两个并发确认请求会双双读到 PENDING、双双置 EXECUTING、双双下真单。
        `UPDATE ... WHERE status='PENDING'` 由数据库保证同一行只有一个赢家，
        靠返回的受影响行数判断自己是不是赢家。
        """
        from data_engine.storage.models import CryptoPendingOrder
        session = self._session()
        try:
            claimed = session.query(CryptoPendingOrder).filter(
                CryptoPendingOrder.order_ref == order_ref,
                CryptoPendingOrder.status == "PENDING",
            ).update({"status": "EXECUTING", "confirmed_at": datetime.now()},
                     synchronize_session=False)
            session.commit()
            if not claimed:
                # 没抢到：单不存在，或已被另一个请求/另一个标签页确认过
                row = session.query(CryptoPendingOrder).filter(
                    CryptoPendingOrder.order_ref == order_ref).first()
                if not row:
                    raise PendingError(f"待确认单不存在：{order_ref}")
                raise PendingError(f"单状态为 {row.status}，不可确认（仅 PENDING 可）")
            row = session.query(CryptoPendingOrder).filter(
                CryptoPendingOrder.order_ref == order_ref).first()
            return {"strategy_id": row.strategy_id, "symbol": row.symbol, "side": row.side,
                    "quote_amount": row.quote_amount, "quantity": row.quantity,
                    "decision_price": row.price}
        finally:
            session.close()

    def _alert_needs_reconcile(self, order_ref: str, intent: dict, reason: str) -> None:
        """状态未知/执行中断 → 发风险告警催人工去币安对账。留痕失败不影响主流程。"""
        try:
            from business_events import RISK_ALERT, publish_event
            publish_event(RISK_ALERT, source="crypto_strategy", symbol=intent.get("symbol"),
                          title=f"待对账：{intent.get('side')} {intent.get('symbol')} "
                                f"（单 {order_ref}）状态未知，请去币安核对是否已成交。{reason}",
                          order_ref=order_ref)
        except Exception:  # noqa: BLE001
            pass

    # ──────────────── 此刻重新决策的核心 ────────────────

    def _revalidate_and_execute(self, intent: dict, order_ref: str = "") -> dict:
        from acquisition.markets import binance_trade as bt
        if not bt.has_credentials():
            return {"ok": False, "reason": "币安 API key 未配置，无法成交"}
        from crypto_intel_engine import execution as ex
        from trading_engine.brokers.base import OrderStatus
        from trading_engine.brokers.binance_broker import get_binance_broker

        symbol, side = intent["symbol"], intent["side"]
        broker = get_binance_broker()
        if not broker.connect():
            return {"ok": False, "reason": "币安连接失败"}

        cur = broker.get_current_price(symbol)
        if not cur or cur <= 0:
            return {"ok": False, "reason": "拿不到当前价"}

        # 载策略（可能被改/退役）→ 重验条件 + 取漂移阈值
        spec = self._load_spec(intent["strategy_id"])
        drift_thr = spec.guardrails.max_confirm_slippage_pct if spec else _DEFAULT_DRIFT

        # ① 漂移：现价 vs 决策价
        dec = intent.get("decision_price")
        drift = abs(cur - dec) / dec if dec else 0.0
        if dec and drift > drift_thr:
            return {"stale": True,
                    "reason": f"行情已漂移 {drift:.2%}（决策 {dec:.6g}→现价 {cur:.6g}），"
                              f"超阈值 {drift_thr:.2%}，已作废，请等引擎按现价重排"}

        # ② 重验触发条件（理由还成不成立）
        if spec:
            from crypto_strategy.evaluator import evaluate
            broker_info = ex.crypto_broker_info(broker)
            cur_pct = self._current_pct(symbol, broker_info)
            from crypto_intel_engine import analyze_crypto_symbol
            analysis = analyze_crypto_symbol(symbol, broker_info=broker_info,
                                             current_position_pct=cur_pct)
            rules = spec.entry_rules.when if side == "BUY" else spec.exit_rules.when
            hit, fired = evaluate(rules, analysis)
            if not hit:
                return {"stale": True, "reason": f"{side} 触发条件此刻已不成立：{fired}"}
        else:
            broker_info = ex.crypto_broker_info(broker)

        # ③ 按现价重算币量（BUY 用冻结的目标 USDT；SELL 用现持仓）
        if side == "BUY":
            quote = intent.get("quote_amount") or (intent.get("quantity") or 0) * (dec or cur)
            qty = quote / cur
        else:
            qty = self._held_qty(broker, symbol)
            if qty <= 0:
                return {"stale": True, "reason": "确认时已无该币持仓，卖单作废"}

        # ④ 重跑风控（现价现量，不可绕过的第二道闸）
        passed, _msgs, failed = ex.risk_check(symbol, side, qty, cur, broker_info)
        if not passed:
            return {"ok": False, "reason": "确认时风控未通过：" + "；".join(failed)}

        # ⑤ 买入自动补足 → 市价成交（现价，不再用旧限价）
        if side == "BUY":
            acct = broker.get_account_info()
            need = round(qty * cur, 2)
            steps, short = ex.plan_funding(acct, round(need * ex.FUND_BUFFER, 2))
            if short > 0:
                return {"ok": False, "reason": f"买力不足，仍缺 {short} USDT"}
            if steps and not ex.execute_funding(broker, steps, need):
                return {"ok": False, "reason": "自动补足现货失败，未下单"}

        # 卖出自动腾挪：币在活期理财/资金钱包时先赎回+划转到现货再卖，否则交易所拒
        if side == "SELL":
            spot_ok, spot_reason = ex.ensure_spot_for_sell(broker, symbol, qty)
            if not spot_ok:
                return {"ok": False, "reason": f"{spot_reason}，未下单"}

        # 幂等键用 order_ref：同一张待确认单无论重试多少次，落到币安都是同一个 clientOrderId
        order = broker.submit_order(symbol, side, qty, None,   # None=市价，按现价成交
                                    client_order_id=order_ref or None)

        # ⛔ 判定顺序：**先看成交量，再看状态**。币安市价单在薄盘/价格带/STP 场景会返回
        # EXPIRED（映射成 REJECTED）**同时带 executedQty > 0** —— 只看状态就会把「已经买到
        # 一部分」判成完全失败：不落台账 → 系统以为没花钱，账上却真多了币 → 下 tick 重排
        # 同向单 → 重复买入。
        filled = order.filled_quantity or 0.0
        terminal_bad = order.status in (OrderStatus.CANCELLED, OrderStatus.REJECTED,
                                        OrderStatus.FAILED)
        if order.status == OrderStatus.UNKNOWN:
            return {"unknown": True, "order_id": order.order_id,
                    "reason": order.error_msg or "订单状态未知"}
        if filled <= 0:
            if terminal_bad:
                return {"ok": False, "reason": order.error_msg or "交易所拒单"}
            # 市价单却零成交（薄盘/交易对暂停）——已受理但没吃到量，不是成交也不是失败
            ex.record_crypto_resting(symbol, side, None, order.order_id, order_type="MARKET")
            return {"ok": True, "order_id": order.order_id, "fill_price": None,
                    "fill_qty": 0.0, "drift": round(drift, 4),
                    "note": "市价单零成交（未吃到量），请去币安核对"}

        fill_price = order.filled_price or cur
        ex.record_crypto_trade(symbol, side, fill_price, filled, order.order_id,
                               commission=order.commission or 0.0)
        if side == "SELL":
            ex.auto_sweep_to_earn(broker)
        # terminal_bad 且 filled>0 = 部分成交后余量被撤/过期：成交那部分是**真的**，必须落账
        partial = terminal_bad or filled < qty
        return {"ok": True, "order_id": order.order_id, "fill_price": fill_price,
                "fill_qty": filled, "drift": round(drift, 4), "partial": partial,
                "note": (f"部分成交 {filled:g}/{qty:g}，余量未成交" if partial else None)}

    # ──────────────── 小工具 ────────────────

    def _load_spec(self, strategy_id: str):
        try:
            from crypto_strategy.service import spec_from_row
            from data_engine.storage.database import get_session
            from data_engine.storage.models import CryptoStrategy
            session = get_session()
            try:
                row = session.query(CryptoStrategy).filter(
                    CryptoStrategy.strategy_id == strategy_id).first()
                return spec_from_row(row) if row else None
            finally:
                session.close()
        except Exception as e:  # noqa: BLE001
            logger.warning(f"确认时载策略失败 {strategy_id}: {e}")
            return None

    def _current_pct(self, symbol, broker_info) -> float:
        total = float((broker_info or {}).get("total_value") or 0.0)
        pos = ((broker_info or {}).get("positions") or {}).get(symbol)
        if pos and total:
            return round((pos.get("market_value") or 0.0) / total * 100, 1)
        return 0.0

    def _held_qty(self, broker, symbol) -> float:
        try:
            p = broker.get_position(symbol)
            return float(p.quantity) if p else 0.0
        except Exception:  # noqa: BLE001
            return 0.0

    def _delete(self, order_ref: str) -> None:
        from data_engine.storage.models import CryptoPendingOrder
        session = self._session()
        try:
            session.query(CryptoPendingOrder).filter(
                CryptoPendingOrder.order_ref == order_ref).delete(synchronize_session=False)
            session.commit()
        finally:
            session.close()

    def _finalize(self, order_ref: str, status: str, *, order_id=None, fill_price=None,
                  fill_qty=None, drift=None, error=None) -> None:
        from data_engine.storage.models import CryptoPendingOrder
        session = self._session()
        try:
            row = session.query(CryptoPendingOrder).filter(
                CryptoPendingOrder.order_ref == order_ref).first()
            if row:
                row.status = status
                row.executed_order_id = order_id
                row.fill_price = fill_price
                row.fill_quantity = fill_qty
                if error:
                    row.error_message = error
                if drift is not None:
                    existing = json.loads(row.reason) if row.reason else {}
                    existing["confirm_drift"] = drift
                    row.reason = json.dumps(existing, ensure_ascii=False, default=str)
                session.commit()
        except Exception as e:  # noqa: BLE001
            logger.warning(f"待确认单收尾写入失败 {order_ref}: {e}")
        finally:
            session.close()


crypto_pending_service = CryptoPendingOrderService()

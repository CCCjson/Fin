"""crypto 半自动策略执行引擎 —— 每 tick 读取已启用策略、评估、过闸、**排队待确认**（需求3）。

决策闸顺序（任一不过即跳过并记原因）：
  kill → 当日亏损熔断 → 单日笔数/费用 → 白名单 → 条件命中 → 成本净边际闸 →
  仓位换算 → 单笔名义/单币敞口上限 → RiskManager(5硬规则不可绕) → mode 分支(paper/live)

⛔ Jason 2026-07-21 拍板半自动：引擎**永不自动成交**。
  - paper：只写 CryptoStrategyRun 标「本该下的单」，不排单、不碰币安、不写 CryptoTrade。
  - live：产 `CryptoPendingOrder` 待确认单 → 推给 Jason 逐笔确认 → 确认时再跑风控才成交。
逐笔人工确认红线原样保留。执行/风控/资金/台账复用 `crypto_intel_engine/execution.py`。
"""
import json
from datetime import date, datetime
from typing import Any

from loguru import logger

from crypto_intel_engine.dsl import gross_target_edge
from crypto_strategy import guardrails as gr
from crypto_strategy.evaluator import evaluate
from crypto_strategy.service import spec_from_row


class CryptoStrategyEngine:
    """一次 tick 的执行体（同步；由 scheduler через run_in_executor 卸载出事件循环）。"""

    def run(self) -> dict[str, Any]:
        if gr.is_killed():
            logger.info("crypto 策略引擎：kill-switch 开启，本 tick 全停")
            return {"killed": True, "evaluated": 0}

        # 先清理：过期待确认单立即删 + 成交/失败单短期后删（防堆积，前端不吃陈单）
        try:
            from crypto_strategy.pending import crypto_pending_service
            cleaned = crypto_pending_service.cleanup()
            if cleaned.get("expired_deleted") or cleaned.get("terminal_deleted"):
                logger.info(f"crypto 策略引擎：清理待确认单 {cleaned}")
        except Exception as e:  # noqa: BLE001
            logger.warning(f"待确认单清理失败: {e}")

        rows = self._load_enabled()
        ran = 0
        for row in rows:
            if not self._due(row):
                continue
            ran += 1
            try:
                self._run_one(row)
            except Exception as e:  # noqa: BLE001 — 单策略炸不掀翻整 tick
                logger.warning(f"crypto 策略 {row.strategy_id} 本 tick 失败: {e}")
                self._log_run(row.strategy_id, "error", mode=row.mode, error=str(e))
        return {"killed": False, "strategies": len(rows), "ran": ran}

    # ──────────────── 加载 / 到期 ────────────────

    def _load_enabled(self) -> list:
        from data_engine.storage.database import get_session
        from data_engine.storage.models import CryptoStrategy
        session = get_session()
        try:
            return session.query(CryptoStrategy).filter(CryptoStrategy.enabled == 1).all()
        finally:
            session.close()

    def _due(self, row) -> bool:
        if not row.last_run_at:
            return True
        delta = (datetime.now() - row.last_run_at).total_seconds() / 60.0
        return delta >= (row.interval_minutes or 30)

    # ──────────────── 单策略 ────────────────

    def _run_one(self, row) -> None:
        spec = spec_from_row(row)
        broker, broker_info = self._broker_and_info(has_key_required=(row.mode == "live"))
        capital = self._capital(spec, broker_info)

        # 当日亏损熔断（引擎级，权威）
        realized = self._realized_today()
        unreal = float((broker_info or {}).get("unrealized_pnl") or 0.0)
        ok, reason = gr.check_daily_loss(realized, unreal, capital, spec.guardrails.daily_loss_pct)
        if not ok:
            self._halt(row.strategy_id, reason)
            self._log_run(row.strategy_id, "blocked_guardrail", mode=row.mode,
                          detail={"daily_loss": reason})
            return

        # 单日笔数 / 费用
        counts = self._today_counts(row.strategy_id)
        ok, reason = gr.check_daily_counts(counts["orders"], counts["round_trips"],
                                           counts["fees"], spec.guardrails)
        if not ok:
            self._log_run(row.strategy_id, "skipped", mode=row.mode, detail={"counts": reason})
            self._touch(row.strategy_id)
            return

        decisions: list[dict] = []
        staged_refs: list[str] = []
        staged = 0
        for symbol in spec.universe.symbols:
            wl_ok, wl_reason = gr.check_whitelist(symbol, spec.guardrails.symbol_whitelist)
            if not wl_ok:
                decisions.append({"symbol": symbol, "status": "blocked_guardrail", "reason": wl_reason})
                continue
            d = self._decide_symbol(spec, symbol, broker, broker_info, capital)
            decisions.append(d)
            if d.get("action") and d.get("status") == "ready":
                ref = self._stage_or_log(row, spec, symbol, d)
                if ref:
                    staged += 1
                    staged_refs.append(ref)

        status = "order_staged" if staged else "evaluated"
        self._log_run(row.strategy_id, status, mode=row.mode,
                      symbols=len(spec.universe.symbols), orders=staged,
                      detail={"decisions": decisions}, order_ids=staged_refs or None,
                      realized=realized)
        self._touch(row.strategy_id, signaled=staged > 0)

    # ──────────────── 逐币决策（不执行，可单测）────────────────

    def _decide_symbol(self, spec, symbol: str, broker, broker_info: dict, capital: float) -> dict:
        from crypto_intel_engine import analyze_crypto_symbol

        current_pct = self._current_pct(symbol, broker_info)
        analysis = analyze_crypto_symbol(symbol, broker_info=broker_info,
                                         current_position_pct=current_pct)
        if analysis.get("error"):
            return {"symbol": symbol, "status": "skipped", "reason": analysis["error"]}

        held = current_pct > 0 or self._held_qty(symbol, broker) > 0
        d: dict[str, Any] = {"symbol": symbol, "composite": analysis.get("composite"),
                             "recommendation": analysis.get("recommendation"), "held": held}

        entry_hit, entry_fired = evaluate(spec.entry_rules.when, analysis)
        exit_hit, exit_fired = evaluate(spec.exit_rules.when, analysis)
        price = (analysis.get("price") or {}).get("latest")

        # 持仓 → 看卖出（允许砍亏，不过成本闸）
        if held and exit_hit:
            qty = self._held_qty(symbol, broker)
            if qty <= 0:
                return {**d, "status": "skipped", "reason": "退出命中但读不到持仓数量"}
            ok, msgs, failed = self._risk(symbol, "SELL", qty, price, broker_info)
            if not ok:
                return {**d, "status": "blocked_risk", "action": "SELL", "reason": failed}
            return {**d, "status": "ready", "action": "SELL", "qty": qty, "price": price,
                    "fired": exit_fired}

        # 空仓 → 看买入（过成本净边际闸）
        if not held and entry_hit:
            cm = spec.cost_model
            gross = gross_target_edge(cm, price, analysis.get("take_profit"))
            cost_ok, cost_reason, net = gr.cost_gate(gross, cm)
            tp_ok, tp_reason = gr.take_profit_clears_cost(price, analysis.get("take_profit"), cm)
            if not (cost_ok and tp_ok):
                return {**d, "status": "blocked_cost", "action": "BUY", "net_edge": net,
                        "reason": cost_reason if not cost_ok else tp_reason, "fired": entry_fired}
            qty = self._size_buy(spec, symbol, price, analysis, broker_info, capital)
            if not qty or qty <= 0:
                return {**d, "status": "skipped", "action": "BUY", "reason": "仓位换算为0（资金/最小下单量）"}
            notional = qty * price
            n_ok, n_reason = gr.check_notional_cap(notional, spec.guardrails)
            e_ok, e_reason = gr.check_symbol_exposure(
                symbol, notional, broker_info, spec.position_policy.per_symbol_exposure_cap_pct)
            if not (n_ok and e_ok):
                return {**d, "status": "blocked_guardrail", "action": "BUY",
                        "reason": n_reason if not n_ok else e_reason}
            ok, msgs, failed = self._risk(symbol, "BUY", qty, price, broker_info)
            if not ok:
                return {**d, "status": "blocked_risk", "action": "BUY", "reason": failed}
            return {**d, "status": "ready", "action": "BUY", "qty": qty, "price": price,
                    "net_edge": net, "notional": round(notional, 2), "fired": entry_fired}

        return {**d, "status": "no_signal"}

    # ──────────────── 半自动：paper 只记 / live 排队待确认（永不自动成交）────────────────

    def _stage_or_log(self, row, spec, symbol: str, d: dict) -> str | None:
        """paper：只在日志标「本应下单」，不排单不成交。
        live：排一张待确认单（去重），推给 Jason 逐笔确认。⛔ 引擎绝不自动成交。
        返回 order_ref（live 排单成功）或 None。
        """
        action, qty, price = d["action"], d["qty"], d["price"]
        if row.mode == "paper":
            d["staged"] = False
            d["paper_would"] = f"{action} {qty:g} @ {price}"     # 干跑：只观察
            return None

        from crypto_strategy.pending import crypto_pending_service
        if crypto_pending_service.has_open(row.strategy_id, symbol, action):
            d["status"] = "skipped_dup"
            d["note"] = "已有同向未决单，不重复排"
            return None
        # BUY 冻结**目标 USDT 金额**（确认时按现价重算币量，行情漂移仓位金额仍准）；SELL 不冻金额
        quote_amount = round(qty * price, 2) if (action == "BUY" and price) else None
        pending = crypto_pending_service.create_pending(
            row.strategy_id, symbol, action, qty, price, quote_amount=quote_amount,
            net_edge=d.get("net_edge"),
            reason={"fired": d.get("fired"), "composite": d.get("composite"),
                    "recommendation": d.get("recommendation"), "notional": d.get("notional")})
        d["staged"] = True
        d["pending_ref"] = pending["order_ref"]
        return pending["order_ref"]

    # ──────────────── 小工具 ────────────────

    def _broker_and_info(self, has_key_required: bool):
        from acquisition.markets import binance_trade as bt
        from crypto_intel_engine import execution as ex
        if bt.has_credentials():
            try:
                from trading_engine.brokers.binance_broker import get_binance_broker
                broker = get_binance_broker()
                if broker.connect():
                    return broker, ex.crypto_broker_info(broker)
            except Exception as e:  # noqa: BLE001
                logger.warning(f"引擎连币安失败: {e}")
        if has_key_required:
            raise RuntimeError("live 策略需要币安 key 且连接成功")
        # 无 key：paper 退化为「假设空仓 + 配置资金」
        from trading_engine.risk.adapter import get_total_capital
        cap = get_total_capital()
        return None, {"cash": cap, "market_value": 0.0, "total_value": cap,
                      "unrealized_pnl": 0.0, "positions": {},
                      "recent_closed_pnls": [], "last_loss_date": None}

    def _capital(self, spec, broker_info) -> float:
        if spec.capital_basis == "real_total_value":
            return float((broker_info or {}).get("total_value") or 0.0)
        from trading_engine.risk.adapter import get_total_capital
        return get_total_capital()

    def _current_pct(self, symbol, broker_info) -> float:
        total = float((broker_info or {}).get("total_value") or 0.0)
        pos = ((broker_info or {}).get("positions") or {}).get(symbol)
        if pos and total:
            return round((pos.get("market_value") or 0.0) / total * 100, 1)
        return 0.0

    def _held_qty(self, symbol, broker) -> float:
        if broker is None:
            return 0.0
        try:
            p = broker.get_position(symbol)
            return float(p.quantity) if p else 0.0
        except Exception:  # noqa: BLE001
            return 0.0

    def _size_buy(self, spec, symbol, price, analysis, broker_info, capital) -> float | None:
        from crypto_intel_engine.cockpit import size_crypto_position
        pol = spec.position_policy
        if pol.target_pct_source == "fixed":
            target = pol.fixed_target_pct * 100
        else:
            target = analysis.get("suggested_position_pct") or 0.0
        if not target:
            return None
        try:
            from acquisition.markets.binance_trade import symbol_filters
            filt = symbol_filters(symbol)
        except Exception:  # noqa: BLE001
            filt = {}
        max_pct = pol.max_position_pct
        sizing = size_crypto_position(symbol, price, target, broker_info=broker_info,
                                      max_position_pct=max_pct,
                                      step_size=filt.get("step_size"), min_qty=filt.get("min_qty"),
                                      min_notional=filt.get("min_notional"))
        return (sizing or {}).get("shares") or (sizing or {}).get("quantity")

    def _risk(self, symbol, action, qty, price, broker_info):
        from crypto_intel_engine.execution import risk_check
        return risk_check(symbol, action, qty, price, broker_info)

    def _realized_today(self) -> float:
        """今日已实现盈亏：全量回放加权成本，只累加卖出日期=今天的平仓盈亏。"""
        from data_engine.storage.database import get_session
        from data_engine.storage.models import CryptoTrade
        session = get_session()
        try:
            trades = (session.query(CryptoTrade)
                      .order_by(CryptoTrade.trade_date.asc(), CryptoTrade.id.asc()).all())
        finally:
            session.close()
        today = date.today()
        positions: dict[str, dict[str, float]] = {}
        realized = 0.0
        for t in trades:
            pos = positions.setdefault(t.symbol, {"qty": 0.0, "cost": 0.0, "avg": 0.0})
            if t.side == "BUY":
                pos["cost"] += t.amount + (t.commission or 0)
                pos["qty"] += t.quantity
                if pos["qty"] > 0:
                    pos["avg"] = pos["cost"] / pos["qty"]
            elif t.side == "SELL":
                pnl = (t.amount - (t.commission or 0)) - pos["avg"] * t.quantity
                if t.trade_date == today:
                    realized += pnl
                pos["qty"] -= t.quantity
                pos["cost"] = pos["avg"] * pos["qty"]
        return realized

    def _today_counts(self, strategy_id: str) -> dict:
        """单日笔数=当日为本策略排出的待确认单数（防刷屏）；费用/往返=真实成交台账。"""
        from data_engine.storage.database import get_session
        from data_engine.storage.models import CryptoPendingOrder, CryptoTrade
        session = get_session()
        try:
            day_start = datetime.combine(date.today(), datetime.min.time())
            orders = session.query(CryptoPendingOrder).filter(
                CryptoPendingOrder.strategy_id == strategy_id,
                CryptoPendingOrder.created_at >= day_start).count()
            trades = session.query(CryptoTrade).filter(CryptoTrade.trade_date == date.today()).all()
            fees = sum(t.commission or 0.0 for t in trades)
            round_trips = sum(1 for t in trades if t.side == "SELL")
            return {"orders": orders, "fees": fees, "round_trips": round_trips}
        finally:
            session.close()

    # ──────────────── 落库 ────────────────

    def _log_run(self, strategy_id, status, *, mode=None, symbols=0, orders=0,
                 detail=None, order_ids=None, realized=None, error=None) -> None:
        from data_engine.storage.database import get_session
        from data_engine.storage.models import CryptoStrategyRun
        session = get_session()
        try:
            session.add(CryptoStrategyRun(
                strategy_id=strategy_id, status=status, mode=mode,
                symbols_evaluated=symbols, orders_placed=orders,
                decision_detail=json.dumps(detail, ensure_ascii=False, default=str) if detail else None,
                executed_order_ids=json.dumps(order_ids) if order_ids else None,
                pnl_realized_today=realized, error_message=error,
                completed_at=datetime.now()))
            session.commit()
        except Exception as e:  # noqa: BLE001
            logger.warning(f"策略 run 日志写入失败: {e}")
        finally:
            session.close()

    def _touch(self, strategy_id, signaled: bool = False) -> None:
        from data_engine.storage.database import get_session
        from data_engine.storage.models import CryptoStrategy
        session = get_session()
        try:
            row = session.query(CryptoStrategy).filter(
                CryptoStrategy.strategy_id == strategy_id).first()
            if row:
                row.last_run_at = datetime.now()
                if signaled:
                    row.last_signal_at = datetime.now()
                session.commit()
        finally:
            session.close()

    def _halt(self, strategy_id, reason: str) -> None:
        from data_engine.storage.database import get_session
        from data_engine.storage.models import CryptoStrategy
        session = get_session()
        try:
            row = session.query(CryptoStrategy).filter(
                CryptoStrategy.strategy_id == strategy_id).first()
            if row:
                row.status = "paused_by_guardrail"
                row.enabled = 0
                row.halted_reason = reason
                row.consecutive_guardrail_trips = (row.consecutive_guardrail_trips or 0) + 1
                session.commit()
                logger.warning(f"策略 {strategy_id} 触发护栏熔断停机: {reason}")
        finally:
            session.close()

        try:
            from business_events import RISK_ALERT, publish_event
            publish_event(RISK_ALERT, source="crypto_strategy", symbol=strategy_id,
                          title=f"策略 {strategy_id} 当日回撤熔断停机：{reason}")
        except Exception:  # noqa: BLE001
            pass


crypto_strategy_engine = CryptoStrategyEngine()

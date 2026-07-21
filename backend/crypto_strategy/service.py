"""CryptoStrategyService —— 策略 CRUD + spec 序列化 + 回测闸 + arm 武装。

编译工具（`agents/tools/crypto_strategy_tools.py`）与 API 路由（`api/routes/crypto_strategy.py`）
都调这一份，业务逻辑不重复。DSL 子对象以 JSON 串存 Text 列，读回时重建 pydantic。
"""
import json
from datetime import datetime, timedelta
from typing import Any

from loguru import logger

from crypto_intel_engine.dsl import CryptoStrategySpec
from crypto_strategy.backtest_gate import run_backtest_gate


class StrategyError(Exception):
    """业务拒绝（如未过回测就想 arm）——路由转 400，工具转 negative。"""


# ──────────────────── spec ↔ row 序列化 ────────────────────

_SUBOBJECTS = ("universe", "entry_rules", "exit_rules", "position_policy",
               "cost_model", "guardrails")


def _spec_to_columns(spec: CryptoStrategySpec) -> dict[str, Any]:
    """CryptoStrategySpec → CryptoStrategy 列 dict（子对象转 JSON 串）。"""
    cols: dict[str, Any] = {
        "name": spec.name, "strategy_kind": spec.strategy_kind,
        "interval_minutes": spec.interval_minutes,
        "capital_basis": spec.capital_basis, "mode": spec.mode,
    }
    for key in _SUBOBJECTS:
        cols[key] = getattr(spec, key).model_dump_json()
    return cols


def spec_from_row(row) -> CryptoStrategySpec:
    """CryptoStrategy 行 → CryptoStrategySpec（重建 + 再校验）。"""
    data: dict[str, Any] = {
        "name": row.name, "strategy_kind": row.strategy_kind,
        "interval_minutes": row.interval_minutes,
        "capital_basis": row.capital_basis, "mode": row.mode,
    }
    for key in _SUBOBJECTS:
        raw = getattr(row, key)
        if raw:
            data[key] = json.loads(raw)
    return CryptoStrategySpec(**data)


def _gen_id() -> str:
    import secrets
    return f"CS-{datetime.now().strftime('%Y%m%d%H%M%S')}-{secrets.token_hex(3)}"


def row_summary(row) -> dict[str, Any]:
    """给前端/工具的轻量摘要（不含 JSON 全文）。"""
    return {
        "strategy_id": row.strategy_id, "name": row.name, "mode": row.mode,
        "status": row.status, "enabled": bool(row.enabled),
        "strategy_kind": row.strategy_kind, "interval_minutes": row.interval_minutes,
        "backtest_passed": bool(row.backtest_passed),
        "backtest_net_return": row.backtest_net_return,
        "last_run_at": str(row.last_run_at) if row.last_run_at else None,
        "halted_reason": row.halted_reason,
    }


# ──────────────────── 服务 ────────────────────

class CryptoStrategyService:
    def _session(self):
        from data_engine.storage.database import get_session
        return get_session()

    # ---- 回测（技术代理 + 真实费率）----
    def _load_bars(self, symbols: list[str], lookback_days: int = 400) -> dict[str, list[dict]]:
        from data_engine.engine import DataEngine
        now = datetime.now()
        end = now.strftime("%Y-%m-%d")
        start = (now - timedelta(days=lookback_days)).strftime("%Y-%m-%d")
        out: dict[str, list[dict]] = {}
        engine = DataEngine()
        try:
            for sym in symbols:
                try:
                    df = engine.get_daily_data(sym, start, end, db_only=True)
                    if df is not None and not df.empty:
                        d = df.reset_index() if df.index.name else df
                        cols = [c for c in ("date", "open", "high", "low", "close", "volume")
                                if c in d.columns]
                        recs = d[cols].to_dict("records")
                        for r in recs:
                            if "date" in r and hasattr(r["date"], "strftime"):
                                r["date"] = r["date"].strftime("%Y-%m-%d")
                        out[sym] = recs
                except Exception as e:  # noqa: BLE001
                    logger.warning(f"回测取 bars 失败 {sym}: {e}")
        finally:
            engine.close()
        return out

    def backtest(self, spec: CryptoStrategySpec) -> dict[str, Any]:
        bars = self._load_bars(spec.universe.symbols)
        return run_backtest_gate(spec, bars)

    # ---- 编译落库（校验已在 spec 构造时完成）----
    def compile_and_persist(self, spec: CryptoStrategySpec, description_nl: str | None = None,
                            do_backtest: bool = True) -> dict[str, Any]:
        from data_engine.storage.models import CryptoStrategy

        bt = self.backtest(spec) if do_backtest else None
        session = self._session()
        try:
            row = CryptoStrategy(strategy_id=_gen_id(), description_nl=description_nl,
                                 enabled=0, status="draft", **_spec_to_columns(spec))
            if bt is not None:
                row.last_backtest_at = datetime.now()
                row.backtest_net_return = bt.get("net_return")
                row.backtest_metrics = json.dumps(
                    {"metrics": bt.get("metrics"), "degraded": bt.get("degraded"),
                     "degraded_reasons": bt.get("degraded_reasons"),
                     "per_symbol": bt.get("per_symbol")}, ensure_ascii=False)
                row.backtest_passed = 1 if bt.get("passed") else 0
                row.status = "backtested"
            session.add(row)
            session.commit()
            return {"strategy_id": row.strategy_id, "summary": row_summary(row), "backtest": bt}
        finally:
            session.close()

    # ---- 读 ----
    def list_strategies(self, *, mode: str | None = None, enabled: bool | None = None) -> list[dict]:
        from data_engine.storage.models import CryptoStrategy
        session = self._session()
        try:
            q = session.query(CryptoStrategy)
            if mode:
                q = q.filter(CryptoStrategy.mode == mode)
            if enabled is not None:
                q = q.filter(CryptoStrategy.enabled == (1 if enabled else 0))
            return [row_summary(r) for r in q.order_by(CryptoStrategy.created_at.desc()).all()]
        finally:
            session.close()

    def get_strategy(self, strategy_id: str) -> dict:
        session = self._session()
        try:
            row = self._get_row(session, strategy_id)
            detail = row_summary(row)
            detail["spec"] = spec_from_row(row).model_dump()
            detail["description_nl"] = row.description_nl
            detail["backtest_metrics"] = json.loads(row.backtest_metrics) if row.backtest_metrics else None
            return detail
        finally:
            session.close()

    # ---- 生命周期 ----
    def arm(self, strategy_id: str) -> dict:
        """武装 live：开始按 DSL 规则产**待确认单**（每笔仍由 Jason 逐笔确认才成交）。

        ⛔ 半自动系统里安全靠**逐笔确认 + 护栏 + 硬风控**，不靠回测——故 arm **不卡回测**
        （回测是双均线代理、不测你的 DSL 规则，仅供参考）。烂策略只会提烂建议、被你拒掉，
        赔不了钱。想纯观察不产真单用 enable_paper。
        """
        session = self._session()
        try:
            row = self._get_row(session, strategy_id)
            row.mode = "live"
            row.enabled = 1
            row.status = "armed"
            row.halted_reason = None
            session.commit()
            return row_summary(row)
        finally:
            session.close()

    def enable_paper(self, strategy_id: str) -> dict:
        """纸面启用（不需回测通过，纯模拟不动真钱）。"""
        session = self._session()
        try:
            row = self._get_row(session, strategy_id)
            row.mode = "paper"
            row.enabled = 1
            row.status = "armed"
            row.halted_reason = None
            session.commit()
            return row_summary(row)
        finally:
            session.close()

    def pause(self, strategy_id: str) -> dict:
        return self._set_state(strategy_id, enabled=0, status="draft")

    def retire(self, strategy_id: str) -> dict:
        """退役 = **彻底删除**策略（连它的待确认单 + 运行日志一起清），从列表消失。

        想只是暂时停用、留着以后再 arm 的用 pause。删除是终态、不可恢复。
        """
        from data_engine.storage.models import (
            CryptoPendingOrder,
            CryptoStrategyRun,
        )
        session = self._session()
        try:
            row = self._get_row(session, strategy_id)   # 不存在则抛 StrategyError
            session.query(CryptoPendingOrder).filter(
                CryptoPendingOrder.strategy_id == strategy_id).delete(synchronize_session=False)
            session.query(CryptoStrategyRun).filter(
                CryptoStrategyRun.strategy_id == strategy_id).delete(synchronize_session=False)
            session.delete(row)
            session.commit()
            return {"strategy_id": strategy_id, "status": "deleted", "deleted": True}
        finally:
            session.close()

    def run_and_store_backtest(self, strategy_id: str) -> dict:
        """按需重跑回测并回写（PUT 改 DSL 后 / 手动触发）。"""
        session = self._session()
        try:
            row = self._get_row(session, strategy_id)
            spec = spec_from_row(row)
            bt = self.backtest(spec)
            row.last_backtest_at = datetime.now()
            row.backtest_net_return = bt.get("net_return")
            row.backtest_metrics = json.dumps(
                {"metrics": bt.get("metrics"), "degraded": bt.get("degraded"),
                 "degraded_reasons": bt.get("degraded_reasons"),
                 "per_symbol": bt.get("per_symbol")}, ensure_ascii=False)
            row.backtest_passed = 1 if bt.get("passed") else 0
            if row.status in ("draft",):
                row.status = "backtested"
            session.commit()
            return {"summary": row_summary(row), "backtest": bt}
        finally:
            session.close()

    # ---- 内部 ----
    def _set_state(self, strategy_id: str, *, enabled: int, status: str) -> dict:
        session = self._session()
        try:
            row = self._get_row(session, strategy_id)
            row.enabled = enabled
            row.status = status
            session.commit()
            return row_summary(row)
        finally:
            session.close()

    def _get_row(self, session, strategy_id: str):
        from data_engine.storage.models import CryptoStrategy
        row = session.query(CryptoStrategy).filter(
            CryptoStrategy.strategy_id == strategy_id).first()
        if not row:
            raise StrategyError(f"策略不存在：{strategy_id}")
        return row


crypto_strategy_service = CryptoStrategyService()

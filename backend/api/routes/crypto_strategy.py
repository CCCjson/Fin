"""crypto 半自动策略 API —— 策略 CRUD / 回测 / arm 武装 / 引擎控制 / kill / 待确认单确认。

半自动：引擎产决策 + 排待确认单，Jason 在此确认/拒绝。逐笔确认红线保留，引擎不自动成交。
业务逻辑全在 `crypto_strategy/service.py` 与 `crypto_strategy/pending.py`，路由只做薄封装。
"""
from typing import Any

from fastapi import APIRouter, Body, HTTPException, Query
from loguru import logger

from common.market_time import utc_iso
from crypto_strategy.pending import PendingError, crypto_pending_service
from crypto_strategy.service import StrategyError, crypto_strategy_service

router = APIRouter(prefix="/crypto-strategy", tags=["加密半自动策略"])


def _400(e: Exception):
    return HTTPException(status_code=400, detail=str(e))


# ──────────────── 策略 CRUD ────────────────

@router.get("/strategies")
async def list_strategies(mode: str | None = Query(None), enabled: bool | None = Query(None)):
    return {"strategies": crypto_strategy_service.list_strategies(mode=mode, enabled=enabled)}


@router.get("/strategies/{strategy_id}")
async def get_strategy(strategy_id: str):
    try:
        return crypto_strategy_service.get_strategy(strategy_id)
    except StrategyError as e:
        raise _400(e) from e


@router.post("/strategies")
async def create_strategy(payload: dict = Body(...)):
    """从结构化 spec 落库（校验 + 净费回测）。payload={spec:{...}, description_nl?:str}。"""
    from pydantic import ValidationError

    from crypto_intel_engine.dsl import CryptoStrategySpec
    try:
        spec = CryptoStrategySpec.model_validate(payload.get("spec") or {})
    except ValidationError as e:
        raise HTTPException(status_code=422, detail=e.errors()) from e
    try:
        return crypto_strategy_service.compile_and_persist(
            spec, description_nl=payload.get("description_nl"))
    except Exception as e:  # noqa: BLE001
        logger.warning(f"创建 crypto 策略失败: {e}")
        raise _400(e) from e


@router.post("/strategies/{strategy_id}/backtest")
async def backtest_strategy(strategy_id: str):
    try:
        return crypto_strategy_service.run_and_store_backtest(strategy_id)
    except StrategyError as e:
        raise _400(e) from e


@router.post("/strategies/{strategy_id}/arm")
async def arm_strategy(strategy_id: str):
    """武装 live：引擎开始按 DSL 产**待确认单**（每笔仍由 Jason 点确认才成交）。

    ⚠️ **不以回测为门槛**（刻意设计，见 `service.arm` 的说明）：半自动的安全边界是
    逐笔人工确认 + 护栏 + 硬风控，而那个回测是双均线代理、并不测你写的 DSL 规则。
    此处此前写着「必须已过净费回测否则 400」，与实现不符，已更正。
    """
    try:
        return crypto_strategy_service.arm(strategy_id)
    except StrategyError as e:
        raise _400(e) from e


@router.post("/strategies/{strategy_id}/enable-paper")
async def enable_paper(strategy_id: str):
    try:
        return crypto_strategy_service.enable_paper(strategy_id)
    except StrategyError as e:
        raise _400(e) from e


@router.post("/strategies/{strategy_id}/pause")
async def pause_strategy(strategy_id: str):
    try:
        return crypto_strategy_service.pause(strategy_id)
    except StrategyError as e:
        raise _400(e) from e


@router.post("/strategies/{strategy_id}/retire")
async def retire_strategy(strategy_id: str):
    try:
        return crypto_strategy_service.retire(strategy_id)
    except StrategyError as e:
        raise _400(e) from e


# ──────────────── 运行日志 ────────────────

@router.get("/runs")
async def list_runs(strategy_id: str = Query(...), limit: int = Query(50, le=500)):
    from data_engine.storage.database import get_session
    from data_engine.storage.models import CryptoStrategyRun
    session = get_session()
    try:
        rows = (session.query(CryptoStrategyRun)
                .filter(CryptoStrategyRun.strategy_id == strategy_id)
                .order_by(CryptoStrategyRun.started_at.desc()).limit(limit).all())
        return {"runs": [{"id": r.id, "status": r.status, "mode": r.mode,
                          "symbols_evaluated": r.symbols_evaluated, "orders_placed": r.orders_placed,
                          # 带 offset：前端 CryptoStrategyTab 走 utils/datetime.ts 转本地
                          "started_at": utc_iso(r.started_at),
                          "decision_detail": r.decision_detail, "error": r.error_message}
                         for r in rows]}
    finally:
        session.close()


# ──────────────── 引擎控制 + kill ────────────────

@router.get("/engine/status")
async def engine_status():
    from crypto_strategy.scheduler import crypto_strategy_scheduler
    return crypto_strategy_scheduler.get_status()


@router.post("/engine/start")
async def engine_start():
    from crypto_strategy.scheduler import crypto_strategy_scheduler
    crypto_strategy_scheduler.start(force=True)   # 手动点按钮：无视开机自启的 env 默认，强制起
    return crypto_strategy_scheduler.get_status()


@router.post("/engine/stop")
async def engine_stop():
    from crypto_strategy.scheduler import crypto_strategy_scheduler
    crypto_strategy_scheduler.stop()
    return crypto_strategy_scheduler.get_status()


@router.post("/engine/kill")
async def engine_kill():
    """总开关：立刻停摆整个引擎（任何策略都不再产单）。"""
    from crypto_strategy import guardrails as gr
    gr.set_killed(True)
    return {"killed": True}


@router.post("/engine/unkill")
async def engine_unkill():
    from crypto_strategy import guardrails as gr
    gr.set_killed(False)
    return {"killed": False}


# ──────────────── 待确认单：列表 / 确认 / 拒绝 ────────────────

@router.get("/pending")
async def list_pending(status: str | None = Query("PENDING"), limit: int = Query(100, le=500)):
    return {"pending": crypto_pending_service.list_pending(status=status, limit=limit)}


@router.post("/pending/{order_ref}/confirm")
async def confirm_pending(order_ref: str) -> dict[str, Any]:
    """Jason 逐笔确认 → 重跑风控 → 真成交。"""
    try:
        return crypto_pending_service.confirm(order_ref)
    except PendingError as e:
        raise _400(e) from e


@router.post("/pending/{order_ref}/reject")
async def reject_pending(order_ref: str):
    try:
        return crypto_pending_service.reject(order_ref)
    except PendingError as e:
        raise _400(e) from e

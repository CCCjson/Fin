"""
Alpha Lab API — AI 自动策略生成与迭代优化
"""
import asyncio
import json
import queue
import threading
from typing import Optional, List, Dict, Any

from fastapi import APIRouter, HTTPException
from fastapi.responses import StreamingResponse
from loguru import logger
from pydantic import BaseModel, Field

from alpha_lab.engine import AlphaLabEngine

router = APIRouter(prefix="/alpha-lab", tags=["Alpha Lab"])

# 全局引擎实例
_engine = AlphaLabEngine()


# ==================== 请求/响应模型 ====================

class StartRequest(BaseModel):
    """启动 Alpha Lab 会话"""
    target_symbols: List[str] = Field(..., description="目标股票列表，如 ['600519.SH']")
    optimization_goal: str = Field("sharpe", description="优化目标: sharpe/return/win_rate/drawdown")
    data_start: str = Field(..., description="数据开始日期 YYYY-MM-DD")
    data_end: str = Field(..., description="数据结束日期 YYYY-MM-DD")
    max_iterations: int = Field(15, description="最大迭代轮数", ge=3, le=30)
    initial_capital: float = Field(1000000.0, description="初始资金")
    constraints: Optional[Dict[str, Any]] = Field(None, description="额外约束")
    provider: Optional[str] = Field(None, description="AI 模型提供商: local/openai/claude")


class IterateRequest(BaseModel):
    """继续迭代"""
    session_id: str
    additional_iterations: int = Field(5, ge=1, le=15)
    user_feedback: Optional[str] = None


# ==================== 流式端点 ====================

@router.post("/start", summary="启动 Alpha Lab 会话（流式 NDJSON）")
async def start_alpha_lab(request: StartRequest):
    """
    启动新的 Alpha Lab 探索会话。

    返回 NDJSON 流式事件：
    - session_created, preparing_data, data_ready
    - iteration_start, generating_code, code_generated, ast_check_passed
    - backtest_running, backtest_done, evaluation_done, iteration_complete
    - phase_change, early_stop, session_complete, error
    """

    async def _streaming():
        try:
            sync_gen = _engine.start_session(
                target_symbols=request.target_symbols,
                optimization_goal=request.optimization_goal,
                data_start=request.data_start,
                data_end=request.data_end,
                max_iterations=request.max_iterations,
                initial_capital=request.initial_capital,
                constraints=request.constraints,
                provider=request.provider,
            )

            # 同步生成器 → 异步流（thread + queue 桥接）
            chunk_queue: queue.Queue = queue.Queue()
            _SENTINEL = object()

            def _drain():
                try:
                    for item in sync_gen:
                        chunk_queue.put(item)
                except Exception as exc:
                    chunk_queue.put(exc)
                finally:
                    chunk_queue.put(_SENTINEL)

            thread = threading.Thread(target=_drain, daemon=True)
            thread.start()

            while True:
                try:
                    item = chunk_queue.get_nowait()
                except queue.Empty:
                    await asyncio.sleep(0.05)
                    continue

                if item is _SENTINEL:
                    break
                if isinstance(item, Exception):
                    raise item

                yield item
                await asyncio.sleep(0)

        except Exception as e:
            logger.error(f"Alpha Lab 流式异常: {e}")
            yield json.dumps(
                {"event": "error", "message": str(e)},
                ensure_ascii=False,
            ) + "\n"

    return StreamingResponse(
        _streaming(),
        media_type="application/x-ndjson",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        },
    )


# ==================== REST 端点 ====================

@router.get("/providers", summary="获取可用 AI 模型提供商列表")
async def list_providers():
    """获取所有可用的 AI provider"""
    providers = _engine.get_providers()
    return {"providers": providers}


@router.get("/sessions", summary="获取会话列表")
async def list_sessions(status: Optional[str] = None):
    """获取 Alpha Lab 会话列表"""
    try:
        sessions = _engine.get_sessions(status=status)
        return {"sessions": sessions, "total": len(sessions)}
    except Exception as e:
        logger.error(f"获取会话列表失败: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/sessions/{session_id}", summary="获取会话详情")
async def get_session_detail(session_id: str):
    """获取会话详情（含所有策略）"""
    detail = _engine.get_session_detail(session_id)
    if not detail:
        raise HTTPException(status_code=404, detail="会话不存在")
    return detail


@router.get("/strategies/{strategy_id}", summary="获取策略详情")
async def get_strategy_detail(strategy_id: str):
    """获取策略代码和完整回测结果"""
    strategy = _engine.get_strategy_detail(strategy_id)
    if not strategy:
        raise HTTPException(status_code=404, detail="策略不存在")
    return strategy


@router.delete("/sessions/{session_id}", summary="删除会话")
async def delete_session(session_id: str):
    """删除指定会话及其关联的策略和日志"""
    try:
        deleted = _engine.delete_session(session_id)
        if not deleted:
            raise HTTPException(status_code=404, detail="会话不存在")
        return {"success": True, "message": "会话已删除"}
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"删除会话失败: {e}")
        raise HTTPException(status_code=500, detail=str(e))

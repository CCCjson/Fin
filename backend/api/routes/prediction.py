"""
股价预测 API — 支持远程 GPU 训练
"""
import asyncio
import json
import queue
import threading
from fastapi import APIRouter, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field
from typing import Optional
from loguru import logger

from prediction_engine.engine import PredictionEngine
from prediction_engine.validator import PredictionValidator

router = APIRouter(prefix="/prediction", tags=["股价预测"])


# ──────────────────── 请求模型 ────────────────────

class TrainRequest(BaseModel):
    symbol: str = Field(..., description="股票代码", example="000001.SZ")
    period: str = Field("2y", description="训练数据周期", example="2y")
    forward_days: int = Field(5, description="预测天数", ge=1, le=30)
    epochs: int = Field(100, description="LSTM 训练轮数", ge=10, le=500)
    hidden_dim: int = Field(256, description="LSTM 隐藏层维度", ge=64, le=1024)
    batch_size: int = Field(64, description="Batch size", ge=8, le=256)


class PredictRequest(BaseModel):
    symbol: str = Field(..., description="股票代码", example="000001.SZ")
    forward_days: int = Field(5, description="预测天数", ge=1, le=30)


class BackfillRequest(BaseModel):
    pass  # 无需参数


# ──────────────────── 训练（远程 GPU 流式进度） ────────────────────

@router.post("/train", summary="训练预测模型（远程 GPU，流式进度）")
async def train_model(request: TrainRequest):
    """
    通过 SSH 在远程 GPU 服务器训练 LSTM + XGBoost 模型，流式返回进度。
    """
    from prediction_engine.remote_predict import run_training, is_training

    if is_training(request.symbol):
        raise HTTPException(status_code=409, detail=f"{request.symbol} 正在训练中")

    async def _stream():
        sync_gen = run_training(
            symbol=request.symbol,
            period=request.period,
            forward_days=request.forward_days,
            epochs=request.epochs,
            hidden_dim=request.hidden_dim,
            batch_size=request.batch_size,
        )

        # 同步生成器 → 异步流
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
                yield json.dumps({"event": "error", "message": str(item)}, ensure_ascii=False) + "\n"
                break

            yield item
            await asyncio.sleep(0)

    return StreamingResponse(
        _stream(),
        media_type="application/x-ndjson",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@router.post("/train/stop", summary="终止训练")
async def stop_training(symbol: str = ""):
    """终止指定股票的远程训练"""
    from prediction_engine.remote_predict import stop_training
    if not symbol:
        raise HTTPException(status_code=400, detail="请指定 symbol")
    stopped = stop_training(symbol)
    return {"stopped": stopped}


@router.get("/train/status", summary="训练状态")
async def training_status(symbol: Optional[str] = None):
    """检查是否有远程训练正在进行"""
    from prediction_engine.remote_predict import is_training
    return {"running": is_training(symbol)}


# ──────────────────── 模型列表 ────────────────────

@router.get("/models", summary="查询已训练模型列表")
async def get_models(symbol: Optional[str] = None):
    """返回所有已训练模型的信息"""
    try:
        engine = PredictionEngine()
        models = engine.get_model_info(symbol)
        return {"count": len(models), "models": models}
    except Exception as e:
        logger.error(f"查询模型列表失败: {e}")
        raise HTTPException(status_code=500, detail=str(e))


# ──────────────────── 预测 ────────────────────

@router.post("/predict", summary="生成股价预测")
async def predict(request: PredictRequest):
    """生成未来 N 天的股价预测"""
    try:
        engine = PredictionEngine()
        result = engine.predict(
            symbol=request.symbol,
            forward_days=request.forward_days,
        )
        return result
    except FileNotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except Exception as e:
        logger.error(f"预测失败: {e}")
        raise HTTPException(status_code=500, detail=str(e))


# ──────────────────── 历史记录 ────────────────────

@router.get("/history", summary="查询历史预测记录")
async def get_history(symbol: Optional[str] = None, limit: int = 50):
    """查询历史预测记录"""
    try:
        validator = PredictionValidator()
        records = validator.get_history(symbol, limit)
        return {"count": len(records), "records": records}
    except Exception as e:
        logger.error(f"查询历史记录失败: {e}")
        raise HTTPException(status_code=500, detail=str(e))


# ──────────────────── 手动回填 ────────────────────

@router.post("/backfill", summary="手动触发预测结果回填")
async def backfill():
    """回填所有已到期但未验证的预测记录"""
    try:
        validator = PredictionValidator()
        result = validator.backfill_outcomes()
        return result
    except Exception as e:
        logger.error(f"回填失败: {e}")
        raise HTTPException(status_code=500, detail=str(e))


# ──────────────────── 预测表现 ────────────────────

@router.get("/performance", summary="预测准确率统计")
async def get_performance(symbol: Optional[str] = None):
    """返回预测表现评估指标"""
    try:
        validator = PredictionValidator()
        result = validator.calc_performance(symbol)
        return result
    except Exception as e:
        logger.error(f"查询表现失败: {e}")
        raise HTTPException(status_code=500, detail=str(e))

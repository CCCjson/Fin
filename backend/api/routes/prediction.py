"""
股价预测 API
"""
import asyncio
import json
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


class PredictRequest(BaseModel):
    symbol: str = Field(..., description="股票代码", example="000001.SZ")
    forward_days: int = Field(5, description="预测天数", ge=1, le=30)


class BackfillRequest(BaseModel):
    pass  # 无需参数


# ──────────────────── 训练（流式进度） ────────────────────

@router.post("/train", summary="训练预测模型（流式进度）")
async def train_model(request: TrainRequest):
    """
    训练 LSTM + XGBoost 模型，流式返回进度。
    """
    engine = PredictionEngine()

    # 用队列在同步回调和异步生成器之间传递进度
    queue: asyncio.Queue = asyncio.Queue()

    def progress_cb(stage: str, progress: float, message: str):
        queue.put_nowait(json.dumps({
            "event": "progress",
            "stage": stage,
            "progress": round(progress, 2),
            "message": message,
        }, ensure_ascii=False) + "\n")

    async def _stream():
        yield json.dumps({
            "event": "start",
            "message": f"开始训练 {request.symbol} ...",
        }, ensure_ascii=False) + "\n"

        loop = asyncio.get_event_loop()

        # 在线程池中运行训练（CPU 密集型）
        train_task = loop.run_in_executor(
            None,
            lambda: engine.train(
                symbol=request.symbol,
                period=request.period,
                forward_days=request.forward_days,
                progress_cb=progress_cb,
            )
        )

        # 持续读取进度队列
        while True:
            try:
                msg = queue.get_nowait()
                yield msg
            except asyncio.QueueEmpty:
                pass

            if train_task.done():
                # 排空剩余消息
                while not queue.empty():
                    yield queue.get_nowait()
                break

            await asyncio.sleep(0.1)

        try:
            result = train_task.result()
            yield json.dumps({
                "event": "complete",
                "progress": 1.0,
                "result": result,
            }, ensure_ascii=False) + "\n"
        except Exception as e:
            logger.error(f"训练失败: {e}")
            yield json.dumps({
                "event": "error",
                "message": str(e),
            }, ensure_ascii=False) + "\n"

    return StreamingResponse(
        _stream(),
        media_type="application/x-ndjson",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


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

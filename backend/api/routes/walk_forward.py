"""
Walk-Forward 验证 API

将历史数据分成多个滚动窗口，在训练期优化参数，在测试期验证表现。
多个窗口的测试期拼接起来，就是样本外的真实表现。

算法整体下沉到 alpha_lab.walk_forward（域9 厚 route 下沉）：本 route 只做
入参校验 + NDJSON 编码 + 同步生成器→async 流的桥接。
"""
import json
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from alpha_lab.walk_forward import generate_windows, run_walk_forward
from api.routes._stream_utils import bridge_sync_stream

router = APIRouter(prefix="/walk_forward", tags=["Walk-Forward验证"])


# ── Request Model ──

class WalkForwardRequest(BaseModel):
    symbol: str = Field(..., description="股票代码")
    strategy: str = Field(default="MA_CROSS", description="策略名称")
    market: str = Field(default="a_share", description="市场")
    initial_capital: float = Field(default=100000.0)
    train_months: int = Field(default=12, description="训练期长度(月)")
    test_months: int = Field(default=3, description="测试期长度(月)")
    step_months: int = Field(default=3, description="滚动步长(月)")
    start_date: str = Field(..., description="整体起始日期")
    end_date: str = Field(..., description="整体结束日期")
    param_grid: Dict[str, List[Any]] = Field(..., description="参数搜索范围")
    slippage_pct: Optional[float] = Field(default=None, description="自定义滑点")
    risk_config: Optional[Dict[str, Any]] = Field(default=None, description="风控配置")


# ── API Endpoint ──

@router.post("/run")
async def run_walk_forward_endpoint(req: WalkForwardRequest):
    """流式 Walk-Forward 验证 (NDJSON)"""

    windows = generate_windows(
        req.start_date, req.end_date,
        req.train_months, req.test_months, req.step_months,
    )

    if not windows:
        raise HTTPException(status_code=400, detail="无法生成有效的时间窗口，请检查日期范围和训练/测试期长度")

    def _ndjson_stream():
        for event in run_walk_forward(
            symbol=req.symbol,
            strategy=req.strategy,
            market=req.market,
            initial_capital=req.initial_capital,
            param_grid=req.param_grid,
            windows=windows,
            slippage_pct=req.slippage_pct,
            risk_config=req.risk_config,
        ):
            yield json.dumps(event, ensure_ascii=False) + "\n"

    return StreamingResponse(
        bridge_sync_stream(_ndjson_stream()),
        media_type="application/x-ndjson",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )

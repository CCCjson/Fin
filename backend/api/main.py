"""
FastAPI主程序
"""
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from fastapi.middleware.cors import CORSMiddleware
from loguru import logger
import sys
from pathlib import Path

# 添加项目根目录到sys.path
sys.path.insert(0, str(Path(__file__).parent.parent))

from data_engine import init_db
from api.routes import data, analysis, backtest, trading, monitor, history, signal_generation, realtime, report, tracking, portfolio, review, advisor, orderbook, backtest_cpp, pipeline, prediction, news, auth, automation, ws, alpha_lab, fine_tune

# 初始化数据库
init_db()

# 创建FastAPI应用
app = FastAPI(
    title="量化交易系统API",
    description="个人量化交易工具 - 支持A股、港股、美股",
    version="1.4.0"
)

# 配置CORS（本地开发用）
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# 注册路由
app.include_router(data.router)
app.include_router(analysis.router)
app.include_router(backtest.router)
app.include_router(trading.router)
app.include_router(monitor.router)
app.include_router(history.router)
app.include_router(signal_generation.router)
app.include_router(realtime.router)
app.include_router(report.router)
app.include_router(tracking.router)
app.include_router(portfolio.router)
app.include_router(review.router)
app.include_router(advisor.router)
app.include_router(orderbook.router)
app.include_router(backtest_cpp.router)
app.include_router(pipeline.router)
app.include_router(prediction.router)
app.include_router(news.router)
app.include_router(auth.router)
app.include_router(automation.router)
app.include_router(alpha_lab.router)
app.include_router(fine_tune.router)
app.include_router(ws.router)


# 全局异常处理
@app.exception_handler(Exception)
async def global_exception_handler(request: Request, exc: Exception):
    """全局异常处理器"""
    logger.error(f"API错误: {exc}")
    return JSONResponse(
        status_code=500,
        content={
            "error": "Internal Server Error",
            "detail": str(exc),
            "success": False
        }
    )


@app.get("/")
async def root():
    """根路径"""
    return {
        "name": "量化交易系统API",
        "version": "1.4.0",
        "status": "running",
        "docs": "/docs",
        "redoc": "/redoc"
    }


@app.get("/health")
async def health_check():
    """健康检查"""
    return {
        "status": "healthy",
        "message": "API is running"
    }


@app.on_event("startup")
async def startup_event():
    """应用启动事件"""
    logger.info("=" * 80)
    logger.info("量化交易系统API启动")
    logger.info("=" * 80)
    logger.info("API文档: http://127.0.0.1:8000/docs")
    logger.info("=" * 80)

    # pytdx 预热：测速选最优服务器，后续盘中扫描直连不再等待
    try:
        from data_engine.fetchers.pytdx_fetcher import warmup_pytdx
        warmup_pytdx()
    except Exception as e:
        logger.warning(f"pytdx 预热失败（不影响其他功能）: {e}")


@app.on_event("shutdown")
async def shutdown_event():
    """应用关闭事件"""
    logger.info("量化交易系统API关闭")


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(
        "main:app",
        host="127.0.0.1",
        port=8000,
        reload=True,
        log_level="info"
    )

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

# 在任何引擎/transformers import 之前同步代理 env（direct 模式清掉残留死代理，
# 让 HF 模型加载/出网请求在 Clash 关时也能直连）。
from net_proxy import apply_proxy_env
apply_proxy_env()

from data_engine import init_db
from api.routes import data, analysis, backtest, trading, monitor, history, signal_generation, realtime, report, tracking, portfolio, review, advisor, orderbook, backtest_cpp, pipeline, prediction, news, auth, automation, ws, alpha_lab, fine_tune, stock_pools, walk_forward, cockpit, watchlist, screener, agent, knowledge

# 初始化数据库
init_db()

# 初始化外置金融大脑知识库（独立 knowledge.db + vec0 向量表，幂等）
try:
    from knowledge_engine.database import init_knowledge_db
    init_knowledge_db()
except Exception as _e:  # noqa: BLE001 — 知识库初始化失败不应阻断主服务启动
    from loguru import logger as _logger
    _logger.warning(f"知识库 knowledge_engine 初始化失败（不影响主服务）：{_e}")

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
app.include_router(stock_pools.router)
app.include_router(walk_forward.router)
app.include_router(cockpit.router)
app.include_router(watchlist.router)
app.include_router(screener.router)
app.include_router(knowledge.router)
app.include_router(ws.router)
app.include_router(agent.router)


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

    # 启动订单簿 WebSocket 后台推送任务（保存句柄，shutdown 时取消）
    import asyncio
    from api.routes.orderbook import start_bg_task
    start_bg_task()
    logger.info("订单簿 WebSocket 后台推送任务已启动")

    # 启动价格预警常驻监控任务（开机自启，盘中自动检查）
    from automation.price_alert_monitor import price_alert_loop
    asyncio.create_task(price_alert_loop())
    logger.info("价格预警监控任务已启动")

    # 知识库定时摄入论文（双重门控：默认关，需显式开+配 query；只摄入不回测）
    try:
        from knowledge_engine.scheduler import knowledge_scheduler
        knowledge_scheduler.start()
    except Exception as e:
        logger.warning(f"知识库定时摄入启动失败（不影响主服务）: {e}")


@app.on_event("shutdown")
async def shutdown_event():
    """应用关闭事件"""
    # 先取消订单簿后台推送 task，再关 httpx 客户端
    # （顺序很重要：task 若仍在跑，其 get_client() 会重建一个永不关闭的客户端）
    from api.routes.orderbook import stop_bg_task, close_client
    await stop_bg_task()
    await close_client()

    try:
        from knowledge_engine.scheduler import knowledge_scheduler
        knowledge_scheduler.stop()
    except Exception:
        pass

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

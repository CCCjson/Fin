"""
FastAPI主程序
"""
from fastapi import FastAPI, Request, Depends
from fastapi.responses import JSONResponse
from fastapi.middleware.cors import CORSMiddleware
from loguru import logger
import os
import sys
from pathlib import Path

# 添加项目根目录到sys.path
sys.path.insert(0, str(Path(__file__).parent.parent))

# 在任何引擎/transformers import 之前同步代理 env（direct 模式清掉残留死代理，
# 让 HF 模型加载/出网请求在 Clash 关时也能直连）。
from net import apply_proxy_env
apply_proxy_env()

from data_engine import init_db
# 架构收敛（2026-07）：功能类 route 只保留服务于 8 个工作台页面的；
# 信号/复盘/自选/报告/顾问/交易记录等能力经 MoneyBill 工具进程内直调引擎，不再走 HTTP。
from api.routes import data, analysis, monitor, history, realtime, orderbook, backtest_cpp, prediction, news, auth, automation, ws, fine_tune, stock_pools, walk_forward, screener, agent, knowledge, data_monitor, deep_history
from api.deps import require_auth, assert_strong_secret

# 弱密钥启动自检：强制鉴权却仍用默认/弱 JWT_SECRET 时直接抛错退出，
# 别让用户在不知情的情况下带弱密钥跑在局域网/公网。
assert_strong_secret()

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

# 配置 CORS —— 收紧为显式白名单（原 allow_origins=["*"] + allow_credentials=True
# 组合本身矛盾，浏览器会拒绝且等于无防护）。
# 用正则覆盖三类可信来源：
#   1) 本地 vite dev server / 后端本机（localhost、127.0.0.1，任意端口，含 5174）
#   2) 局域网内手机访问 dev server（10.x / 192.168.x / 172.16-31.x 私网段，vite host:true）
#   3) Tauri v2 webview：macOS/iOS/Linux 为 tauri://localhost，Windows 为 http://tauri.localhost
_CORS_ORIGIN_REGEX = (
    r"^("
    r"https?://(localhost|127\.0\.0\.1)(:\d+)?"
    r"|https?://(10(\.\d{1,3}){3}|192\.168(\.\d{1,3}){2}|172\.(1[6-9]|2\d|3[01])(\.\d{1,3}){2})(:\d+)?"
    r"|tauri://localhost"
    r"|http://tauri\.localhost"
    r")$"
)
app.add_middleware(
    CORSMiddleware,
    allow_origin_regex=_CORS_ORIGIN_REGEX,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# 注册路由 —— 除 auth（登录本身）与 ws（WebSocket 走查询参数手动校验）外，
# 所有业务 router 统一挂 require_auth 依赖，强制 JWT 鉴权。
# 注：orderbook 含 WebSocket 子路由，require_auth 已兼容 ?token= 查询参数，
# 其 WS 也一并受保护（前端 WS URL 需带 token）。
_auth = [Depends(require_auth)]
app.include_router(data.router, dependencies=_auth)
app.include_router(analysis.router, dependencies=_auth)
app.include_router(monitor.router, dependencies=_auth)
app.include_router(history.router, dependencies=_auth)
app.include_router(realtime.router, dependencies=_auth)
app.include_router(orderbook.router, dependencies=_auth)
app.include_router(backtest_cpp.router, dependencies=_auth)
app.include_router(prediction.router, dependencies=_auth)
app.include_router(news.router, dependencies=_auth)
app.include_router(auth.router)  # 登录/校验端点本身必须公开
app.include_router(automation.router, dependencies=_auth)
app.include_router(fine_tune.router, dependencies=_auth)
app.include_router(stock_pools.router, dependencies=_auth)
app.include_router(walk_forward.router, dependencies=_auth)
app.include_router(screener.router, dependencies=_auth)
app.include_router(knowledge.router, dependencies=_auth)
app.include_router(ws.router)  # WebSocket：在端点内手动校验 ?token=（close 1008）
app.include_router(agent.router, dependencies=_auth)
app.include_router(data_monitor.router, dependencies=_auth)
app.include_router(deep_history.router, dependencies=_auth)


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
    # 内存泄漏追踪器（默认关闭；FIN_MEMTRACE=1 才启动，零成本）。放最前面尽早取基线。
    try:
        from memtrace import maybe_start_memtrace
        maybe_start_memtrace()
    except Exception as e:  # noqa: BLE001
        logger.warning(f"memtrace 启动失败（不影响主服务）: {e}")

    logger.info("=" * 80)
    logger.info("量化交易系统API启动")
    logger.info("=" * 80)
    logger.info("API文档: http://127.0.0.1:8000/docs")
    logger.info("=" * 80)

    # pytdx 预热：测速选最优服务器，后续盘中扫描直连不再等待
    try:
        from acquisition.markets.pytdx_fetcher import warmup_pytdx
        warmup_pytdx()
    except Exception as e:
        logger.warning(f"pytdx 预热失败（不影响其他功能）: {e}")

    # 启动订单簿 WebSocket 后台推送任务（保存句柄，shutdown 时取消）
    import asyncio
    from api.routes.orderbook import start_bg_task
    start_bg_task()
    logger.info("订单簿 WebSocket 后台推送任务已启动")

    # 价格预警 / 持仓守护 / 涨停扫描 **不再开机常驻自动扫**（Jason 2026-07-10 拍板）。
    # 这三个盘中轮询没有独立开关、连 FIN_DISABLE_SCHEDULERS 都管不到，白烧快代理额度。
    # 改成 MoneyBill 需要时才调工具现拉：
    #   check_price_alerts   → price_alert_monitor._scan_once()
    #   get_position_guard   → position_guardian.build_guard_status()
    #   get_limit_up_pool    → limit_up_scanner.get_intraday_snapshot()（缓存过期即懒扫）

    # 多实例部署（app 稳定后端 + web 开发后端各跑一份）时，web 端应关闭定时任务，
    # 避免两边重复拉数据/重复扫描新闻。FIN_DISABLE_SCHEDULERS=true 由 restart.sh 注入。
    schedulers_enabled = os.getenv("FIN_DISABLE_SCHEDULERS", "false").lower() not in ("1", "true", "yes")
    if not schedulers_enabled:
        logger.info("FIN_DISABLE_SCHEDULERS 已启用，跳过知识库/每日数据/新闻三个定时任务")

    # 知识库定时摄入论文（双重门控：默认关，需显式开+配 query；只摄入不回测）
    if schedulers_enabled:
        try:
            from knowledge_engine.scheduler import knowledge_scheduler
            knowledge_scheduler.start()
        except Exception as e:
            logger.warning(f"知识库定时摄入启动失败（不影响主服务）: {e}")

    # 每日数据更新链定时任务（开机自启，收盘后自动 更新日线→补信号→更新追踪）
    if schedulers_enabled:
        try:
            from data_engine.daily_pipeline_scheduler import daily_pipeline_scheduler
            daily_pipeline_scheduler.start()
        except Exception as e:
            logger.warning(f"每日数据更新定时任务启动失败（不影响主服务）: {e}")

    # Newnew 新闻定时任务（开机自启，默认每 15 分钟抓取国内外新闻+分析，每周清一次去重缓存）
    if schedulers_enabled:
        try:
            from news_engine.news_scheduler import news_scheduler
            news_scheduler.start()
        except Exception as e:
            logger.warning(f"新闻定时任务启动失败（不影响主服务）: {e}")

    # 业务事件总线 → WebSocket 桥接：把 BizEvent 广播到前端活动流（复用 /ws/automation）
    try:
        loop = asyncio.get_running_loop()
        from business_events import BUS
        from automation.websocket_manager import ws_manager

        def _push_biz_event(ev: dict):
            # 生产者可能在工作线程里 publish，用 run_coroutine_threadsafe 跨线程投递到主循环
            try:
                asyncio.run_coroutine_threadsafe(
                    ws_manager.broadcast({"type": "business_event", "data": ev}), loop)
            except Exception:  # noqa: BLE001
                pass

        BUS.subscribe(_push_biz_event)
        logger.info("业务事件总线已接入 WebSocket 推送")
    except Exception as e:
        logger.warning(f"业务事件总线 WS 桥接失败（不影响主服务）: {e}")


@app.on_event("shutdown")
async def shutdown_event():
    """应用关闭事件"""
    # 先取消订单簿后台推送 task，再关 httpx 客户端
    # （顺序很重要：task 若仍在跑，其 get_client() 会重建一个永不关闭的客户端）
    from api.routes.orderbook import stop_bg_task, close_client
    await stop_bg_task()
    await close_client()

    schedulers_enabled = os.getenv("FIN_DISABLE_SCHEDULERS", "false").lower() not in ("1", "true", "yes")

    if schedulers_enabled:
        try:
            from knowledge_engine.scheduler import knowledge_scheduler
            knowledge_scheduler.stop()
        except Exception:
            pass

        try:
            from data_engine.daily_pipeline_scheduler import daily_pipeline_scheduler
            daily_pipeline_scheduler.stop()
        except Exception:
            pass

        try:
            from news_engine.news_scheduler import news_scheduler
            news_scheduler.stop()
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

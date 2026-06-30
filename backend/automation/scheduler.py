"""
自动化调度引擎 — APScheduler 定时扫描

三种任务：
1. 日线扫描 (CronTrigger 15:30 周一到周五)
2. 盘中扫描 (IntervalTrigger 每N分钟，仅交易时间)
3. 持仓检查 (IntervalTrigger 每5分钟，盘中)
"""
import json
import asyncio
import time
from datetime import datetime, timedelta
from typing import Dict, Any, List, Optional

from loguru import logger

from data_engine.storage.database import get_session
from data_engine.storage.models import AutomationConfig, AutomationLog
from automation.pending_order_manager import PendingOrderManager


def _is_trading_hours() -> bool:
    """判断当前是否在 A 股交易时间"""
    now = datetime.now()
    # 周末不交易
    if now.weekday() >= 5:
        return False
    t = now.time()
    morning = (t >= datetime.strptime("09:30", "%H:%M").time() and
               t <= datetime.strptime("11:30", "%H:%M").time())
    afternoon = (t >= datetime.strptime("13:00", "%H:%M").time() and
                 t <= datetime.strptime("15:00", "%H:%M").time())
    return morning or afternoon


class AutomationScheduler:
    """自动化调度器"""

    def __init__(self):
        self._scheduler = None
        self._running = False
        self._order_manager = PendingOrderManager()

    @property
    def is_running(self) -> bool:
        return self._running

    def _get_scheduler(self):
        """懒加载 APScheduler"""
        if self._scheduler is None:
            from apscheduler.schedulers.asyncio import AsyncIOScheduler
            from apscheduler.triggers.cron import CronTrigger
            from apscheduler.triggers.interval import IntervalTrigger
            import pytz

            tz = pytz.timezone("Asia/Shanghai")
            self._scheduler = AsyncIOScheduler(timezone=tz)

            # 过期订单清理：每 5 分钟
            self._scheduler.add_job(
                self._expire_orders_job,
                IntervalTrigger(minutes=5),
                id="expire_orders",
                name="清理过期订单",
                replace_existing=True,
            )

        return self._scheduler

    def start(self):
        """启动调度器并加载配置"""
        if self._running:
            logger.warning("调度器已在运行")
            return

        scheduler = self._get_scheduler()
        self._load_configs()
        scheduler.start()
        self._running = True
        logger.info("自动化调度器已启动")

    def stop(self):
        """停止调度器"""
        if not self._running:
            return

        if self._scheduler:
            self._scheduler.shutdown(wait=False)
            self._scheduler = None

        self._running = False
        logger.info("自动化调度器已停止")

    def _load_configs(self):
        """从数据库加载启用的配置，注册定时任务"""
        from apscheduler.triggers.cron import CronTrigger
        from apscheduler.triggers.interval import IntervalTrigger

        session = get_session()
        try:
            configs = session.query(AutomationConfig).filter(AutomationConfig.enabled == 1).all()
            scheduler = self._get_scheduler()

            for config in configs:
                job_id = f"scan_{config.config_id}"

                if config.scan_type == "daily":
                    # 每日 15:30 扫描
                    trigger = CronTrigger(
                        hour=15, minute=30,
                        day_of_week="mon-fri",
                    )
                    scheduler.add_job(
                        self._daily_scan_job,
                        trigger,
                        id=job_id,
                        name=f"日线扫描: {config.name}",
                        args=[config.config_id],
                        replace_existing=True,
                    )
                elif config.scan_type == "intraday":
                    # 盘中每 N 分钟扫描
                    trigger = IntervalTrigger(minutes=config.frequency_minutes or 5)
                    scheduler.add_job(
                        self._intraday_scan_job,
                        trigger,
                        id=job_id,
                        name=f"盘中扫描: {config.name}",
                        args=[config.config_id],
                        replace_existing=True,
                    )
                elif config.scan_type == "position_check":
                    # 持仓检查每 5 分钟
                    trigger = IntervalTrigger(minutes=config.frequency_minutes or 5)
                    scheduler.add_job(
                        self._position_check_job,
                        trigger,
                        id=job_id,
                        name=f"持仓检查: {config.name}",
                        args=[config.config_id],
                        replace_existing=True,
                    )

                logger.info(f"注册任务: {job_id} ({config.scan_type})")

        except Exception as e:
            logger.error(f"加载配置失败: {e}")
        finally:
            session.close()

    def reload_configs(self):
        """重新加载配置"""
        if not self._running:
            return
        scheduler = self._get_scheduler()
        # 移除旧任务（保留 expire_orders）
        for job in scheduler.get_jobs():
            if job.id.startswith("scan_"):
                scheduler.remove_job(job.id)
        self._load_configs()
        logger.info("配置已重新加载")

    def get_jobs_info(self) -> List[Dict]:
        """获取当前任务列表"""
        if not self._scheduler:
            return []

        jobs = []
        for job in self._scheduler.get_jobs():
            jobs.append({
                "id": job.id,
                "name": job.name,
                "next_run": job.next_run_time.isoformat() if job.next_run_time else None,
            })
        return jobs

    async def trigger_scan(self, config_id: str) -> Dict:
        """手动触发一次扫描"""
        session = get_session()
        try:
            config = session.query(AutomationConfig).filter(
                AutomationConfig.config_id == config_id
            ).first()
            if not config:
                raise ValueError(f"配置 {config_id} 不存在")

            if config.scan_type == "daily":
                return await self._daily_scan_job(config_id)
            elif config.scan_type == "intraday":
                return await self._intraday_scan_job(config_id)
            elif config.scan_type == "position_check":
                return await self._position_check_job(config_id)
            else:
                raise ValueError(f"未知扫描类型: {config.scan_type}")
        finally:
            session.close()

    # -------- 扫描任务实现 --------

    async def _daily_scan_job(self, config_id: str) -> Dict:
        """日线扫描任务"""
        start_time = time.time()
        session = get_session()
        log = None

        try:
            config = session.query(AutomationConfig).filter(
                AutomationConfig.config_id == config_id
            ).first()
            if not config:
                logger.warning(f"配置不存在: {config_id}")
                return {"error": "配置不存在"}

            # 解析配置
            strategies = json.loads(config.strategies) if config.strategies else []
            watchlist = json.loads(config.watchlist) if config.watchlist else []
            min_strength = config.min_strength or 0.6

            # 记录日志
            log = AutomationLog(
                config_id=config_id,
                run_type="daily",
                status="running",
                started_at=datetime.now(),
            )
            session.add(log)
            session.commit()

            # WS 通知开始
            try:
                from automation.websocket_manager import notify_scan_started
                asyncio.ensure_future(notify_scan_started(config_id, "daily", len(watchlist)))
            except Exception:
                pass

            # 执行信号扫描
            from strategy.signal_generator import SignalGenerator
            generator = SignalGenerator()

            scan_symbols = watchlist if watchlist else None
            result = generator.scan_market(
                symbols=scan_symbols,
                lookback_days=60,
                save_to_db=True,
                limit=100,
            )

            symbols_scanned = result.get("symbols_scanned", 0)
            total_signals = result.get("total_signals", 0)

            # 过滤信号并创建订单
            orders_created = 0
            if total_signals > 0:
                orders_created = await self._create_orders_from_scan(
                    config, result, min_strength, session
                )

            # 更新日志
            duration = time.time() - start_time
            log.status = "completed"
            log.symbols_scanned = symbols_scanned
            log.signals_found = total_signals
            log.orders_created = orders_created
            log.duration_seconds = round(duration, 2)
            log.completed_at = datetime.now()

            config.last_run_at = datetime.now()
            session.commit()

            scan_result = {
                "symbols_scanned": symbols_scanned,
                "signals_found": total_signals,
                "orders_created": orders_created,
                "duration_seconds": round(duration, 2),
            }

            # WS 通知完成
            try:
                from automation.websocket_manager import notify_scan_completed
                asyncio.ensure_future(notify_scan_completed(config_id, "daily", scan_result))
            except Exception:
                pass

            logger.info(f"日线扫描完成: 扫描 {symbols_scanned} 只，信号 {total_signals} 个，订单 {orders_created} 个，耗时 {duration:.1f}s")
            return scan_result

        except Exception as e:
            if log:
                log.status = "failed"
                log.error_message = str(e)
                log.completed_at = datetime.now()
                log.duration_seconds = round(time.time() - start_time, 2)
                session.commit()
            logger.error(f"日线扫描失败: {e}")
            return {"error": str(e)}
        finally:
            session.close()

    async def _intraday_scan_job(self, config_id: str) -> Dict:
        """盘中扫描任务"""
        if not _is_trading_hours():
            return {"skipped": True, "reason": "非交易时间"}

        start_time = time.time()
        session = get_session()
        log = None

        try:
            config = session.query(AutomationConfig).filter(
                AutomationConfig.config_id == config_id
            ).first()
            if not config:
                return {"error": "配置不存在"}

            watchlist = json.loads(config.watchlist) if config.watchlist else []
            min_strength = config.min_strength or 0.6

            if not watchlist:
                return {"skipped": True, "reason": "监控列表为空"}

            log = AutomationLog(
                config_id=config_id,
                run_type="intraday",
                status="running",
                started_at=datetime.now(),
            )
            session.add(log)
            session.commit()

            # 使用 pytdx 分钟线数据进行盘中扫描
            from strategy.signal_generator import SignalGenerator
            generator = SignalGenerator()

            result = generator.scan_intraday(
                symbols=watchlist,
                bar_count=240,
                period=1,
                save_to_db=True,
            )

            symbols_scanned = result.get("symbols_scanned", 0)
            total_signals = result.get("total_signals", 0)

            orders_created = 0
            if total_signals > 0:
                orders_created = await self._create_orders_from_scan(
                    config, result, min_strength, session
                )

            duration = time.time() - start_time
            log.status = "completed"
            log.symbols_scanned = symbols_scanned
            log.signals_found = total_signals
            log.orders_created = orders_created
            log.duration_seconds = round(duration, 2)
            log.completed_at = datetime.now()
            config.last_run_at = datetime.now()
            session.commit()

            return {
                "symbols_scanned": symbols_scanned,
                "signals_found": total_signals,
                "orders_created": orders_created,
            }

        except Exception as e:
            if log:
                log.status = "failed"
                log.error_message = str(e)
                log.completed_at = datetime.now()
                session.commit()
            logger.error(f"盘中扫描失败: {e}")
            return {"error": str(e)}
        finally:
            session.close()

    async def _position_check_job(self, config_id: str) -> Dict:
        """持仓止损止盈检查"""
        if not _is_trading_hours():
            return {"skipped": True, "reason": "非交易时间"}

        start_time = time.time()
        session = get_session()

        try:
            config = session.query(AutomationConfig).filter(
                AutomationConfig.config_id == config_id
            ).first()
            if not config:
                return {"error": "配置不存在"}

            from portfolio.calculator import PortfolioCalculator
            from trading_engine.risk.manager import RiskManager
            from trading_engine.risk.adapter import get_effective_risk_config

            calculator = PortfolioCalculator()
            positions = calculator.get_current_positions()
            risk_manager = RiskManager(get_effective_risk_config())

            orders_created = 0
            alerts = []

            for pos in positions:
                symbol = pos["symbol"]
                avg_cost = pos.get("avg_cost", 0)
                current_price = pos.get("current_price")
                quantity = pos.get("quantity", 0)

                if not current_price or current_price <= 0 or quantity <= 0:
                    continue

                # 风控检查
                should_close, results = risk_manager.check_position(
                    symbol=symbol,
                    position_info=pos,
                    current_price=current_price,
                )

                if should_close:
                    alert_reasons = [r.message for r in results if not r.passed]
                    logger.warning(f"持仓风控告警: {symbol} | {', '.join(alert_reasons)}")

                    # 创建卖出订单
                    signal = {
                        "symbol": symbol,
                        "signal_type": "SELL",
                        "strength": 0.9,
                        "strategy": "position_check",
                        "price": current_price,
                        "reasons": [{"indicator": "RiskManager", "detail": r} for r in alert_reasons],
                    }
                    order = self._order_manager.create_from_signal(
                        signal=signal,
                        scan_source="position_check",
                        broker_type=config.broker_type or "paper",
                        expire_minutes=config.order_expire_minutes or 15,
                    )

                    if order:
                        orders_created += 1
                        alerts.append({"symbol": symbol, "reasons": alert_reasons})

                        # WS 推送
                        try:
                            from automation.websocket_manager import notify_pending_order, notify_risk_alert
                            asyncio.ensure_future(notify_pending_order(order))
                            asyncio.ensure_future(notify_risk_alert(
                                symbol, "stop_loss_triggered",
                                f"{symbol} 触发止损/止盈", {"reasons": alert_reasons}
                            ))
                        except Exception:
                            pass

            duration = time.time() - start_time
            logger.info(f"持仓检查完成: {len(positions)} 只持仓，创建 {orders_created} 个卖出订单，耗时 {duration:.1f}s")

            return {
                "positions_checked": len(positions),
                "orders_created": orders_created,
                "alerts": alerts,
            }

        except Exception as e:
            logger.error(f"持仓检查失败: {e}")
            return {"error": str(e)}
        finally:
            session.close()

    async def _expire_orders_job(self):
        """清理过期订单"""
        count = self._order_manager.expire_orders()
        if count > 0:
            try:
                from automation.websocket_manager import ws_manager
                await ws_manager.broadcast({
                    "type": "orders_expired",
                    "data": {"count": count},
                })
            except Exception:
                pass

    async def _create_orders_from_scan(
        self,
        config: AutomationConfig,
        scan_result: Dict,
        min_strength: float,
        session,
    ) -> int:
        """从扫描结果中创建待确认订单"""
        orders_created = 0

        # scan_result 中的 results 是按 symbol 分的统计，
        # 需要从数据库获取最新信号
        from data_engine.storage.models import Signal
        from sqlalchemy import desc

        for symbol, info in scan_result.get("results", {}).items():
            if info.get("count", 0) == 0:
                continue

            # 获取该股票最新的信号（今天的）
            signals = (
                session.query(Signal)
                .filter(Signal.symbol == symbol)
                .order_by(desc(Signal.created_at))
                .limit(5)
                .all()
            )

            for sig in signals:
                if sig.strength and sig.strength < min_strength:
                    continue

                # 解析 reasons
                reasons = []
                if sig.reasons:
                    try:
                        reasons = json.loads(sig.reasons)
                    except (json.JSONDecodeError, TypeError):
                        reasons = [{"detail": sig.reasons}]

                signal_dict = {
                    "symbol": sig.symbol,
                    "signal_type": sig.signal_type,
                    "strength": sig.strength or 0.5,
                    "strategy": sig.strategy or "",
                    "price": sig.price or sig.entry_price or 0,
                    "stop_loss": sig.stop_loss,
                    "take_profit": sig.take_profit,
                    "reasons": reasons,
                }

                order = self._order_manager.create_from_signal(
                    signal=signal_dict,
                    scan_source=config.scan_type,
                    broker_type=config.broker_type or "paper",
                    expire_minutes=config.order_expire_minutes or 30,
                    position_size_pct=config.position_size_pct or 0.10,
                )

                if order:
                    orders_created += 1
                    # WS 推送
                    try:
                        from automation.websocket_manager import notify_pending_order
                        asyncio.ensure_future(notify_pending_order(order))
                    except Exception:
                        pass

        return orders_created


# 全局调度器实例
automation_scheduler = AutomationScheduler()

"""
自动做市商 Bot

在 Python 层实现，作为"外部客户端"通过 HTTP API 与 C++ 撮合引擎交互。
策略：围绕中间价持续挂买卖单，提供流动性，支持库存偏斜和随机波动。
"""
import asyncio
import random
import math
from dataclasses import dataclass, field, asdict
from typing import Dict, List, Optional
from datetime import datetime

import httpx
from loguru import logger


@dataclass
class MarketMakerConfig:
    """做市商配置（全部可调）"""
    enabled: bool = False
    interval_ms: int = 300          # 报价刷新周期(ms)
    spread_ticks: int = 2           # 目标半价差(tick数)
    levels: int = 3                 # 每侧挂几档
    base_quantity: int = 500        # 第一档数量
    quantity_decay: float = 0.6     # 后续档位数量衰减系数
    max_position: int = 10000       # 最大净持仓
    skew_factor: float = 0.001      # 库存偏斜系数
    price_drift: float = 0.0        # 价格漂移(每周期)
    volatility: float = 0.0005      # 随机波动幅度(标准差)
    tick_size: float = 0.01         # 最小价格变动


@dataclass
class MarketMakerStatus:
    """做市商运行状态"""
    running: bool = False
    net_position: int = 0           # 净持仓(正=多头, 负=空头)
    active_bid_orders: int = 0      # 当前买方挂单数
    active_ask_orders: int = 0      # 当前卖方挂单数
    total_trades: int = 0           # 总成交笔数
    uptime_seconds: float = 0.0     # 运行时长
    target_mid: float = 0.0         # 当前目标中间价
    pnl: float = 0.0               # 估算盈亏


class MarketMakerBot:
    """单个 session 的做市商 Bot"""

    CPP_URL = "http://localhost:8001"

    def __init__(self, session_id: str, config: MarketMakerConfig):
        self.session_id = session_id
        self.config = config
        self._task: Optional[asyncio.Task] = None
        self._client: Optional[httpx.AsyncClient] = None

        # 状态
        self.net_position: int = 0
        self.total_trades: int = 0
        self.target_mid: float = 0.0
        self.pnl: float = 0.0
        self._start_time: Optional[datetime] = None

        # 当前挂单跟踪
        self._bid_orders: List[str] = []   # order_id 列表
        self._ask_orders: List[str] = []

    async def start(self):
        """启动做市商"""
        if self._task and not self._task.done():
            return

        self._client = httpx.AsyncClient(
            base_url=self.CPP_URL,
            timeout=5.0,
            limits=httpx.Limits(max_connections=10, max_keepalive_connections=5),
            # 🔴 `trust_env=False`：不读 .env 里的 HTTP_PROXY。
            # 本机 8001 走代理是纯粹的错误 —— Clash 没开时会报
            # `ProxyError`，看上去完全不像订单簿服务的问题。
            # ⛔ 别当样板删掉（有门禁 tests/test_localhost_bypasses_proxy.py）。
            trust_env=False,
        )
        self._start_time = datetime.now()
        self.config.enabled = True
        self._task = asyncio.create_task(self._run_loop())
        logger.info(f"做市商启动: session={self.session_id[:12]}...")

    async def stop(self):
        """停止做市商并撤销所有挂单"""
        self.config.enabled = False
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None

        # 撤销所有挂单
        await self._cancel_all_orders()

        if self._client:
            await self._client.aclose()
            self._client = None

        logger.info(f"做市商停止: session={self.session_id[:12]}..., "
                     f"净持仓={self.net_position}")

    def get_status(self) -> MarketMakerStatus:
        """获取当前状态"""
        uptime = 0.0
        if self._start_time and self.config.enabled:
            uptime = (datetime.now() - self._start_time).total_seconds()

        return MarketMakerStatus(
            running=self.config.enabled and self._task is not None and not self._task.done(),
            net_position=self.net_position,
            active_bid_orders=len(self._bid_orders),
            active_ask_orders=len(self._ask_orders),
            total_trades=self.total_trades,
            uptime_seconds=uptime,
            target_mid=self.target_mid,
            pnl=self.pnl,
        )

    def update_config(self, new_config: dict):
        """运行中更新参数"""
        for key, val in new_config.items():
            if hasattr(self.config, key) and key != "enabled":
                setattr(self.config, key, val)

    async def _run_loop(self):
        """做市主循环"""
        try:
            # 先获取当前中间价
            stats = await self._get_stats()
            if stats and stats.get("mid_price", 0) > 0:
                self.target_mid = stats["mid_price"]
            else:
                logger.warning("做市商无法获取初始中间价，使用 0")
                return

            while self.config.enabled:
                try:
                    await self._quote_cycle()
                except httpx.ConnectError:
                    logger.warning("做市商: C++ 服务不可达，等待重连...")
                    await asyncio.sleep(2)
                    continue
                except Exception as e:
                    logger.error(f"做市商周期异常: {e}")

                await asyncio.sleep(self.config.interval_ms / 1000.0)

        except asyncio.CancelledError:
            pass

    async def _quote_cycle(self):
        """单个报价周期"""
        cfg = self.config

        # 1. 价格漂移 + 随机波动
        drift = cfg.price_drift * cfg.tick_size
        noise = random.gauss(0, cfg.volatility * self.target_mid) if cfg.volatility > 0 else 0
        self.target_mid += drift + noise
        # 对齐到 tick
        self.target_mid = round(self.target_mid / cfg.tick_size) * cfg.tick_size

        # 2. 库存偏斜
        skew = cfg.skew_factor * self.net_position * cfg.tick_size
        adjusted_mid = self.target_mid - skew

        # 3. 计算目标报价
        half_spread = cfg.spread_ticks * cfg.tick_size

        new_bids = []
        new_asks = []

        for level in range(cfg.levels):
            offset = half_spread + level * cfg.tick_size
            qty = max(1, int(cfg.base_quantity * (cfg.quantity_decay ** level)))

            bid_price = round((adjusted_mid - offset) / cfg.tick_size) * cfg.tick_size
            ask_price = round((adjusted_mid + offset) / cfg.tick_size) * cfg.tick_size

            # 风控：净持仓检查
            if self.net_position < cfg.max_position:
                new_bids.append((bid_price, qty))
            if self.net_position > -cfg.max_position:
                new_asks.append((ask_price, qty))

        # 4. 撤旧挂新
        await self._cancel_all_orders()

        # 5. 挂新单
        self._bid_orders = []
        self._ask_orders = []

        for bid_price, qty in new_bids:
            result = await self._submit_order("BUY", bid_price, qty)
            if result:
                order_id = result.get("order_id", "")
                if order_id:
                    # 检查是否立即成交了
                    filled = result.get("filled_quantity", 0)
                    if filled > 0:
                        self.net_position += filled
                        self.total_trades += len(result.get("fills", []))
                        self._update_pnl(result.get("fills", []), "BUY")
                    if result.get("is_resting"):
                        self._bid_orders.append(order_id)

        for ask_price, qty in new_asks:
            result = await self._submit_order("SELL", ask_price, qty)
            if result:
                order_id = result.get("order_id", "")
                if order_id:
                    filled = result.get("filled_quantity", 0)
                    if filled > 0:
                        self.net_position -= filled
                        self.total_trades += len(result.get("fills", []))
                        self._update_pnl(result.get("fills", []), "SELL")
                    if result.get("is_resting"):
                        self._ask_orders.append(order_id)

    def _update_pnl(self, fills: list, side: str):
        """更新估算 PnL（简化版：基于成交价与目标中间价的差距）"""
        for fill in fills:
            price = fill.get("price", 0)
            qty = fill.get("quantity", 0)
            if side == "BUY":
                # 买入低于中间价 = 赚
                self.pnl += (self.target_mid - price) * qty
            else:
                # 卖出高于中间价 = 赚
                self.pnl += (price - self.target_mid) * qty

    async def _get_stats(self) -> Optional[dict]:
        """获取盘口统计"""
        try:
            resp = await self._client.get(f"/api/sessions/{self.session_id}/stats")
            if resp.status_code == 200:
                return resp.json()
        except Exception:
            pass
        return None

    async def _submit_order(self, side: str, price: float, quantity: int) -> Optional[dict]:
        """提交限价单"""
        try:
            resp = await self._client.post(
                f"/api/sessions/{self.session_id}/orders",
                json={
                    "side": side,
                    "order_type": "LIMIT",
                    "price": price,
                    "quantity": quantity,
                    "client_tag": "mm",
                },
            )
            if resp.status_code == 200:
                return resp.json()
        except Exception as e:
            logger.debug(f"做市商下单失败: {e}")
        return None

    async def _cancel_order(self, order_id: str) -> bool:
        """撤销单个订单"""
        try:
            resp = await self._client.delete(
                f"/api/sessions/{self.session_id}/orders/{order_id}"
            )
            return resp.status_code == 200
        except Exception:
            return False

    async def _cancel_all_orders(self):
        """撤销所有做市商挂单"""
        tasks = []
        for oid in self._bid_orders + self._ask_orders:
            tasks.append(self._cancel_order(oid))
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        self._bid_orders = []
        self._ask_orders = []


class MarketMakerManager:
    """管理所有 session 的做市商"""

    def __init__(self):
        self._bots: Dict[str, MarketMakerBot] = {}

    async def start_bot(self, session_id: str, config: MarketMakerConfig) -> MarketMakerStatus:
        """启动指定 session 的做市商"""
        # 如果已有旧 bot，先停
        if session_id in self._bots:
            await self._bots[session_id].stop()

        bot = MarketMakerBot(session_id, config)
        self._bots[session_id] = bot
        await bot.start()
        return bot.get_status()

    async def stop_bot(self, session_id: str) -> Optional[MarketMakerStatus]:
        """停止指定 session 的做市商"""
        bot = self._bots.get(session_id)
        if not bot:
            return None
        await bot.stop()
        status = bot.get_status()
        del self._bots[session_id]
        return status

    def get_status(self, session_id: str) -> Optional[MarketMakerStatus]:
        """获取做市商状态"""
        bot = self._bots.get(session_id)
        if not bot:
            return None
        return bot.get_status()

    def update_config(self, session_id: str, new_config: dict) -> bool:
        """更新做市商配置"""
        bot = self._bots.get(session_id)
        if not bot:
            return False
        bot.update_config(new_config)
        return True

    def get_config(self, session_id: str) -> Optional[dict]:
        """获取做市商当前配置"""
        bot = self._bots.get(session_id)
        if not bot:
            return None
        return asdict(bot.config)


# 全局实例
mm_manager = MarketMakerManager()

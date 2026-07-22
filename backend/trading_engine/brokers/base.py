"""
券商接口基类
"""
from abc import ABC, abstractmethod
from enum import Enum
from dataclasses import dataclass
from datetime import datetime
from typing import List, Optional


class OrderStatus(Enum):
    """订单状态"""
    PENDING = "PENDING"              # 待提交
    SUBMITTED = "SUBMITTED"          # 已提交
    PARTIAL_FILLED = "PARTIAL_FILLED"  # 部分成交
    FILLED = "FILLED"                # 完全成交
    CANCELLED = "CANCELLED"          # 已撤销
    REJECTED = "REJECTED"            # 已拒绝
    FAILED = "FAILED"                # 失败（确认没成交）
    UNKNOWN = "UNKNOWN"              # ⚠️ 状态未知：请求发出后失联且回查未果，见下方说明


# `UNKNOWN` 与 `FAILED` 的区别是**能不能安全重下**：
#   FAILED  = 已确认交易所没受理 → 可以重下。
#   UNKNOWN = 请求已发出但结果不明（超时/断连，且按 clientOrderId 回查也没拿到答案）→
#             **绝不可重下**，必须人工去交易所核对。上层要把它单独归到「待人工对账」桶，
#             不能当失败处理（当失败会触发重排 → 同一笔成交两次）。
#             目前只有币安实盘路径会产生此状态，A 股/paper 永不产生。


@dataclass
class BrokerOrder:
    """券商订单"""
    order_id: str                    # 订单ID
    symbol: str                      # 股票代码
    action: str                      # 动作 (BUY/SELL)
    quantity: int                    # 数量
    price: Optional[float] = None    # 价格（None=市价单）
    filled_quantity: int = 0         # 已成交数量
    filled_price: float = 0.0        # 成交均价
    status: OrderStatus = OrderStatus.PENDING
    submit_time: Optional[datetime] = None
    filled_time: Optional[datetime] = None
    commission: float = 0.0          # 手续费
    error_msg: Optional[str] = None  # 错误信息


@dataclass
class BrokerPosition:
    """券商持仓。

    ## 字段语义（A 股/paper 与 crypto 统一）

    - `quantity`：**总敞口**。A 股=持仓股数；crypto=现货+活期理财+资金三钱包合计（含理财
      敞口才是真实占比，喂风控/占比计算）。
    - `available`：**可直接交易量**。A 股=可用股数（T+1）；crypto=现货 free（能立刻卖的部分，
      理财/资金里的需先赎回/划转，见 `wallet_breakdown`）。注解放宽为 float 以容纳 crypto 小数币量。
    - `wallet_breakdown`：仅 crypto 用。`{"spot": q, "spot_locked": q, "earn_flexible": q,
      "funding": q}`（只放非零项），卖出时据此算「需从理财赎回/从资金划转多少」。A 股/paper 留 None。
      ⚠️ `spot_locked` 是 `spot` 的**子集**（挂单锁定量，不可直接卖、也无法靠赎回/划转变出来），
      算总敞口时**不要**把它再加一遍。`available` 恒等于 `spot - spot_locked`。
    """
    symbol: str              # 股票代码 / 交易对
    quantity: float          # 总敞口（见类 docstring）
    avg_cost: float          # 平均成本
    current_price: float     # 当前价格
    market_value: float      # 市值
    unrealized_pnl: float    # 未实现盈亏
    available: float         # 可直接交易量（见类 docstring）
    wallet_breakdown: Optional[dict[str, float]] = None  # crypto 分钱包明细（只放非零项）

    @property
    def unrealized_pnl_pct(self) -> float:
        """未实现盈亏百分比"""
        cost_basis = self.quantity * self.avg_cost
        if cost_basis == 0:
            return 0.0
        return (self.unrealized_pnl / cost_basis) * 100

    @property
    def available_quantity(self) -> int:
        """可用数量（别名）"""
        return self.available


class BaseBroker(ABC):
    """券商接口基类"""

    def __init__(self, name: str):
        self.name = name

    @abstractmethod
    def get_account_info(self) -> dict:
        """
        获取账户信息

        Returns:
            包含现金、总资产等信息的字典
        """
        pass

    @abstractmethod
    def get_positions(self) -> List[BrokerPosition]:
        """获取持仓列表"""
        pass

    @abstractmethod
    def get_position(self, symbol: str) -> Optional[BrokerPosition]:
        """获取指定股票的持仓"""
        pass

    @abstractmethod
    def submit_order(self, symbol: str, action: str, quantity: int, price: Optional[float] = None) -> BrokerOrder:
        """
        提交订单

        Args:
            symbol: 股票代码
            action: BUY 或 SELL
            quantity: 数量
            price: 价格（None=市价单）

        Returns:
            订单对象
        """
        pass

    @abstractmethod
    def cancel_order(self, order_id: str) -> bool:
        """撤销订单"""
        pass

    @abstractmethod
    def get_order(self, order_id: str) -> Optional[BrokerOrder]:
        """查询订单"""
        pass

    @abstractmethod
    def get_orders(self, symbol: Optional[str] = None) -> List[BrokerOrder]:
        """获取订单列表"""
        pass

    @abstractmethod
    def get_current_price(self, symbol: str) -> float:
        """获取当前价格"""
        pass

    def __repr__(self):
        return f"{self.__class__.__name__}(name={self.name})"

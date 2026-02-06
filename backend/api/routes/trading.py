"""
交易相关API
"""
from fastapi import APIRouter, HTTPException
from typing import List, Optional

from api.models.schemas import (
    OrderRequest,
    OrderResponse,
    PositionResponse,
    AccountResponse,
    UpdatePriceRequest,
    MessageResponse
)
from trading_engine.brokers.paper_broker import PaperBroker

router = APIRouter(prefix="/trading", tags=["交易"])

# 全局Paper Trading实例
# 注意：这里使用全局实例，实际使用时可以考虑使用会话管理
paper_broker: Optional[PaperBroker] = None


def get_broker() -> PaperBroker:
    """获取broker实例，如果未初始化则自动初始化"""
    global paper_broker
    if paper_broker is None:
        # 自动初始化，使用默认参数
        paper_broker = PaperBroker(
            initial_cash=1000000.0,
            commission_rate=0.0003,
            slippage=0.0001
        )
    return paper_broker


@router.post("/init", response_model=MessageResponse)
async def init_broker(
    initial_cash: float = 1000000.0,
    commission_rate: float = 0.0003,
    slippage: float = 0.0001
):
    """
    初始化Paper Trading账户

    Args:
        initial_cash: 初始资金
        commission_rate: 手续费率
        slippage: 滑点
    """
    global paper_broker
    try:
        paper_broker = PaperBroker(
            initial_cash=initial_cash,
            commission_rate=commission_rate,
            slippage=slippage
        )

        return MessageResponse(
            message=f"交易账户初始化成功，初始资金: {initial_cash:,.2f}",
            success=True,
            data={
                "initial_cash": initial_cash,
                "commission_rate": commission_rate,
                "slippage": slippage
            }
        )

    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/order", response_model=OrderResponse)
async def submit_order(request: OrderRequest):
    """
    提交订单

    Args:
        symbol: 股票代码
        action: BUY/SELL
        quantity: 数量
        price: 价格（None为市价单）
    """
    broker = get_broker()

    try:
        order = broker.submit_order(
            symbol=request.symbol,
            action=request.action,
            quantity=request.quantity,
            price=request.price
        )

        return OrderResponse(
            order_id=order.order_id,
            symbol=order.symbol,
            action=order.action,
            quantity=order.quantity,
            price=order.price,
            status=order.status.value,
            filled_price=order.filled_price,
            filled_quantity=order.filled_quantity,
            commission=order.commission,
            submit_time=order.submit_time,
            message=f"订单提交成功: {order.action} {order.symbol} {order.quantity}股"
        )

    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/price/update", response_model=MessageResponse)
async def update_market_price(request: UpdatePriceRequest):
    """
    更新市场价格（用于Paper Trading模拟）

    Args:
        symbol: 股票代码
        price: 最新价格
    """
    broker = get_broker()

    try:
        broker.update_market_price(request.symbol, request.price)

        return MessageResponse(
            message=f"已更新 {request.symbol} 价格: {request.price}",
            success=True
        )

    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/account", response_model=AccountResponse)
async def get_account():
    """
    获取账户信息
    """
    broker = get_broker()

    try:
        account_info = broker.get_account_info()

        # 获取所有持仓
        positions = []
        if "positions" in account_info:
            for symbol, pos in account_info["positions"].items():
                positions.append(PositionResponse(
                    symbol=symbol,
                    quantity=pos.quantity,
                    available_quantity=pos.available_quantity,
                    avg_cost=pos.avg_cost,
                    current_price=pos.current_price,
                    market_value=pos.market_value,
                    unrealized_pnl=pos.unrealized_pnl,
                    unrealized_pnl_pct=pos.unrealized_pnl_pct
                ))

        return AccountResponse(
            cash=account_info["cash"],
            market_value=account_info["market_value"],
            total_value=account_info["total_value"],
            available_cash=account_info["cash"],  # Paper Trading中所有现金都可用
            frozen_cash=0.0,  # Paper Trading中没有冻结资金
            positions=positions
        )

    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/position/{symbol}", response_model=PositionResponse)
async def get_position(symbol: str):
    """
    获取指定股票的持仓
    """
    broker = get_broker()

    try:
        pos = broker.get_position(symbol)

        if pos is None:
            raise HTTPException(
                status_code=404,
                detail=f"未持有 {symbol}"
            )

        return PositionResponse(
            symbol=symbol,
            quantity=pos.quantity,
            available_quantity=pos.available_quantity,
            avg_cost=pos.avg_cost,
            current_price=pos.current_price,
            market_value=pos.market_value,
            unrealized_pnl=pos.unrealized_pnl,
            unrealized_pnl_pct=pos.unrealized_pnl_pct
        )

    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/orders")
async def get_orders(symbol: Optional[str] = None):
    """
    获取订单列表

    Args:
        symbol: 股票代码（可选，不传则返回所有）
    """
    broker = get_broker()

    try:
        orders = broker.get_orders(symbol=symbol)

        orders_list = []
        for order in orders:
            orders_list.append({
                "order_id": order.order_id,
                "symbol": order.symbol,
                "action": order.action,
                "quantity": order.quantity,
                "price": order.price,
                "status": order.status.value,
                "filled_price": order.filled_price,
                "filled_quantity": order.filled_quantity,
                "commission": order.commission,
                "submit_time": order.submit_time.isoformat()
            })

        return {
            "orders": orders_list,
            "count": len(orders_list)
        }

    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/reset", response_model=MessageResponse)
async def reset_broker():
    """
    重置交易账户（清空所有持仓和订单）
    """
    global paper_broker
    paper_broker = None

    return MessageResponse(
        message="交易账户已重置",
        success=True
    )

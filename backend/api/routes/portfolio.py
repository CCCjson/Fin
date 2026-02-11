"""
交易记录与持仓管理 API
"""
import re
import json
from datetime import date, datetime
from typing import Optional, List

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field, field_validator
from loguru import logger

from data_engine.storage.database import get_session
from data_engine.storage.models import ManualTrade, AnalysisReport, StockInfo
from portfolio.calculator import PortfolioCalculator

# A股代码格式: 6位数字 + .SH 或 .SZ
_A_SHARE_PATTERN = re.compile(r'^\d{6}\.(SH|SZ)$')

router = APIRouter(prefix="/portfolio", tags=["交易记录与持仓"])


# ==================== Pydantic 模型 ====================

class TradeCreate(BaseModel):
    """创建交易记录"""
    symbol: str = Field(..., description="股票代码，如 600519.SH")
    name: Optional[str] = Field(None, description="股票名称")
    side: str = Field(..., description="BUY / SELL")
    price: float = Field(..., gt=0, description="成交价")
    quantity: int = Field(..., gt=0, description="成交数量（股）")
    amount: Optional[float] = Field(None, description="成交金额，默认 price * quantity")
    commission: float = Field(0, ge=0, description="手续费")
    trade_date: str = Field(..., description="成交日期 YYYY-MM-DD")
    note: Optional[str] = Field(None, description="备注")
    # AI 关联
    report_id: Optional[str] = Field(None)
    ai_recommended_price: Optional[float] = Field(None)
    ai_stop_loss: Optional[float] = Field(None)
    ai_take_profit: Optional[float] = Field(None)
    ai_composite_score: Optional[float] = Field(None)
    ai_strategy: Optional[str] = Field(None)

    @field_validator('symbol')
    @classmethod
    def validate_symbol(cls, v: str) -> str:
        v = v.strip().upper()
        if not _A_SHARE_PATTERN.match(v):
            raise ValueError(f'股票代码格式错误: {v}，应为6位数字+.SH或.SZ，如 600519.SH')
        return v


class TradeUpdate(BaseModel):
    """修改交易记录"""
    symbol: Optional[str] = None
    name: Optional[str] = None
    side: Optional[str] = None
    price: Optional[float] = Field(None, gt=0)
    quantity: Optional[int] = Field(None, gt=0)
    amount: Optional[float] = None
    commission: Optional[float] = Field(None, ge=0)
    trade_date: Optional[str] = None
    note: Optional[str] = None
    report_id: Optional[str] = None
    ai_recommended_price: Optional[float] = None
    ai_stop_loss: Optional[float] = None
    ai_take_profit: Optional[float] = None
    ai_composite_score: Optional[float] = None
    ai_strategy: Optional[str] = None


class ImportFromReportRequest(BaseModel):
    """从报告批量导入交易"""
    report_id: str = Field(..., description="报告 ID")
    trades: List[TradeCreate] = Field(..., description="交易列表")


# ==================== 路由 ====================

@router.post("/trades", summary="录入交易记录")
async def create_trade(req: TradeCreate):
    """录入一笔手动交易"""
    session = get_session()
    try:
        # 自动计算金额
        amount = req.amount if req.amount is not None else req.price * req.quantity

        # 自动查找股票名称
        name = req.name
        if not name:
            stock = session.query(StockInfo).filter(StockInfo.symbol == req.symbol).first()
            if stock:
                name = stock.name

        trade = ManualTrade(
            symbol=req.symbol,
            name=name,
            side=req.side.upper(),
            price=req.price,
            quantity=req.quantity,
            amount=amount,
            commission=req.commission,
            trade_date=date.fromisoformat(req.trade_date),
            note=req.note,
            report_id=req.report_id,
            ai_recommended_price=req.ai_recommended_price,
            ai_stop_loss=req.ai_stop_loss,
            ai_take_profit=req.ai_take_profit,
            ai_composite_score=req.ai_composite_score,
            ai_strategy=req.ai_strategy,
        )

        session.add(trade)
        session.commit()
        session.refresh(trade)

        logger.info(f"录入交易: {trade.side} {trade.symbol} x{trade.quantity} @{trade.price}")
        return _trade_to_dict(trade)

    except Exception as e:
        session.rollback()
        logger.error(f"录入交易失败: {e}")
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        session.close()


@router.get("/trades", summary="交易记录列表")
async def list_trades(
    symbol: Optional[str] = Query(None, description="股票代码筛选"),
    side: Optional[str] = Query(None, description="方向筛选: BUY / SELL"),
    start_date: Optional[str] = Query(None, description="开始日期 YYYY-MM-DD"),
    end_date: Optional[str] = Query(None, description="结束日期 YYYY-MM-DD"),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
):
    """获取交易记录列表（分页 + 筛选）"""
    session = get_session()
    try:
        query = session.query(ManualTrade)

        if symbol:
            query = query.filter(ManualTrade.symbol == symbol)
        if side:
            query = query.filter(ManualTrade.side == side.upper())
        if start_date:
            query = query.filter(ManualTrade.trade_date >= date.fromisoformat(start_date))
        if end_date:
            query = query.filter(ManualTrade.trade_date <= date.fromisoformat(end_date))

        total = query.count()
        trades = query.order_by(ManualTrade.trade_date.desc(), ManualTrade.id.desc()).offset(offset).limit(limit).all()

        return {
            "total": total,
            "trades": [_trade_to_dict(t) for t in trades],
        }
    finally:
        session.close()


@router.put("/trades/{trade_id}", summary="修改交易记录")
async def update_trade(trade_id: int, req: TradeUpdate):
    """修改一笔交易记录"""
    session = get_session()
    try:
        trade = session.query(ManualTrade).filter(ManualTrade.id == trade_id).first()
        if not trade:
            raise HTTPException(status_code=404, detail="交易记录不存在")

        update_data = req.model_dump(exclude_unset=True)
        if "trade_date" in update_data and update_data["trade_date"] is not None:
            update_data["trade_date"] = date.fromisoformat(update_data["trade_date"])

        for key, value in update_data.items():
            setattr(trade, key, value)

        # 重新计算金额
        if "price" in update_data or "quantity" in update_data:
            if "amount" not in update_data:
                trade.amount = trade.price * trade.quantity

        session.commit()
        session.refresh(trade)

        return _trade_to_dict(trade)

    except HTTPException:
        raise
    except Exception as e:
        session.rollback()
        logger.error(f"修改交易失败: {e}")
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        session.close()


@router.delete("/trades/{trade_id}", summary="删除交易记录")
async def delete_trade(trade_id: int):
    """删除一笔交易记录"""
    session = get_session()
    try:
        trade = session.query(ManualTrade).filter(ManualTrade.id == trade_id).first()
        if not trade:
            raise HTTPException(status_code=404, detail="交易记录不存在")

        session.delete(trade)
        session.commit()
        return {"message": "交易记录已删除", "id": trade_id}

    except HTTPException:
        raise
    except Exception as e:
        session.rollback()
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        session.close()


@router.get("/positions", summary="当前持仓")
async def get_positions():
    """获取当前持仓 + 浮动盈亏"""
    calculator = PortfolioCalculator()
    positions = calculator.get_current_positions()
    return {"positions": positions}


@router.get("/stats", summary="绩效统计")
async def get_stats():
    """获取整体绩效统计"""
    calculator = PortfolioCalculator()
    stats = calculator.get_performance_stats()
    return stats


@router.get("/reports/{report_id}/recommendations", summary="获取报告推荐列表")
async def get_report_recommendations(report_id: str):
    """解析 AI 报告数据快照，提取推荐标的列表（供导入时筛选）"""
    session = get_session()
    try:
        report = session.query(AnalysisReport).filter(
            AnalysisReport.report_id == report_id
        ).first()

        if not report:
            raise HTTPException(status_code=404, detail="报告不存在")

        if not report.data_snapshot:
            return {"report_id": report_id, "title": report.title, "recommendations": []}

        try:
            snapshot = json.loads(report.data_snapshot)
        except json.JSONDecodeError:
            return {"report_id": report_id, "title": report.title, "recommendations": []}

        recommendations = []
        top_stocks = snapshot.get("top_stocks", {})
        buy_recs = top_stocks.get("buy_recommendations", [])

        for rec in buy_recs:
            recommendations.append({
                "symbol": rec.get("symbol", ""),
                "name": rec.get("name", ""),
                "price": rec.get("price") or rec.get("entry_price"),
                "stop_loss": rec.get("stop_loss"),
                "take_profit": rec.get("take_profit"),
                "score": rec.get("score") or rec.get("composite_score"),
                "strategy": rec.get("strategy", ""),
            })

        return {
            "report_id": report_id,
            "title": report.title,
            "recommendations": recommendations,
        }

    finally:
        session.close()


@router.post("/import-from-report", summary="从报告批量导入交易")
async def import_from_report(req: ImportFromReportRequest):
    """从 AI 报告批量导入交易记录"""
    session = get_session()
    try:
        created = []
        for t in req.trades:
            amount = t.amount if t.amount is not None else t.price * t.quantity
            name = t.name
            if not name:
                stock = session.query(StockInfo).filter(StockInfo.symbol == t.symbol).first()
                if stock:
                    name = stock.name

            trade = ManualTrade(
                symbol=t.symbol,
                name=name,
                side=t.side.upper(),
                price=t.price,
                quantity=t.quantity,
                amount=amount,
                commission=t.commission,
                trade_date=date.fromisoformat(t.trade_date),
                note=t.note,
                report_id=req.report_id,
                ai_recommended_price=t.ai_recommended_price,
                ai_stop_loss=t.ai_stop_loss,
                ai_take_profit=t.ai_take_profit,
                ai_composite_score=t.ai_composite_score,
                ai_strategy=t.ai_strategy,
            )
            session.add(trade)
            created.append(trade)

        session.commit()
        for t in created:
            session.refresh(t)

        logger.info(f"从报告 {req.report_id} 导入 {len(created)} 笔交易")
        return {
            "message": f"成功导入 {len(created)} 笔交易",
            "count": len(created),
            "trades": [_trade_to_dict(t) for t in created],
        }

    except Exception as e:
        session.rollback()
        logger.error(f"从报告导入交易失败: {e}")
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        session.close()


# ==================== 辅助函数 ====================

def _trade_to_dict(t: ManualTrade) -> dict:
    return {
        "id": t.id,
        "symbol": t.symbol,
        "name": t.name,
        "side": t.side,
        "price": t.price,
        "quantity": t.quantity,
        "amount": t.amount,
        "commission": t.commission,
        "trade_date": str(t.trade_date) if t.trade_date else None,
        "note": t.note,
        "report_id": t.report_id,
        "ai_recommended_price": t.ai_recommended_price,
        "ai_stop_loss": t.ai_stop_loss,
        "ai_take_profit": t.ai_take_profit,
        "ai_composite_score": t.ai_composite_score,
        "ai_strategy": t.ai_strategy,
        "created_at": str(t.created_at) if t.created_at else None,
        "updated_at": str(t.updated_at) if t.updated_at else None,
    }

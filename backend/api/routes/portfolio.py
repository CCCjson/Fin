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
from portfolio.closed_trade_service import ClosedTradeService
from trading_engine.risk.manager import RiskManager
from trading_engine.risk.adapter import build_broker_info, get_effective_risk_config
from trading_engine.config import RISK_CONFIG

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
        # 卖出时校验持仓是否足够
        if req.side.upper() == "SELL":
            calculator = PortfolioCalculator()
            current_positions = {
                p["symbol"]: p["quantity"]
                for p in calculator.get_current_positions()
            }
            held_qty = current_positions.get(req.symbol, 0)
            if req.quantity > held_qty:
                raise HTTPException(
                    status_code=400,
                    detail=f"持仓不足: {req.symbol} 当前持有 {held_qty} 股，卖出 {req.quantity} 股"
                )

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

        # --- SELL 交易自动生成已平仓记录 ---
        if trade.side == "SELL":
            try:
                _closed_trade_service.generate_closed_trade_for_sell(trade.id)
            except Exception as e:
                logger.warning(f"自动生成已平仓记录失败（不影响录入）: {e}")

        # --- 风控检查（不阻断录入，仅产生警告） ---
        risk_warnings = []
        try:
            broker_info = build_broker_info()
            rm = RiskManager(config=get_effective_risk_config())
            _, results = rm.check_order(
                symbol=trade.symbol,
                action=trade.side,
                quantity=trade.quantity,
                price=trade.price,
                broker_info=broker_info,
            )
            for r in results:
                if not r.passed:
                    risk_warnings.append({
                        "rule": r.rule_name,
                        "message": r.message,
                        "severity": r.severity,
                    })
        except Exception as e:
            logger.warning(f"风控检查异常（不影响录入）: {e}")

        result = _trade_to_dict(trade)
        result["risk_warnings"] = risk_warnings
        return result

    except HTTPException:
        raise
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

        # 修改交易后，删除关联的已平仓记录并重新生成
        try:
            _closed_trade_service.delete_by_trade_id(trade_id)
            if trade.side == "SELL":
                _closed_trade_service.generate_closed_trade_for_sell(trade.id)
        except Exception as e:
            logger.warning(f"更新已平仓记录失败（不影响修改）: {e}")

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

        # 先删除关联的已平仓记录
        try:
            _closed_trade_service.delete_by_trade_id(trade_id)
        except Exception as e:
            logger.warning(f"删除关联已平仓记录失败: {e}")

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


@router.get("/risk-monitor", summary="风控监控")
async def get_risk_monitor():
    """全面的风控监控：总体概览 + 每只持仓的风控指标"""
    try:
        eff_cfg = get_effective_risk_config()
        rm = RiskManager(config=eff_cfg)
        broker_info = build_broker_info()
        total_capital = broker_info["total_value"]
        market_value = broker_info["market_value"]
        positions = broker_info["positions"]
        recent_pnls = broker_info.get("recent_closed_pnls", [])
        last_loss_date = broker_info.get("last_loss_date")

        stop_loss_pct = RISK_CONFIG.get("stop_loss_pct", 0.05)
        take_profit_pct = RISK_CONFIG.get("take_profit_pct", 0.15)
        max_pos_pct = eff_cfg.get("max_position_pct", 0.20)
        max_total_pct = eff_cfg.get("max_total_position_pct", 0.80)

        # --- 总体概览 ---
        total_position_pct = (market_value / total_capital) if total_capital > 0 else 0
        cash = broker_info["cash"]

        # 连续亏损
        consecutive_losses = 0
        for p in recent_pnls:
            if p < 0:
                consecutive_losses += 1
            else:
                break

        overview = {
            "total_capital": total_capital,
            "market_value": round(market_value, 2),
            "cash": round(cash, 2),
            "total_position_pct": round(total_position_pct * 100, 1),
            "max_total_position_pct": max_total_pct * 100,
            "total_position_ok": total_position_pct <= max_total_pct,
            "consecutive_losses": consecutive_losses,
            "max_consecutive_losses": RISK_CONFIG.get("max_consecutive_losses", 3),
            "consecutive_loss_ok": consecutive_losses < RISK_CONFIG.get("max_consecutive_losses", 3),
            "last_loss_date": last_loss_date,
        }

        # --- 每只持仓的风控指标 ---
        position_risks = []
        alerts = []

        for sym, pos in positions.items():
            cur = pos.get("current_price")
            avg = pos.get("avg_cost", 0)
            mv = pos.get("market_value") or 0

            if cur is None or avg <= 0:
                continue

            # 直接复用 PortfolioCalculator 已算好的百分比，保证和持仓表一致
            pnl_pct_display = pos.get("unrealized_pnl_pct")
            if pnl_pct_display is None:
                pnl_pct_display = round((cur - avg) / avg * 100, 2)

            pnl_ratio = pnl_pct_display / 100  # 转回小数做比较
            pos_pct = mv / total_capital if total_capital > 0 else 0

            stop_loss_display = round(stop_loss_pct * 100, 2)   # 5.0
            take_profit_display = round(take_profit_pct * 100, 2)  # 15.0

            # 距止损/止盈的距离（百分点）
            dist_stop_loss = pnl_pct_display - (-stop_loss_display)
            dist_take_profit = take_profit_display - pnl_pct_display

            # 风控级别
            if pnl_ratio <= -stop_loss_pct:
                level = "danger"
            elif pnl_ratio <= -stop_loss_pct * 0.6:
                level = "warning"
            elif pnl_ratio >= take_profit_pct:
                level = "take_profit"
            elif pnl_ratio >= take_profit_pct * 0.8:
                level = "near_tp"
            else:
                level = "safe"

            # 仓位是否超限
            pos_overweight = pos_pct > max_pos_pct

            entry = {
                "symbol": sym,
                "name": pos.get("name", sym),
                "pnl_pct": pnl_pct_display,
                "position_pct": round(pos_pct * 100, 1),
                "max_position_pct": max_pos_pct * 100,
                "position_overweight": pos_overweight,
                "stop_loss_pct": -stop_loss_display,
                "take_profit_pct": take_profit_display,
                "dist_stop_loss": round(dist_stop_loss, 2),
                "dist_take_profit": round(dist_take_profit, 2),
                "level": level,
            }
            position_risks.append(entry)

            # 生成 alerts（兼容原来的前端逻辑）
            if level == "danger":
                alerts.append({
                    "symbol": sym, "name": pos.get("name", sym),
                    "rule": "止损规则",
                    "message": f"亏损 {abs(pnl_pct_display):.2f}% 触发止损（止损线 {stop_loss_display:.0f}%）",
                    "severity": "ERROR",
                })
            elif level == "warning":
                alerts.append({
                    "symbol": sym, "name": pos.get("name", sym),
                    "rule": "接近止损",
                    "message": f"亏损 {abs(pnl_pct_display):.2f}%，接近止损线 {stop_loss_display:.0f}%",
                    "severity": "WARNING",
                })
            elif level == "take_profit":
                alerts.append({
                    "symbol": sym, "name": pos.get("name", sym),
                    "rule": "止盈规则",
                    "message": f"盈利 {pnl_pct_display:.2f}% 触发止盈（止盈线 {take_profit_display:.0f}%）",
                    "severity": "WARNING",
                })
            if pos_overweight:
                alerts.append({
                    "symbol": sym, "name": pos.get("name", sym),
                    "rule": "仓位超限",
                    "message": f"仓位 {pos_pct*100:.1f}% 超过单股限制 {max_pos_pct*100:.0f}%",
                    "severity": "ERROR",
                })

        # 按风险级别排序：danger > warning > take_profit > near_tp > safe
        level_order = {"danger": 0, "warning": 1, "take_profit": 2, "near_tp": 3, "safe": 4}
        position_risks.sort(key=lambda x: (level_order.get(x["level"], 9), -abs(x["pnl_pct"])))

        return {
            "overview": overview,
            "position_risks": position_risks,
            "alerts": alerts,
        }

    except Exception as e:
        logger.error(f"风控监控异常: {e}")
        return {"overview": {}, "position_risks": [], "alerts": [], "error": str(e)}


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


# ==================== 已平仓交易 API ====================

_closed_trade_service = ClosedTradeService()


@router.get("/closed-trades", summary="已平仓交易列表")
async def get_closed_trades(
    symbol: Optional[str] = Query(None, description="股票代码筛选"),
    sell_reason: Optional[str] = Query(None, description="卖出原因: take_profit/stop_loss/manual_close"),
    market_env: Optional[str] = Query(None, description="大盘环境: bullish/neutral/bearish"),
    start_date: Optional[str] = Query(None, description="卖出开始日期 YYYY-MM-DD"),
    end_date: Optional[str] = Query(None, description="卖出结束日期 YYYY-MM-DD"),
    sort_by: str = Query("sell_date", description="排序字段"),
    sort_order: str = Query("desc", description="排序方向: asc/desc"),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
):
    """获取已平仓交易列表（分页 + 筛选 + 汇总统计）"""
    return _closed_trade_service.get_closed_trades(
        symbol=symbol,
        sell_reason=sell_reason,
        market_env=market_env,
        start_date=date.fromisoformat(start_date) if start_date else None,
        end_date=date.fromisoformat(end_date) if end_date else None,
        sort_by=sort_by,
        sort_order=sort_order,
        limit=limit,
        offset=offset,
    )


@router.post("/closed-trades/rebuild", summary="重建已平仓记录")
async def rebuild_closed_trades():
    """清空并重建全部已平仓交易记录（幂等操作）"""
    result = _closed_trade_service.rebuild_all()
    return {
        "message": f"重建完成: 成功 {result['count']} 笔, 失败 {result['errors']} 笔",
        **result,
    }


@router.get("/closed-trades/stats", summary="已平仓交易统计")
async def get_closed_trade_stats():
    """按策略、卖出原因、大盘环境分组统计"""
    return _closed_trade_service.get_stats()

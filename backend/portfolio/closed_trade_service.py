"""
ClosedTradeService — 已平仓交易配对服务

将买入和卖出交易配对，追踪完整交易周期，计算收益率和大盘环境。
使用加权平均成本法（与 PortfolioCalculator 一致）。
"""
import json
from datetime import date, timedelta
from typing import Optional, Dict, List, Any

import pandas as pd
from loguru import logger

from data_engine.storage.database import get_session
from data_engine.storage.models import ManualTrade, ClosedTrade


class ClosedTradeService:
    """已平仓交易配对与分析服务"""

    def __init__(self):
        self._hs300_cache: Optional[pd.DataFrame] = None

    # ==================== 公开方法 ====================

    def generate_closed_trade_for_sell(self, sell_trade_id: int) -> Optional[Dict]:
        """
        为一笔卖出交易生成已平仓记录。

        按时间回放该 symbol 全部交易，用加权平均成本法计算 avg_cost，
        然后配对生成 ClosedTrade 记录。

        Args:
            sell_trade_id: ManualTrade 表中 SELL 记录的 id

        Returns:
            生成的 ClosedTrade 字典，或 None
        """
        session = get_session()
        try:
            sell_trade = session.query(ManualTrade).filter(ManualTrade.id == sell_trade_id).first()
            if not sell_trade or sell_trade.side != "SELL":
                logger.warning(f"无效的卖出交易 id={sell_trade_id}")
                return None

            symbol = sell_trade.symbol

            # 获取该 symbol 在卖出日期及之前的全部交易（按时间排序）
            all_trades = (
                session.query(ManualTrade)
                .filter(
                    ManualTrade.symbol == symbol,
                    ManualTrade.trade_date <= sell_trade.trade_date,
                    ManualTrade.id <= sell_trade.id,  # 同日的交易按 id 排序
                )
                .order_by(ManualTrade.trade_date.asc(), ManualTrade.id.asc())
                .all()
            )

            # 加权平均成本法回放
            avg_cost = 0.0
            quantity = 0
            total_cost = 0.0
            first_buy_trade = None
            buy_commission_total = 0.0

            for t in all_trades:
                if t.id == sell_trade.id:
                    break  # 到卖出这笔就停
                if t.side == "BUY":
                    if first_buy_trade is None:
                        first_buy_trade = t
                    new_cost = t.amount + (t.commission or 0)
                    total_cost += new_cost
                    quantity += t.quantity
                    buy_commission_total += (t.commission or 0)
                    if quantity > 0:
                        avg_cost = total_cost / quantity
                elif t.side == "SELL":
                    quantity -= t.quantity
                    total_cost = avg_cost * quantity

            if avg_cost <= 0 or quantity <= 0:
                logger.warning(f"无法配对卖出交易: {symbol} id={sell_trade_id}，持仓不足")
                return None

            # 查找与该买入关联的 AI 信号信息
            # 优先用最近一笔有 AI 信息的买入记录
            buy_trades_with_ai = (
                session.query(ManualTrade)
                .filter(
                    ManualTrade.symbol == symbol,
                    ManualTrade.side == "BUY",
                    ManualTrade.trade_date <= sell_trade.trade_date,
                    ManualTrade.id < sell_trade.id,
                    ManualTrade.ai_strategy.isnot(None),
                )
                .order_by(ManualTrade.trade_date.desc(), ManualTrade.id.desc())
                .first()
            )

            # 如果没有 AI 信息的买入，就取最近一笔买入
            if not buy_trades_with_ai:
                buy_trades_with_ai = (
                    session.query(ManualTrade)
                    .filter(
                        ManualTrade.symbol == symbol,
                        ManualTrade.side == "BUY",
                        ManualTrade.trade_date <= sell_trade.trade_date,
                        ManualTrade.id < sell_trade.id,
                    )
                    .order_by(ManualTrade.trade_date.desc(), ManualTrade.id.desc())
                    .first()
                )

            ref_buy = buy_trades_with_ai or first_buy_trade
            if not ref_buy:
                logger.warning(f"找不到对应的买入记录: {symbol}")
                return None

            # 计算各项指标
            sell_commission = sell_trade.commission or 0
            sell_revenue = sell_trade.price * sell_trade.quantity - sell_commission
            cost_basis = avg_cost * sell_trade.quantity
            pnl = sell_revenue - cost_basis
            pnl_pct = (pnl / cost_basis * 100) if cost_basis > 0 else 0

            buy_date = ref_buy.trade_date
            sell_date = sell_trade.trade_date
            holding_days = (sell_date - buy_date).days if buy_date and sell_date else 0

            # 大盘环境
            market_env, market_env_detail = self._assess_market_environment(buy_date)

            # 卖出原因
            sell_reason = self._determine_sell_reason(
                sell_trade.price,
                ref_buy.ai_stop_loss,
                ref_buy.ai_take_profit,
                sell_trade.note,
            )

            # 同期沪深300收益率
            benchmark_return = self._calc_benchmark_return(buy_date, sell_date)
            excess_return = (pnl_pct - benchmark_return) if benchmark_return is not None else None

            # 佣金合计（估算：按比例分摊买入佣金 + 卖出佣金）
            total_commission = sell_commission
            if quantity > 0 and buy_commission_total > 0:
                # 按卖出数量占总持仓的比例分摊买入佣金
                ratio = sell_trade.quantity / (quantity + sell_trade.quantity)
                total_commission += buy_commission_total * ratio

            # 检查是否已存在（避免重复）
            existing = session.query(ClosedTrade).filter(
                ClosedTrade.sell_trade_id == sell_trade.id
            ).first()
            if existing:
                session.delete(existing)

            closed = ClosedTrade(
                symbol=symbol,
                name=sell_trade.name or ref_buy.name,
                buy_trade_id=ref_buy.id,
                buy_date=buy_date,
                buy_price=round(avg_cost, 4),
                buy_quantity=sell_trade.quantity,
                buy_signal_strategy=ref_buy.ai_strategy,
                buy_signal_strength=ref_buy.ai_composite_score,
                ai_stop_loss=ref_buy.ai_stop_loss,
                ai_take_profit=ref_buy.ai_take_profit,
                market_env=market_env,
                market_env_detail=json.dumps(market_env_detail, ensure_ascii=False) if market_env_detail else None,
                sell_trade_id=sell_trade.id,
                sell_date=sell_date,
                sell_price=sell_trade.price,
                sell_quantity=sell_trade.quantity,
                sell_reason=sell_reason,
                holding_days=holding_days,
                pnl=round(pnl, 2),
                pnl_pct=round(pnl_pct, 2),
                total_commission=round(total_commission, 2),
                benchmark_return_pct=round(benchmark_return, 2) if benchmark_return is not None else None,
                excess_return_pct=round(excess_return, 2) if excess_return is not None else None,
            )

            session.add(closed)
            session.commit()
            session.refresh(closed)

            logger.info(f"生成已平仓记录: {symbol} 买入{buy_date} 卖出{sell_date} 收益{pnl_pct:.2f}%")
            return self._to_dict(closed)

        except Exception as e:
            session.rollback()
            logger.error(f"生成已平仓记录失败: {e}")
            return None
        finally:
            session.close()

    def rebuild_all(self) -> Dict[str, Any]:
        """
        清空并重建全部已平仓记录（幂等操作）。

        Returns:
            {"count": 生成数量, "errors": 失败数量}
        """
        session = get_session()
        try:
            # 清空现有记录
            deleted = session.query(ClosedTrade).delete()
            session.commit()
            logger.info(f"清空已有 {deleted} 条已平仓记录，开始重建")
        except Exception as e:
            session.rollback()
            logger.error(f"清空已平仓记录失败: {e}")
            return {"count": 0, "errors": 0}
        finally:
            session.close()

        # 预加载沪深300数据
        self._preload_hs300()

        # 获取所有 SELL 交易
        session = get_session()
        try:
            sell_trades = (
                session.query(ManualTrade)
                .filter(ManualTrade.side == "SELL")
                .order_by(ManualTrade.trade_date.asc(), ManualTrade.id.asc())
                .all()
            )
            sell_ids = [t.id for t in sell_trades]
        finally:
            session.close()

        count = 0
        errors = 0
        for sell_id in sell_ids:
            result = self.generate_closed_trade_for_sell(sell_id)
            if result:
                count += 1
            else:
                errors += 1

        # 清理缓存
        self._hs300_cache = None

        logger.info(f"重建完成: 成功 {count} 笔, 失败 {errors} 笔")
        return {"count": count, "errors": errors}

    def delete_by_trade_id(self, trade_id: int):
        """删除与某笔 ManualTrade 关联的 ClosedTrade 记录"""
        session = get_session()
        try:
            deleted = session.query(ClosedTrade).filter(
                (ClosedTrade.buy_trade_id == trade_id) | (ClosedTrade.sell_trade_id == trade_id)
            ).delete(synchronize_session='fetch')
            session.commit()
            if deleted > 0:
                logger.info(f"删除了 {deleted} 条关联的已平仓记录 (trade_id={trade_id})")
        except Exception as e:
            session.rollback()
            logger.error(f"删除关联已平仓记录失败: {e}")
        finally:
            session.close()

    def get_closed_trades(
        self,
        symbol: Optional[str] = None,
        sell_reason: Optional[str] = None,
        market_env: Optional[str] = None,
        start_date: Optional[date] = None,
        end_date: Optional[date] = None,
        sort_by: str = "sell_date",
        sort_order: str = "desc",
        limit: int = 50,
        offset: int = 0,
    ) -> Dict[str, Any]:
        """
        查询已平仓交易列表（分页 + 筛选）

        Returns:
            {"total": int, "trades": list, "summary": dict}
        """
        session = get_session()
        try:
            query = session.query(ClosedTrade)

            if symbol:
                query = query.filter(ClosedTrade.symbol == symbol)
            if sell_reason:
                query = query.filter(ClosedTrade.sell_reason == sell_reason)
            if market_env:
                query = query.filter(ClosedTrade.market_env == market_env)
            if start_date:
                query = query.filter(ClosedTrade.sell_date >= start_date)
            if end_date:
                query = query.filter(ClosedTrade.sell_date <= end_date)

            total = query.count()

            # 排序
            sort_col = getattr(ClosedTrade, sort_by, ClosedTrade.sell_date)
            if sort_order == "asc":
                query = query.order_by(sort_col.asc())
            else:
                query = query.order_by(sort_col.desc())

            trades = query.offset(offset).limit(limit).all()

            # 汇总统计（基于筛选后的全部数据）
            all_for_summary = session.query(ClosedTrade)
            if symbol:
                all_for_summary = all_for_summary.filter(ClosedTrade.symbol == symbol)
            if sell_reason:
                all_for_summary = all_for_summary.filter(ClosedTrade.sell_reason == sell_reason)
            if market_env:
                all_for_summary = all_for_summary.filter(ClosedTrade.market_env == market_env)
            if start_date:
                all_for_summary = all_for_summary.filter(ClosedTrade.sell_date >= start_date)
            if end_date:
                all_for_summary = all_for_summary.filter(ClosedTrade.sell_date <= end_date)

            all_records = all_for_summary.all()
            summary = self._calc_summary(all_records)

            return {
                "total": total,
                "trades": [self._to_dict(t) for t in trades],
                "summary": summary,
            }
        finally:
            session.close()

    def get_stats(self) -> Dict[str, Any]:
        """
        详细统计：按策略、卖出原因、大盘环境分组统计

        Returns:
            {"overall": {...}, "by_strategy": [...], "by_sell_reason": [...], "by_market_env": [...]}
        """
        session = get_session()
        try:
            all_records = session.query(ClosedTrade).all()
            if not all_records:
                return {"overall": self._empty_summary(), "by_strategy": [], "by_sell_reason": [], "by_market_env": []}

            overall = self._calc_summary(all_records)

            # 按策略分组
            by_strategy: Dict[str, list] = {}
            for r in all_records:
                key = r.buy_signal_strategy or "未知策略"
                by_strategy.setdefault(key, []).append(r)

            strategy_stats = [
                {"name": k, **self._calc_summary(v)} for k, v in by_strategy.items()
            ]
            strategy_stats.sort(key=lambda x: x["count"], reverse=True)

            # 按卖出原因分组
            by_reason: Dict[str, list] = {}
            for r in all_records:
                key = r.sell_reason or "unknown"
                by_reason.setdefault(key, []).append(r)

            reason_stats = [
                {"name": k, **self._calc_summary(v)} for k, v in by_reason.items()
            ]

            # 按大盘环境分组
            by_env: Dict[str, list] = {}
            for r in all_records:
                key = r.market_env or "unknown"
                by_env.setdefault(key, []).append(r)

            env_stats = [
                {"name": k, **self._calc_summary(v)} for k, v in by_env.items()
            ]

            return {
                "overall": overall,
                "by_strategy": strategy_stats,
                "by_sell_reason": reason_stats,
                "by_market_env": env_stats,
            }
        finally:
            session.close()

    # ==================== 内部方法 ====================

    def _assess_market_environment(self, buy_date: date) -> tuple:
        """
        综合3指标投票判断买入日的大盘环境。

        指标：
        1. 沪深300近5日涨跌幅
        2. 收盘价 vs MA20
        3. 5日均量 vs 20日均量

        Returns:
            (env_str, detail_dict) 如 ("bullish", {...})
        """
        try:
            df = self._get_hs300_data()
            if df is None or df.empty:
                return ("unknown", None)

            # 找到买入日期或之前最近的交易日
            mask = df.index <= pd.Timestamp(buy_date)
            if not mask.any():
                return ("unknown", None)

            df_before = df[mask]
            if len(df_before) < 20:
                return ("unknown", None)

            close = df_before['close'].iloc[-1]
            close_5d_ago = df_before['close'].iloc[-5] if len(df_before) >= 5 else close

            # 指标1: 近5日涨跌幅
            change_5d = (close - close_5d_ago) / close_5d_ago * 100

            # 指标2: 收盘价 vs MA20
            ma20 = df_before['close'].iloc[-20:].mean()
            above_ma20 = close > ma20

            # 指标3: 5日均量 vs 20日均量
            vol_5d = df_before['volume'].iloc[-5:].mean()
            vol_20d = df_before['volume'].iloc[-20:].mean()
            vol_ratio = vol_5d / vol_20d if vol_20d > 0 else 1.0

            # 投票
            bullish_votes = 0
            bearish_votes = 0

            if change_5d > 1:
                bullish_votes += 1
            elif change_5d < -1:
                bearish_votes += 1

            if above_ma20:
                bullish_votes += 1
            else:
                bearish_votes += 1

            if vol_ratio > 1.1:
                bullish_votes += 1
            elif vol_ratio < 0.9:
                bearish_votes += 1

            if bullish_votes >= 2:
                env = "bullish"
            elif bearish_votes >= 2:
                env = "bearish"
            else:
                env = "neutral"

            detail = {
                "change_5d": round(float(change_5d), 2),
                "close": round(float(close), 2),
                "ma20": round(float(ma20), 2),
                "above_ma20": bool(above_ma20),
                "vol_5d_avg": round(float(vol_5d), 0),
                "vol_20d_avg": round(float(vol_20d), 0),
                "vol_ratio": round(float(vol_ratio), 2),
                "bullish_votes": int(bullish_votes),
                "bearish_votes": int(bearish_votes),
            }

            return (env, detail)

        except Exception as e:
            logger.warning(f"评估大盘环境失败 ({buy_date}): {e}")
            return ("unknown", None)

    def _determine_sell_reason(
        self,
        sell_price: float,
        ai_stop_loss: Optional[float],
        ai_take_profit: Optional[float],
        note: Optional[str],
    ) -> str:
        """
        判断卖出原因。

        优先级：
        1. 备注关键词（止盈/止损）
        2. 价格 vs AI 止盈止损价
        3. 默认 manual_close
        """
        # 检查备注关键词
        if note:
            note_lower = note.lower()
            if any(kw in note_lower for kw in ["止盈", "take_profit", "tp", "止赢"]):
                return "take_profit"
            if any(kw in note_lower for kw in ["止损", "stop_loss", "sl", "割肉", "斩仓"]):
                return "stop_loss"

        # 检查 AI 止盈止损价
        if ai_take_profit and sell_price >= ai_take_profit:
            return "take_profit"
        if ai_stop_loss and sell_price <= ai_stop_loss:
            return "stop_loss"

        return "manual_close"

    def _calc_benchmark_return(self, buy_date: date, sell_date: date) -> Optional[float]:
        """计算同期沪深300收益率(%)"""
        try:
            df = self._get_hs300_data()
            if df is None or df.empty:
                return None

            # 找买入日期的最近收盘价
            buy_mask = df.index <= pd.Timestamp(buy_date)
            if not buy_mask.any():
                return None
            buy_close = df[buy_mask]['close'].iloc[-1]

            # 找卖出日期的最近收盘价
            sell_mask = df.index <= pd.Timestamp(sell_date)
            if not sell_mask.any():
                return None
            sell_close = df[sell_mask]['close'].iloc[-1]

            if buy_close <= 0:
                return None

            return (sell_close - buy_close) / buy_close * 100

        except Exception as e:
            logger.warning(f"计算基准收益失败: {e}")
            return None

    def _get_hs300_data(self) -> Optional[pd.DataFrame]:
        """获取沪深300数据（优先缓存 → DB → akshare）"""
        if self._hs300_cache is not None:
            return self._hs300_cache

        # 先尝试从 DB 读取
        session = get_session()
        try:
            from data_engine.storage.models import DailyQuote
            quotes = (
                session.query(DailyQuote)
                .filter(DailyQuote.symbol == "000300.SH")
                .order_by(DailyQuote.date.asc())
                .all()
            )
            if quotes and len(quotes) > 50:
                data = [{
                    'date': q.date,
                    'close': q.close,
                    'volume': q.volume or 0,
                } for q in quotes]
                df = pd.DataFrame(data)
                df['date'] = pd.to_datetime(df['date'])
                df = df.set_index('date')
                self._hs300_cache = df
                return df
        except Exception as e:
            logger.debug(f"从 DB 读取沪深300失败: {e}")
        finally:
            session.close()

        # 从 akshare 获取
        return self._fetch_hs300_from_akshare()

    def _fetch_hs300_from_akshare(self) -> Optional[pd.DataFrame]:
        """从 akshare 获取沪深300日线数据"""
        try:
            import akshare as ak
            from net import domestic_akshare
            df = domestic_akshare(ak.stock_zh_index_daily_em, symbol="sh000300")
            if df is None or df.empty:
                return None

            df = df.rename(columns={"date": "date", "close": "close", "volume": "volume"})
            df['date'] = pd.to_datetime(df['date'])
            df = df.set_index('date')
            df = df[['close', 'volume']].copy()
            df['close'] = df['close'].astype(float)
            df['volume'] = df['volume'].astype(float)

            self._hs300_cache = df
            logger.info(f"从 akshare 获取沪深300数据: {len(df)} 条")
            return df

        except Exception as e:
            logger.warning(f"从 akshare 获取沪深300数据失败: {e}")
            return None

    def _preload_hs300(self):
        """预加载沪深300数据到缓存"""
        self._hs300_cache = None
        self._get_hs300_data()

    def _calc_summary(self, records: List[ClosedTrade]) -> Dict[str, Any]:
        """计算一组已平仓记录的汇总统计"""
        if not records:
            return self._empty_summary()

        count = len(records)
        wins = [r for r in records if r.pnl and r.pnl > 0]
        losses = [r for r in records if r.pnl and r.pnl < 0]
        win_count = len(wins)
        loss_count = len(losses)

        total_pnl = sum(r.pnl or 0 for r in records)
        avg_pnl_pct = sum(r.pnl_pct or 0 for r in records) / count if count > 0 else 0
        avg_holding_days = sum(r.holding_days or 0 for r in records) / count if count > 0 else 0

        avg_win_pct = (sum(r.pnl_pct or 0 for r in wins) / win_count) if win_count > 0 else 0
        avg_loss_pct = (sum(r.pnl_pct or 0 for r in losses) / loss_count) if loss_count > 0 else 0

        benchmark_returns = [r.benchmark_return_pct for r in records if r.benchmark_return_pct is not None]
        avg_benchmark = sum(benchmark_returns) / len(benchmark_returns) if benchmark_returns else None

        excess_returns = [r.excess_return_pct for r in records if r.excess_return_pct is not None]
        avg_excess = sum(excess_returns) / len(excess_returns) if excess_returns else None

        return {
            "count": count,
            "win_count": win_count,
            "loss_count": loss_count,
            "win_rate": round(win_count / count * 100, 1) if count > 0 else 0,
            "total_pnl": round(total_pnl, 2),
            "avg_pnl_pct": round(avg_pnl_pct, 2),
            "avg_holding_days": round(avg_holding_days, 1),
            "avg_win_pct": round(avg_win_pct, 2),
            "avg_loss_pct": round(avg_loss_pct, 2),
            "avg_benchmark_return_pct": round(avg_benchmark, 2) if avg_benchmark is not None else None,
            "avg_excess_return_pct": round(avg_excess, 2) if avg_excess is not None else None,
        }

    def _empty_summary(self) -> Dict[str, Any]:
        return {
            "count": 0,
            "win_count": 0,
            "loss_count": 0,
            "win_rate": 0,
            "total_pnl": 0,
            "avg_pnl_pct": 0,
            "avg_holding_days": 0,
            "avg_win_pct": 0,
            "avg_loss_pct": 0,
            "avg_benchmark_return_pct": None,
            "avg_excess_return_pct": None,
        }

    @staticmethod
    def _to_dict(ct: ClosedTrade) -> Dict[str, Any]:
        """ClosedTrade 转字典"""
        detail = None
        if ct.market_env_detail:
            try:
                detail = json.loads(ct.market_env_detail)
            except (json.JSONDecodeError, TypeError):
                detail = None

        return {
            "id": ct.id,
            "symbol": ct.symbol,
            "name": ct.name,
            "buy_trade_id": ct.buy_trade_id,
            "buy_date": str(ct.buy_date) if ct.buy_date else None,
            "buy_price": ct.buy_price,
            "buy_quantity": ct.buy_quantity,
            "buy_signal_strategy": ct.buy_signal_strategy,
            "buy_signal_strength": ct.buy_signal_strength,
            "ai_stop_loss": ct.ai_stop_loss,
            "ai_take_profit": ct.ai_take_profit,
            "market_env": ct.market_env,
            "market_env_detail": detail,
            "sell_trade_id": ct.sell_trade_id,
            "sell_date": str(ct.sell_date) if ct.sell_date else None,
            "sell_price": ct.sell_price,
            "sell_quantity": ct.sell_quantity,
            "sell_reason": ct.sell_reason,
            "holding_days": ct.holding_days,
            "pnl": ct.pnl,
            "pnl_pct": ct.pnl_pct,
            "total_commission": ct.total_commission,
            "benchmark_return_pct": ct.benchmark_return_pct,
            "excess_return_pct": ct.excess_return_pct,
            "created_at": str(ct.created_at) if ct.created_at else None,
        }

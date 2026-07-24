"""
信号追踪器 — 追踪每个信号的实际市场表现
"""
from typing import Dict, List, Optional
from datetime import datetime, date, timedelta
from sqlalchemy import func, and_, text
from loguru import logger
from common.market_time import utc_now

from common.market import A_SHARE
from common.market_time import market_today

from data_engine.storage.database import get_session
from data_engine.storage.models import Signal, SignalTracking, DailyQuote, StockInfo


class SignalTracker:
    """信号追踪器 — 追踪每个信号的实际市场表现"""

    RETURN_DAYS = [1, 3, 5, 10, 20]
    MAX_WINDOW = 20  # 最大追踪窗口

    def __init__(self):
        self.session = get_session()

    def close(self):
        """关闭数据库会话"""
        self.session.close()

    def update_all(self) -> Dict:
        """
        主入口：批量更新所有信号的追踪数据

        Returns:
            {"created": N, "updated": M, "completed": K}
        """
        try:
            created = self._create_pending_records()
            updated, completed = self._update_all_tracking()
            self.session.commit()
            logger.info(f"信号追踪更新完成: created={created}, updated={updated}, completed={completed}")
            return {"created": created, "updated": updated, "completed": completed}
        except Exception as e:
            self.session.rollback()
            logger.error(f"信号追踪更新失败: {e}")
            raise

    def _create_pending_records(self) -> int:
        """为没有追踪记录的信号创建 pending 记录"""
        # 查找没有追踪记录的信号
        existing_ids = self.session.query(SignalTracking.signal_id).subquery()
        missing_signals = self.session.query(Signal).filter(
            ~Signal.signal_id.in_(self.session.query(existing_ids.c.signal_id))
        ).all()

        count = 0
        for signal in missing_signals:
            tracking = SignalTracking(
                signal_id=signal.signal_id,
                symbol=signal.symbol,
                strategy=signal.strategy,
                signal_type=signal.signal_type,
                signal_date=signal.date,
                signal_price=signal.price,
                stop_loss=signal.stop_loss,
                take_profit=signal.take_profit,
                tracking_status='pending',
                tracked_days=0,
            )
            self.session.add(tracking)
            count += 1

        if count > 0:
            self.session.flush()
            logger.info(f"创建了 {count} 条 pending 追踪记录")

        return count

    def _update_all_tracking(self) -> tuple:
        """更新所有非 completed 的追踪记录（批量加载行情，避免 N+1 查询）"""
        from collections import defaultdict
        from sqlalchemy import distinct

        records = self.session.query(SignalTracking).filter(
            SignalTracking.tracking_status != 'completed'
        ).all()

        if not records:
            return 0, 0

        # 用子查询获取所有涉及的 symbol（避免大 IN list 超出 SQLite 变量限制）
        # 使用 select() 构造以符合 SQLAlchemy 2.x IN 子查询规范
        from sqlalchemy import select
        symbols_select = (
            select(distinct(SignalTracking.symbol))
            .where(SignalTracking.tracking_status != 'completed')
        )
        min_date = min(r.signal_date for r in records)

        all_quotes = (
            self.session.query(DailyQuote)
            .filter(
                DailyQuote.symbol.in_(symbols_select),
                DailyQuote.date > min_date,
            )
            .order_by(DailyQuote.symbol.asc(), DailyQuote.date.asc())
            .all()
        )

        # 按 symbol 分组（已按 symbol+date 排序，直接 append）
        quotes_by_symbol: dict = defaultdict(list)
        for q in all_quotes:
            quotes_by_symbol[q.symbol].append(q)

        updated = 0
        completed = 0
        for record in records:
            sym_quotes = quotes_by_symbol.get(record.symbol, [])
            # 过滤到信号日期之后的数据，最多取 MAX_WINDOW 条
            relevant_quotes = [q for q in sym_quotes if q.date > record.signal_date][:self.MAX_WINDOW]
            changed = self._update_tracking(record, relevant_quotes)
            if changed:
                updated += 1
                if record.tracking_status == 'completed':
                    completed += 1

        return updated, completed

    def _update_tracking(self, tracking: SignalTracking, quotes: list) -> bool:
        """
        更新单条追踪记录

        Args:
            tracking: 追踪记录
            quotes: 信号日期之后的行情列表（已过滤+截断到 MAX_WINDOW）

        Returns:
            是否有数据变更
        """
        if not quotes:
            tracking.tracking_status = 'pending'
            tracking.tracked_days = 0
            return False

        signal_price = tracking.signal_price
        if signal_price is None or signal_price <= 0:
            return False

        is_buy = tracking.signal_type == 'BUY'
        n_days = len(quotes)
        tracking.tracked_days = n_days

        # 计算 N 日收益率
        day_map = {1: 'return_1d', 3: 'return_3d', 5: 'return_5d', 10: 'return_10d', 20: 'return_20d'}
        for n, attr in day_map.items():
            if n_days >= n:
                close_price = quotes[n - 1].close
                if is_buy:
                    ret = (close_price / signal_price - 1) * 100
                else:
                    ret = (1 - close_price / signal_price) * 100
                setattr(tracking, attr, round(ret, 2))
            else:
                setattr(tracking, attr, None)

        # 计算极值统计（MFE 和 MAE）
        window = quotes[:self.MAX_WINDOW]
        if is_buy:
            gains = [(q.high / signal_price - 1) * 100 for q in window]
            losses = [(q.low / signal_price - 1) * 100 for q in window]
        else:
            gains = [(1 - q.low / signal_price) * 100 for q in window]
            losses = [(1 - q.high / signal_price) * 100 for q in window]

        if gains:
            max_gain = max(gains)
            if max_gain > 0:
                # 价格曾有利于持仓方向，记录最大浮盈及出现日
                tracking.max_gain = round(max_gain, 2)
                tracking.max_gain_day = gains.index(max_gain) + 1
            else:
                # 价格从未朝有利方向运动，浮盈记为 0
                tracking.max_gain = 0.0
                tracking.max_gain_day = None

        if losses:
            max_loss = min(losses)
            if max_loss < 0:
                # 价格曾逆势运动，记录最大浮亏（负值）及出现日
                tracking.max_loss = round(max_loss, 2)
                tracking.max_loss_day = losses.index(max_loss) + 1
            else:
                # 价格从未逆势运动，浮亏记为 0
                tracking.max_loss = 0.0
                tracking.max_loss_day = None

        # 止损止盈追踪
        tracking.hit_stop_loss = 0
        tracking.hit_take_profit = 0
        tracking.days_to_stop = None
        tracking.days_to_target = None

        for i, q in enumerate(window):
            day_num = i + 1
            if tracking.stop_loss is not None and not tracking.hit_stop_loss:
                if is_buy and q.low <= tracking.stop_loss:
                    tracking.hit_stop_loss = 1
                    tracking.days_to_stop = day_num
                elif not is_buy and q.high >= tracking.stop_loss:
                    tracking.hit_stop_loss = 1
                    tracking.days_to_stop = day_num

            if tracking.take_profit is not None and not tracking.hit_take_profit:
                if is_buy and q.high >= tracking.take_profit:
                    tracking.hit_take_profit = 1
                    tracking.days_to_target = day_num
                elif not is_buy and q.low <= tracking.take_profit:
                    tracking.hit_take_profit = 1
                    tracking.days_to_target = day_num

        # 判定 outcome（基于 return_10d）
        tracking.outcome = None
        if tracking.return_10d is not None:
            if tracking.return_10d > 1:
                tracking.outcome = 'win'
            elif tracking.return_10d < -1:
                tracking.outcome = 'loss'
            else:
                tracking.outcome = 'neutral'

        # 更新 tracking_status
        if n_days >= self.MAX_WINDOW:
            tracking.tracking_status = 'completed'
        elif n_days > 0:
            tracking.tracking_status = 'tracking'
        else:
            tracking.tracking_status = 'pending'

        tracking.updated_at = utc_now()
        return True

    def get_strategy_stats(self, strategy: Optional[str] = None, days: Optional[int] = None) -> Dict:
        """
        按策略聚合统计（SQL 聚合，不加载全部记录到内存）

        Args:
            strategy: 策略名称筛选
            days: 只统计最近 N 天的信号
        """
        from sqlalchemy import case as sql_case  # func / and_ 已在模块级导入

        filters = []
        if strategy:
            filters.append(SignalTracking.strategy == strategy)
        if days:
            cutoff = market_today(A_SHARE) - timedelta(days=days)
            filters.append(SignalTracking.signal_date >= cutoff)

        def _agg_cols(with_strategy: bool):
            cols = [
                func.count(SignalTracking.id).label('total'),
                func.sum(sql_case((SignalTracking.tracking_status != 'pending', 1), else_=0)).label('tracked'),
                func.sum(sql_case((SignalTracking.tracking_status == 'completed', 1), else_=0)).label('completed'),
                func.sum(sql_case((SignalTracking.outcome == 'win', 1), else_=0)).label('win'),
                func.sum(sql_case((SignalTracking.outcome == 'loss', 1), else_=0)).label('loss'),
                func.sum(sql_case((SignalTracking.outcome == 'neutral', 1), else_=0)).label('neutral'),
                func.avg(SignalTracking.return_5d).label('avg_return_5d'),
                func.avg(SignalTracking.return_10d).label('avg_return_10d'),
                func.avg(SignalTracking.max_gain).label('avg_max_gain'),
                func.avg(SignalTracking.max_loss).label('avg_max_loss'),
                func.sum(sql_case((
                    and_(
                        SignalTracking.stop_loss.isnot(None),
                        SignalTracking.tracked_days > 0,
                        SignalTracking.hit_stop_loss == 1,
                    ), 1), else_=0)).label('sl_hit'),
                func.sum(sql_case((
                    and_(
                        SignalTracking.stop_loss.isnot(None),
                        SignalTracking.tracked_days > 0,
                    ), 1), else_=0)).label('sl_total'),
                func.sum(sql_case((
                    and_(
                        SignalTracking.take_profit.isnot(None),
                        SignalTracking.tracked_days > 0,
                        SignalTracking.hit_take_profit == 1,
                    ), 1), else_=0)).label('tp_hit'),
                func.sum(sql_case((
                    and_(
                        SignalTracking.take_profit.isnot(None),
                        SignalTracking.tracked_days > 0,
                    ), 1), else_=0)).label('tp_total'),
            ]
            if with_strategy:
                cols = [SignalTracking.strategy] + cols
            return cols

        def _row_to_metrics(row) -> Dict:
            judged = (row.win or 0) + (row.loss or 0) + (row.neutral or 0)
            win_rate = round((row.win or 0) / judged * 100, 1) if judged > 0 else 0.0
            sl_total = row.sl_total or 0
            tp_total = row.tp_total or 0
            return {
                "total": int(row.total or 0),
                "tracked": int(row.tracked or 0),
                "completed": int(row.completed or 0),
                "judged": int(judged),  # 有 outcome 的信号数（win+loss+neutral）
                "win": int(row.win or 0),
                "loss": int(row.loss or 0),
                "neutral": int(row.neutral or 0),
                "win_rate": win_rate,
                "avg_return_5d": round(float(row.avg_return_5d), 2) if row.avg_return_5d is not None else None,
                "avg_return_10d": round(float(row.avg_return_10d), 2) if row.avg_return_10d is not None else None,
                "avg_max_gain": round(float(row.avg_max_gain), 2) if row.avg_max_gain is not None else None,
                "avg_max_loss": round(float(row.avg_max_loss), 2) if row.avg_max_loss is not None else None,
                "stop_loss_hit_rate": round((row.sl_hit or 0) / sl_total * 100, 1) if sl_total > 0 else 0.0,
                "take_profit_hit_rate": round((row.tp_hit or 0) / tp_total * 100, 1) if tp_total > 0 else 0.0,
            }

        # 整体聚合（不 GROUP BY）
        overall_q = self.session.query(*_agg_cols(with_strategy=False))
        for f in filters:
            overall_q = overall_q.filter(f)
        overall = _row_to_metrics(overall_q.one())

        # 按策略聚合（GROUP BY strategy）
        strat_q = self.session.query(*_agg_cols(with_strategy=True))
        for f in filters:
            strat_q = strat_q.filter(f)
        strat_q = strat_q.group_by(SignalTracking.strategy)
        by_strategy = {
            (row.strategy or 'unknown'): _row_to_metrics(row)
            for row in strat_q.all()
        }

        return {
            "overall": overall,
            "by_strategy": by_strategy,
        }

    def get_tracked_signals(
        self,
        strategy: Optional[str] = None,
        outcome: Optional[str] = None,
        signal_type: Optional[str] = None,
        tracking_status: Optional[str] = None,
        start_date: Optional[date] = None,
        end_date: Optional[date] = None,
        limit: int = 50,
        offset: int = 0,
    ) -> Dict:
        """
        查询追踪信号列表

        Args:
            tracking_status: 逗号分隔，如 "tracking,completed"

        Returns:
            {"signals": [...], "total": N}
        """
        query = self.session.query(SignalTracking)

        if strategy:
            query = query.filter(SignalTracking.strategy == strategy)
        if outcome:
            query = query.filter(SignalTracking.outcome == outcome)
        if signal_type:
            query = query.filter(SignalTracking.signal_type == signal_type)
        if tracking_status:
            statuses = [s.strip() for s in tracking_status.split(",") if s.strip()]
            if len(statuses) == 1:
                query = query.filter(SignalTracking.tracking_status == statuses[0])
            elif statuses:
                query = query.filter(SignalTracking.tracking_status.in_(statuses))
        if start_date:
            query = query.filter(SignalTracking.signal_date >= start_date)
        if end_date:
            query = query.filter(SignalTracking.signal_date <= end_date)

        total = query.count()
        records = query.order_by(
            SignalTracking.tracked_days.desc(),
            SignalTracking.signal_date.desc(),
            SignalTracking.id.desc(),
        ).offset(offset).limit(limit).all()

        # 批量查询股票名称
        symbols = list(set(r.symbol for r in records))
        name_map = {}
        if symbols:
            stock_infos = self.session.query(StockInfo).filter(
                StockInfo.symbol.in_(symbols)
            ).all()
            name_map = {s.symbol: s.name for s in stock_infos}

        signals = []
        for r in records:
            signals.append({
                "id": r.id,
                "signal_id": r.signal_id,
                "symbol": r.symbol,
                "name": name_map.get(r.symbol, r.symbol),
                "strategy": r.strategy,
                "signal_type": r.signal_type,
                "signal_date": str(r.signal_date),
                "signal_price": r.signal_price,
                "stop_loss": r.stop_loss,
                "take_profit": r.take_profit,
                "return_1d": r.return_1d,
                "return_3d": r.return_3d,
                "return_5d": r.return_5d,
                "return_10d": r.return_10d,
                "return_20d": r.return_20d,
                "max_gain": r.max_gain,
                "max_loss": r.max_loss,
                "max_gain_day": r.max_gain_day,
                "max_loss_day": r.max_loss_day,
                "hit_stop_loss": bool(r.hit_stop_loss),
                "hit_take_profit": bool(r.hit_take_profit),
                "days_to_stop": r.days_to_stop,
                "days_to_target": r.days_to_target,
                "tracking_status": r.tracking_status,
                "outcome": r.outcome,
                "tracked_days": r.tracked_days,
            })

        return {"signals": signals, "total": total}

    def get_signal_detail(self, signal_id: str) -> Optional[Dict]:
        """获取单个信号的追踪详情"""
        r = self.session.query(SignalTracking).filter(
            SignalTracking.signal_id == signal_id
        ).first()

        if not r:
            return None

        # 查股票名称
        stock = self.session.query(StockInfo).filter(
            StockInfo.symbol == r.symbol
        ).first()
        name = stock.name if stock else r.symbol

        return {
            "id": r.id,
            "signal_id": r.signal_id,
            "symbol": r.symbol,
            "name": name,
            "strategy": r.strategy,
            "signal_type": r.signal_type,
            "signal_date": str(r.signal_date),
            "signal_price": r.signal_price,
            "stop_loss": r.stop_loss,
            "take_profit": r.take_profit,
            "return_1d": r.return_1d,
            "return_3d": r.return_3d,
            "return_5d": r.return_5d,
            "return_10d": r.return_10d,
            "return_20d": r.return_20d,
            "max_gain": r.max_gain,
            "max_loss": r.max_loss,
            "max_gain_day": r.max_gain_day,
            "max_loss_day": r.max_loss_day,
            "hit_stop_loss": bool(r.hit_stop_loss),
            "hit_take_profit": bool(r.hit_take_profit),
            "days_to_stop": r.days_to_stop,
            "days_to_target": r.days_to_target,
            "tracking_status": r.tracking_status,
            "outcome": r.outcome,
            "tracked_days": r.tracked_days,
            "created_at": r.created_at.isoformat() if r.created_at else None,
            "updated_at": r.updated_at.isoformat() if r.updated_at else None,
        }

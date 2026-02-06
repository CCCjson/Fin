"""
数据访问层 (Repository)
"""
from typing import List, Optional, Dict
from datetime import datetime, timedelta
from sqlalchemy.orm import Session
from sqlalchemy import and_, func, or_
import pandas as pd

from data_engine.storage.models import StockInfo, DailyQuote, RealtimeQuote, DataUpdateLog


class StockRepository:
    """股票信息仓库"""

    def __init__(self, session: Session):
        self.session = session

    def save_stock_info(self, stock_info: StockInfo) -> StockInfo:
        """保存股票信息（如果存在则更新）"""
        existing = self.session.query(StockInfo).filter(
            StockInfo.symbol == stock_info.symbol
        ).first()

        if existing:
            # 更新现有记录
            for key, value in stock_info.__dict__.items():
                if not key.startswith('_') and key != 'symbol':
                    setattr(existing, key, value)
            self.session.commit()
            return existing
        else:
            # 插入新记录
            self.session.add(stock_info)
            self.session.commit()
            return stock_info

    def get_stock_info(self, symbol: str) -> Optional[StockInfo]:
        """获取股票信息"""
        return self.session.query(StockInfo).filter(
            StockInfo.symbol == symbol
        ).first()

    def get_stocks_by_market(self, market: str, is_active: bool = True) -> List[StockInfo]:
        """获取指定市场的所有股票"""
        query = self.session.query(StockInfo).filter(
            StockInfo.market == market
        )
        if is_active:
            query = query.filter(StockInfo.is_active == 1)
        return query.all()

    def search_stocks(self, keyword: str, limit: int = 20) -> List[StockInfo]:
        """搜索股票"""
        return self.session.query(StockInfo).filter(
            or_(
                StockInfo.symbol.like(f"%{keyword}%"),
                StockInfo.name.like(f"%{keyword}%")
            )
        ).limit(limit).all()


class QuoteRepository:
    """行情数据仓库"""

    def __init__(self, session: Session):
        self.session = session

    def save_daily_quotes(self, quotes: List[DailyQuote]) -> int:
        """批量保存日线数据（去重）"""
        saved_count = 0

        for quote in quotes:
            existing = self.session.query(DailyQuote).filter(
                and_(
                    DailyQuote.symbol == quote.symbol,
                    DailyQuote.date == quote.date
                )
            ).first()

            if existing:
                # 更新现有记录
                existing.open = quote.open
                existing.high = quote.high
                existing.low = quote.low
                existing.close = quote.close
                existing.volume = quote.volume
                existing.amount = quote.amount
                existing.turnover = quote.turnover
                existing.updated_at = datetime.now()
            else:
                # 插入新记录
                self.session.add(quote)
                saved_count += 1

        self.session.commit()
        return saved_count

    def get_daily_quotes(
        self,
        symbol: str,
        start_date: datetime,
        end_date: datetime
    ) -> List[DailyQuote]:
        """查询日线数据"""
        return self.session.query(DailyQuote).filter(
            and_(
                DailyQuote.symbol == symbol,
                DailyQuote.date >= start_date,
                DailyQuote.date <= end_date
            )
        ).order_by(DailyQuote.date).all()

    def get_daily_quotes_df(
        self,
        symbol: str,
        start_date: datetime,
        end_date: datetime
    ) -> pd.DataFrame:
        """查询日线数据并返回 DataFrame"""
        quotes = self.get_daily_quotes(symbol, start_date, end_date)

        if not quotes:
            return pd.DataFrame()

        data = [{
            'date': q.date,
            'open': q.open,
            'high': q.high,
            'low': q.low,
            'close': q.close,
            'volume': q.volume,
            'amount': q.amount,
            'turnover': q.turnover
        } for q in quotes]

        df = pd.DataFrame(data)
        df['date'] = pd.to_datetime(df['date'])
        df.set_index('date', inplace=True)
        return df

    def get_latest_date(self, symbol: str) -> Optional[datetime]:
        """获取最新数据日期（用于增量更新）"""
        result = self.session.query(func.max(DailyQuote.date)).filter(
            DailyQuote.symbol == symbol
        ).scalar()
        return result

    def get_missing_dates(
        self,
        symbol: str,
        start_date: datetime,
        end_date: datetime
    ) -> List[datetime]:
        """检测缺失的交易日"""
        existing_dates = self.session.query(DailyQuote.date).filter(
            and_(
                DailyQuote.symbol == symbol,
                DailyQuote.date >= start_date,
                DailyQuote.date <= end_date
            )
        ).all()

        existing_dates_set = {d[0] for d in existing_dates}

        # 生成所有工作日（简化版，实际应该使用交易日历）
        all_dates = []
        current = start_date
        while current <= end_date:
            if current.weekday() < 5:  # 周一到周五
                all_dates.append(current)
            current += timedelta(days=1)

        missing_dates = [d for d in all_dates if d.date() not in existing_dates_set]
        return missing_dates

    def save_realtime_quote(self, quote: RealtimeQuote) -> RealtimeQuote:
        """保存实时行情"""
        self.session.add(quote)
        self.session.commit()
        return quote

    def get_latest_realtime_quote(self, symbol: str) -> Optional[RealtimeQuote]:
        """获取最新实时行情"""
        return self.session.query(RealtimeQuote).filter(
            RealtimeQuote.symbol == symbol
        ).order_by(RealtimeQuote.timestamp.desc()).first()


class LogRepository:
    """日志仓库"""

    def __init__(self, session: Session):
        self.session = session

    def save_update_log(self, log: DataUpdateLog) -> DataUpdateLog:
        """保存更新日志"""
        self.session.add(log)
        self.session.commit()
        return log

    def get_recent_logs(self, limit: int = 50) -> List[DataUpdateLog]:
        """获取最近的更新日志"""
        return self.session.query(DataUpdateLog).order_by(
            DataUpdateLog.started_at.desc()
        ).limit(limit).all()

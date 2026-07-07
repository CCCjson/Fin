"""
数据访问层 (Repository)
"""
from typing import List, Optional, Dict
from datetime import datetime, timedelta
from sqlalchemy.orm import Session
from sqlalchemy import and_, func, or_
import pandas as pd

from data_engine.storage.models import (
    StockInfo, DailyQuote, RealtimeQuote, DataUpdateLog, FinancialData,
    Watchlist, PriceAlert, StockValuation,
)


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

    def get_names_for_symbols(self, symbols: List[str]) -> Dict[str, str]:
        """按 symbol 列表批量查名称。查不到的 symbol 不出现在返回结果里，调用方自行 fallback。"""
        if not symbols:
            return {}
        rows = self.session.query(StockInfo.symbol, StockInfo.name).filter(
            StockInfo.symbol.in_(list(set(symbols)))
        ).all()
        return {sym: name for sym, name in rows}


def get_stock_names(session: Session, symbols: List[str]) -> Dict[str, str]:
    """薄封装：不想显式持有 StockRepository 实例时直接调用。"""
    return StockRepository(session).get_names_for_symbols(symbols)


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


class FinancialRepository:
    """财务基本面数据仓库"""

    def __init__(self, session: Session):
        self.session = session

    def save_financial_data(self, symbol: str, df: pd.DataFrame, data_source: str = "sina") -> int:
        """
        批量保存财务数据（按 symbol+report_date 去重，已有则更新）

        Args:
            symbol: 股票代码
            df: 标准化后的 DataFrame（列名为英文）
            data_source: 数据来源标识

        Returns:
            新插入的记录数
        """
        saved_count = 0
        field_names = {c.name for c in FinancialData.__table__.columns} - {"id", "created_at", "updated_at", "symbol", "report_date", "data_source"}

        for _, row in df.iterrows():
            report_date = row["report_date"].date() if hasattr(row["report_date"], "date") else row["report_date"]

            existing = self.session.query(FinancialData).filter(
                and_(
                    FinancialData.symbol == symbol,
                    FinancialData.report_date == report_date,
                )
            ).first()

            if existing:
                # 更新已有记录
                for col in field_names:
                    if col in row.index and pd.notna(row[col]):
                        setattr(existing, col, float(row[col]))
                existing.updated_at = datetime.now()
            else:
                # 新插入
                record = FinancialData(
                    symbol=symbol,
                    report_date=report_date,
                    data_source=data_source,
                )
                for col in field_names:
                    if col in row.index and pd.notna(row[col]):
                        setattr(record, col, float(row[col]))
                self.session.add(record)
                saved_count += 1

        self.session.commit()
        return saved_count

    def get_financial_data(
        self,
        symbol: str,
        start_date: Optional[datetime] = None,
        end_date: Optional[datetime] = None,
        limit: int = 0,
    ) -> List[FinancialData]:
        """查询财务数据记录"""
        query = self.session.query(FinancialData).filter(
            FinancialData.symbol == symbol
        )
        if start_date:
            query = query.filter(FinancialData.report_date >= start_date)
        if end_date:
            query = query.filter(FinancialData.report_date <= end_date)
        query = query.order_by(FinancialData.report_date.desc())
        if limit > 0:
            query = query.limit(limit)
        return query.all()

    def get_financial_df(
        self,
        symbol: str,
        start_date: Optional[datetime] = None,
        end_date: Optional[datetime] = None,
    ) -> pd.DataFrame:
        """查询财务数据并返回 DataFrame"""
        records = self.get_financial_data(symbol, start_date, end_date)
        if not records:
            return pd.DataFrame()

        # 只取数值字段
        field_names = {c.name for c in FinancialData.__table__.columns} - {"id", "created_at", "updated_at", "data_source"}
        data = []
        for r in records:
            row = {}
            for col in field_names:
                row[col] = getattr(r, col, None)
            data.append(row)

        df = pd.DataFrame(data)
        df["report_date"] = pd.to_datetime(df["report_date"])
        df = df.sort_values("report_date", ascending=True).reset_index(drop=True)
        return df

    def get_latest_report_date(self, symbol: str) -> Optional[datetime]:
        """获取最新报告期（用于增量更新）"""
        result = self.session.query(func.max(FinancialData.report_date)).filter(
            FinancialData.symbol == symbol
        ).scalar()
        return result

    def get_symbols_with_data(self) -> List[str]:
        """获取所有有财务数据的股票代码"""
        results = self.session.query(FinancialData.symbol).distinct().all()
        return [r[0] for r in results]


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


class WatchlistRepository:
    """自选股仓库"""

    def __init__(self, session: Session):
        self.session = session

    def list_all(self) -> List[Watchlist]:
        return self.session.query(Watchlist).order_by(
            Watchlist.group_name, Watchlist.sort_order, Watchlist.id
        ).all()

    def list_groups(self) -> List[str]:
        rows = self.session.query(Watchlist.group_name).distinct().all()
        return [r[0] for r in rows]

    def add(self, symbol: str, name: Optional[str] = None,
            group_name: str = "默认分组", note: Optional[str] = None) -> Watchlist:
        """添加自选股（同分组+symbol 已存在则更新备注/名称）"""
        existing = self.session.query(Watchlist).filter(
            and_(Watchlist.group_name == group_name, Watchlist.symbol == symbol)
        ).first()
        if existing:
            if name is not None:
                existing.name = name
            if note is not None:
                existing.note = note
            self.session.commit()
            return existing
        record = Watchlist(symbol=symbol, name=name, group_name=group_name, note=note)
        self.session.add(record)
        self.session.commit()
        return record

    def update(self, item_id: int, **fields) -> Optional[Watchlist]:
        record = self.session.query(Watchlist).filter(Watchlist.id == item_id).first()
        if not record:
            return None
        for key in ("name", "group_name", "note", "sort_order"):
            if key in fields and fields[key] is not None:
                setattr(record, key, fields[key])
        self.session.commit()
        return record

    def delete(self, item_id: int) -> bool:
        record = self.session.query(Watchlist).filter(Watchlist.id == item_id).first()
        if not record:
            return False
        self.session.delete(record)
        self.session.commit()
        return True

    def symbols(self) -> List[str]:
        rows = self.session.query(Watchlist.symbol).distinct().all()
        return [r[0] for r in rows]


class PriceAlertRepository:
    """价格预警仓库"""

    def __init__(self, session: Session):
        self.session = session

    def list_all(self, status: Optional[str] = None) -> List[PriceAlert]:
        query = self.session.query(PriceAlert)
        if status:
            query = query.filter(PriceAlert.status == status)
        return query.order_by(PriceAlert.created_at.desc()).all()

    def get_active_alerts(self) -> List[PriceAlert]:
        return self.session.query(PriceAlert).filter(
            PriceAlert.status == "active"
        ).all()

    def add(self, alert_id: str, symbol: str, alert_type: str, threshold: float,
            name: Optional[str] = None, repeat: int = 0,
            message: Optional[str] = None) -> PriceAlert:
        record = PriceAlert(
            alert_id=alert_id, symbol=symbol, alert_type=alert_type,
            threshold=threshold, name=name, repeat=repeat, message=message,
        )
        self.session.add(record)
        self.session.commit()
        return record

    def update_status(self, alert_id: str, status: str) -> Optional[PriceAlert]:
        record = self.session.query(PriceAlert).filter(
            PriceAlert.alert_id == alert_id
        ).first()
        if not record:
            return None
        record.status = status
        self.session.commit()
        return record

    def delete(self, alert_id: str) -> bool:
        record = self.session.query(PriceAlert).filter(
            PriceAlert.alert_id == alert_id
        ).first()
        if not record:
            return False
        self.session.delete(record)
        self.session.commit()
        return True


class ValuationRepository:
    """估值快照仓库"""

    def __init__(self, session: Session):
        self.session = session

    _VAL_FIELDS = ("pe", "pe_ttm", "pb", "ps", "total_mv", "circ_mv", "dividend_yield")

    def upsert_snapshot(self, symbol: str, snapshot_date, values: Dict,
                        data_source: str = "eastmoney") -> bool:
        """按 symbol+snapshot_date upsert 一条估值快照。返回是否新插入"""
        existing = self.session.query(StockValuation).filter(
            and_(
                StockValuation.symbol == symbol,
                StockValuation.snapshot_date == snapshot_date,
            )
        ).first()
        if existing:
            for col in self._VAL_FIELDS:
                if values.get(col) is not None:
                    setattr(existing, col, values[col])
            existing.updated_at = datetime.now()
            self.session.commit()
            return False
        record = StockValuation(symbol=symbol, snapshot_date=snapshot_date, data_source=data_source)
        for col in self._VAL_FIELDS:
            if values.get(col) is not None:
                setattr(record, col, values[col])
        self.session.add(record)
        self.session.commit()
        return True

    def get_latest(self, symbol: str) -> Optional[StockValuation]:
        return self.session.query(StockValuation).filter(
            StockValuation.symbol == symbol
        ).order_by(StockValuation.snapshot_date.desc()).first()

    def get_all_latest(self) -> Dict[str, StockValuation]:
        """返回 {symbol: 最新估值记录}（取每个 symbol 的最新 snapshot_date）"""
        sub = self.session.query(
            StockValuation.symbol,
            func.max(StockValuation.snapshot_date).label("md"),
        ).group_by(StockValuation.symbol).subquery()
        rows = self.session.query(StockValuation).join(
            sub,
            and_(
                StockValuation.symbol == sub.c.symbol,
                StockValuation.snapshot_date == sub.c.md,
            ),
        ).all()
        return {r.symbol: r for r in rows}

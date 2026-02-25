"""
数据引擎主类
"""
from typing import List, Dict, Optional
from datetime import datetime, timedelta
import pandas as pd
from loguru import logger

from data_engine.fetchers import FetcherFactory, MarketDataRequest
from data_engine.storage import (
    get_session,
    StockInfo,
    DailyQuote,
    DataUpdateLog,
    StockRepository,
    QuoteRepository,
    LogRepository
)
from data_engine.processors import DataNormalizer, DataValidator, DataCleaner


class DataEngine:
    """数据引擎主类"""

    def __init__(self, config: Dict = None):
        self.config = config or {}
        self.session = get_session()

        # 初始化仓库
        self.stock_repo = StockRepository(self.session)
        self.quote_repo = QuoteRepository(self.session)
        self.log_repo = LogRepository(self.session)

    def get_daily_data(
        self,
        symbol: str,
        start_date: str,
        end_date: str,
        adjust: str = "",
        force_update: bool = False,
        db_only: bool = False
    ) -> pd.DataFrame:
        """
        获取日线数据（优先从数据库，不存在则从网络获取）

        Args:
            symbol: 股票代码
            start_date: 开始日期 (YYYY-MM-DD)
            end_date: 结束日期 (YYYY-MM-DD)
            adjust: 复权方式 (qfq/hfq/none)
            force_update: 是否强制从网络更新
            db_only: 仅查询数据库，不联网拉取数据

        Returns:
            DataFrame: 日线数据
        """
        start_dt = datetime.strptime(start_date, "%Y-%m-%d")
        end_dt = datetime.strptime(end_date, "%Y-%m-%d")

        logger.info(f"获取 {symbol} 的日线数据: {start_date} ~ {end_date}")

        # 仅 DB 模式：直接查库返回，不检查新鲜度、不联网
        if db_only:
            df = self.quote_repo.get_daily_quotes_df(symbol, start_dt, end_dt)
            logger.info(f"[db_only] 返回 {len(df)} 条数据")
            return df

        if force_update:
            logger.info("强制从网络获取数据...")
            df = self._fetch_and_save_daily_data(symbol, start_dt, end_dt, adjust)
            return df

        # 先从数据库获取
        df = self.quote_repo.get_daily_quotes_df(symbol, start_dt, end_dt)

        if df.empty:
            # 数据库没有任何数据，全量从网络获取
            logger.info("数据库无数据，从网络获取...")
            df = self._fetch_and_save_daily_data(symbol, start_dt, end_dt, adjust)
            return df

        # 检查数据是否够新：数据库最新日期 vs 请求的 end_date
        latest_in_db = self.quote_repo.get_latest_date(symbol)
        if latest_in_db and latest_in_db < end_dt.date():
            # 新鲜度容忍：如果差距 ≤ 3 天（覆盖周末/短假期），直接返回缓存
            gap_days = (end_dt.date() - latest_in_db).days
            if gap_days <= 3 and not force_update:
                logger.info(f"数据足够新（差 {gap_days} 天，≤3天容忍），跳过网络更新")
                return df

            # 数据库数据不够新，从最新日期的下一天开始补数据
            fetch_start = datetime.combine(latest_in_db + timedelta(days=1), datetime.min.time())
            logger.info(f"数据库最新: {latest_in_db}，补齐 {fetch_start.date()} ~ {end_dt.date()}")
            try:
                new_df = self._fetch_and_save_daily_data(symbol, fetch_start, end_dt, adjust)
                if not new_df.empty:
                    # 合并旧数据和新数据
                    df = pd.concat([df, new_df])
                    df = df[~df.index.duplicated(keep='last')]
                    df.sort_index(inplace=True)
                    logger.info(f"补齐后共 {len(df)} 条数据")
            except Exception as e:
                logger.warning(f"补齐数据失败（返回已有数据）: {e}")

        logger.info(f"返回 {len(df)} 条数据")
        return df

    def _fetch_and_save_daily_data(
        self,
        symbol: str,
        start_date: datetime,
        end_date: datetime,
        adjust: str = ""
    ) -> pd.DataFrame:
        """从网络获取数据并保存到数据库"""
        try:
            # 创建获取器
            fetcher = FetcherFactory.get_fetcher_for_symbol(symbol, self.config)

            # 获取数据
            request = MarketDataRequest(
                symbol=symbol,
                start_date=start_date,
                end_date=end_date,
                adjust=adjust
            )

            response = fetcher.fetch_daily(request)

            if response.data.empty:
                logger.warning(f"{symbol} 没有数据")
                return pd.DataFrame()

            # 数据处理
            df = response.data.copy()

            # 标准化
            df = DataNormalizer.normalize(df)

            # 验证
            is_valid, validation_results = DataValidator.validate(df)
            if not is_valid:
                logger.warning(f"数据验证失败: {validation_results['errors']}")

            # 清洗
            df = DataCleaner.clean(df, self.config.get("cleaner", {}))

            # 保存到数据库
            quotes = []
            for _, row in df.iterrows():
                quote = DailyQuote(
                    symbol=symbol,
                    market=response.market,
                    date=row["date"].date(),
                    open=float(row["open"]),
                    high=float(row["high"]),
                    low=float(row["low"]),
                    close=float(row["close"]),
                    volume=float(row["volume"]),
                    amount=float(row.get("amount", 0)) if pd.notna(row.get("amount")) else None,
                    turnover=float(row.get("turnover", 0)) if pd.notna(row.get("turnover")) else None
                )
                quotes.append(quote)

            saved_count = self.quote_repo.save_daily_quotes(quotes)
            logger.info(f"保存了 {saved_count} 条新数据到数据库")

            return df

        except Exception as e:
            logger.error(f"获取并保存数据失败: {e}")
            raise

    def update_stock_list(self, market: str = "a_share") -> int:
        """
        更新股票列表

        Args:
            market: 市场 (a_share/hk_stock/us_stock)

        Returns:
            更新的股票数量
        """
        logger.info(f"更新 {market} 股票列表...")

        try:
            # 创建获取器
            fetcher = FetcherFactory.create(market, self.config)

            # 获取股票列表
            if market == "a_share":
                stocks = fetcher.get_stock_list()
            else:
                logger.warning(f"{market} 暂不支持获取完整股票列表")
                return 0

            # 保存到数据库
            saved_count = 0
            for stock_data in stocks:
                stock_info = StockInfo(
                    symbol=stock_data["symbol"],
                    name=stock_data["name"],
                    market=stock_data["market"],
                    is_active=1
                )
                self.stock_repo.save_stock_info(stock_info)
                saved_count += 1

            logger.info(f"更新了 {saved_count} 只股票信息")
            return saved_count

        except Exception as e:
            logger.error(f"更新股票列表失败: {e}")
            raise

    def search_stocks(self, keyword: str, market: str = "a_share") -> List[Dict]:
        """
        搜索股票

        Args:
            keyword: 搜索关键字
            market: 市场

        Returns:
            股票列表
        """
        logger.info(f"搜索股票: {keyword} (市场: {market})")

        try:
            fetcher = FetcherFactory.create(market, self.config)
            results = fetcher.search_symbol(keyword)
            return results

        except Exception as e:
            logger.error(f"搜索股票失败: {e}")
            return []

    def get_realtime_quotes(self, symbols: List[str]) -> List[Dict]:
        """
        获取实时行情

        Args:
            symbols: 股票代码列表

        Returns:
            实时行情列表
        """
        logger.info(f"获取实时行情: {symbols}")

        try:
            if not symbols:
                return []

            # 按市场分组
            market_symbols = {}
            for symbol in symbols:
                if symbol.endswith(('.SH', '.SZ')):
                    market_symbols.setdefault('a_share', []).append(symbol)
                elif symbol.endswith('.HK'):
                    market_symbols.setdefault('hk_stock', []).append(symbol)
                else:
                    market_symbols.setdefault('us_stock', []).append(symbol)

            # 分市场获取
            all_quotes = []
            for market, syms in market_symbols.items():
                fetcher = FetcherFactory.create(market, self.config)
                quotes = fetcher.fetch_realtime(syms)
                all_quotes.extend(quotes)

            return all_quotes

        except Exception as e:
            logger.error(f"获取实时行情失败: {e}")
            return []

    def get_latest_date(self, symbol: str) -> Optional[datetime]:
        """获取最新数据日期"""
        return self.quote_repo.get_latest_date(symbol)

    def close(self):
        """关闭数据库连接"""
        self.session.close()

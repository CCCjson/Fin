"""
数据引擎主类
"""
from typing import List, Dict, Optional
from datetime import datetime, timedelta
import pandas as pd
from loguru import logger

from acquisition.markets import FetcherFactory, MarketDataRequest
from net import ProxyExhaustedError
from data_engine.storage import (
    get_session,
    StockInfo,
    DailyQuote,
    DataUpdateLog,
    FinancialData,
    StockRepository,
    QuoteRepository,
    FinancialRepository,
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
        self.financial_repo = FinancialRepository(self.session)
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

        # 检查头部覆盖：数据库最早日期 vs 请求的 start_date
        earliest_in_db = df.index.min().date() if not df.empty else None
        if earliest_in_db and earliest_in_db > start_dt.date():
            head_gap = (earliest_in_db - start_dt.date()).days
            if head_gap > 30:
                # 头部缺口超过 30 天，尝试回填历史数据
                head_end = datetime.combine(earliest_in_db - timedelta(days=1), datetime.min.time())
                logger.info(f"数据头部缺口: 请求 {start_dt.date()} 但最早只有 {earliest_in_db}，回填 {start_dt.date()} ~ {head_end.date()}")
                try:
                    head_df = self._fetch_and_save_daily_data(symbol, start_dt, head_end, adjust)
                    if not head_df.empty:
                        df = pd.concat([head_df, df])
                        df = df[~df.index.duplicated(keep='last')]
                        df.sort_index(inplace=True)
                        logger.info(f"头部回填后共 {len(df)} 条数据")
                except Exception as e:
                    logger.warning(f"头部回填失败（返回已有数据）: {e}")

        # 检查尾部新鲜度：数据库最新日期 vs 请求的 end_date
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
                start_date=start_date.strftime("%Y-%m-%d"),
                end_date=end_date.strftime("%Y-%m-%d"),
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
            stocks = fetcher.get_stock_list()

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
        """获取实时行情（只要行，不要状态）。

        老调用方的形状保持不变；要知道「谁没拿到、为什么」用
        `get_realtime_quotes_with_status`。
        """
        quotes, _ = self.get_realtime_quotes_with_status(symbols)
        return quotes

    def get_realtime_quotes_with_status(
        self, symbols: List[str]
    ) -> "tuple[List[Dict], Dict[str, str]]":
        """获取实时行情，**并如实上报每个市场的失败原因**。

        Returns:
            `(quotes, failures)` —— `failures` 是 `{market: reason}`，reason ∈
            `fetch_failed`（抓取真失败）/ `proxy_exhausted`（代理额度耗尽，
            一个请求都没发出去）。**拿到数据的市场不进 failures**。

        为什么拆成按市场分别 try（2026-07-17，P0-2）：此前整个多市场循环包在
        **一个** try 里，`except Exception: return []` —— 意味着**港股 yfinance
        抛个异常，就把 A 股已经拿到的行情全部丢弃**，调用方收到一个空列表，
        既不知道丢了什么也不知道为什么。而 `ProxyExhaustedError`（额度耗尽，
        是「一个请求都没发」）与普通抓取失败的区别，也在这里被抹平成同一个 `[]`。

        现在：一个市场炸不影响另一个市场的结果，失败原因原样上报给调用方。
        """
        if not symbols:
            return [], {}

        logger.info(f"获取实时行情: {symbols}")

        # 按市场分组
        market_symbols: Dict[str, List[str]] = {}
        for symbol in symbols:
            if symbol.endswith(('.SH', '.SZ')):
                market_symbols.setdefault('a_share', []).append(symbol)
            elif symbol.endswith('.HK'):
                market_symbols.setdefault('hk_stock', []).append(symbol)
            else:
                market_symbols.setdefault('us_stock', []).append(symbol)

        all_quotes: List[Dict] = []
        failures: Dict[str, str] = {}
        for market, syms in market_symbols.items():
            try:
                fetcher = FetcherFactory.create(market, self.config)
                all_quotes.extend(fetcher.fetch_realtime(syms))
            except ProxyExhaustedError as e:
                # 额度耗尽 ≠ 抓取失败：一个请求都没发出去，重试也没用（铁律：
                # 绝不降级直连）。这个区分此前在这里被 except Exception 抹平了。
                logger.warning(f"实时行情/{market}：代理耗尽，{len(syms)} 只未取到: {e}")
                failures[market] = "proxy_exhausted"
            except Exception as e:  # noqa: BLE001 — 一个市场炸不许拖垮其它市场
                logger.error(f"实时行情/{market} 失败（其它市场不受影响）: {e}")
                failures[market] = "fetch_failed"

        return all_quotes, failures

    def get_latest_date(self, symbol: str) -> Optional[datetime]:
        """获取最新数据日期"""
        return self.quote_repo.get_latest_date(symbol)

    def get_financial_data(
        self,
        symbol: str,
        start_year: str = "2015",
        force_update: bool = False,
        db_only: bool = False,
    ) -> pd.DataFrame:
        """
        获取财务基本面数据（优先从数据库，不存在则从 akshare 拉取）

        Args:
            symbol: 股票代码 (600519.SH)
            start_year: 起始年份
            force_update: 是否强制联网更新
            db_only: 仅查库不联网

        Returns:
            DataFrame: 财务数据，含 report_date, eps, roe, gross_margin 等
        """
        logger.info(f"获取 {symbol} 财务基本面数据 (start_year={start_year})")

        if db_only:
            df = self.financial_repo.get_financial_df(symbol)
            logger.info(f"[db_only] {symbol} 返回 {len(df)} 条财务数据")
            return df

        # 先查库
        if not force_update:
            df = self.financial_repo.get_financial_df(symbol)
            if not df.empty:
                latest = self.financial_repo.get_latest_report_date(symbol)
                # 如果最新数据距今不超过 120 天，认为足够新
                if latest and (datetime.now().date() - latest).days <= 120:
                    logger.info(f"{symbol} 财务数据足够新（最新报告期 {latest}），使用缓存")
                    return df

        # 联网拉取
        try:
            from acquisition.markets.financial import FinancialFetcher
            fetcher = FinancialFetcher()
            fresh_df = fetcher.fetch_financial_data(symbol, start_year=start_year)
            if fresh_df.empty:
                # 联网失败，返回库存数据
                return self.financial_repo.get_financial_df(symbol)

            # 保存到数据库
            saved = self.financial_repo.save_financial_data(symbol, fresh_df)
            logger.info(f"{symbol} 保存了 {saved} 条新财务数据")

            # 返回完整数据
            return self.financial_repo.get_financial_df(symbol)

        except Exception as e:
            logger.error(f"获取 {symbol} 财务数据失败: {e}")
            # 回退到库存
            return self.financial_repo.get_financial_df(symbol)

    def close(self):
        """关闭数据库连接"""
        self.session.close()

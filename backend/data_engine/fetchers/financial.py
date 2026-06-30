"""
财务基本面数据获取器 — 从 akshare (新浪财务分析指标) 拉取 A 股财务数据
"""
from typing import Dict, List, Optional
from datetime import datetime
import pandas as pd
from loguru import logger


class FinancialFetcher:
    """A 股财务数据获取器，基于 akshare 的新浪财务分析指标接口"""

    # akshare 列名 → 数据库字段名映射
    COLUMN_MAP = {
        "日期": "report_date",
        "摊薄每股收益(元)": "eps",
        "加权每股收益(元)": "eps_weighted",
        "净资产收益率(%)": "roe",
        "加权净资产收益率(%)": "roe_weighted",
        "总资产利润率(%)": "roa",
        "销售毛利率(%)": "gross_margin",
        "销售净利率(%)": "net_margin",
        "营业利润率(%)": "operating_margin",
        "每股净资产_调整后(元)": "bvps",
        "每股经营性现金流(元)": "ocfps",
        "每股资本公积金(元)": "capital_reserve_ps",
        "每股未分配利润(元)": "undistributed_ps",
        "主营业务收入增长率(%)": "revenue_yoy",
        "净利润增长率(%)": "net_profit_yoy",
        "净资产增长率(%)": "net_asset_yoy",
        "总资产增长率(%)": "total_asset_yoy",
        "流动比率": "current_ratio",
        "速动比率": "quick_ratio",
        "资产负债率(%)": "debt_ratio",
        "股东权益比率(%)": "equity_ratio",
        "存货周转率(次)": "inventory_turnover",
        "存货周转天数(天)": "inventory_turnover_days",
        "应收账款周转天数(天)": "receivable_turnover_days",
        "总资产周转率(次)": "total_asset_turnover",
        "成本费用利润率(%)": "cost_expense_ratio",
        "三项费用比重": "expense_ratio",
        "总资产(元)": "total_assets",
        "主营业务利润(元)": "revenue",
    }

    @staticmethod
    def _normalize_symbol(symbol: str) -> str:
        """
        将标准化 symbol (600519.SH) 转换为 akshare 需要的纯数字 (600519)
        """
        return symbol.split(".")[0]

    def fetch_financial_data(
        self,
        symbol: str,
        start_year: str = "2015",
    ) -> pd.DataFrame:
        """
        获取单只股票的历史财务指标数据

        Args:
            symbol: 股票代码，支持 600519.SH 或 600519
            start_year: 起始年份

        Returns:
            标准化后的 DataFrame，列名为英文
        """
        import akshare as ak

        pure_code = self._normalize_symbol(symbol)
        logger.info(f"正在获取 {symbol} 的财务数据 (start_year={start_year})...")

        try:
            raw = ak.stock_financial_analysis_indicator(
                symbol=pure_code,
                start_year=start_year,
            )
        except Exception as e:
            logger.error(f"获取 {symbol} 财务数据失败: {e}")
            return pd.DataFrame()

        if raw is None or raw.empty:
            logger.warning(f"{symbol} 无财务数据返回")
            return pd.DataFrame()

        # 只保留有映射的列
        available = {k: v for k, v in self.COLUMN_MAP.items() if k in raw.columns}
        df = raw[list(available.keys())].rename(columns=available)

        # 解析日期
        df["report_date"] = pd.to_datetime(df["report_date"], errors="coerce")
        df = df.dropna(subset=["report_date"])

        # 数值列转 float
        numeric_cols = [c for c in df.columns if c != "report_date"]
        for col in numeric_cols:
            df[col] = pd.to_numeric(df[col], errors="coerce")

        # 按日期降序排列（最新在前）
        df = df.sort_values("report_date", ascending=False).reset_index(drop=True)

        logger.info(f"{symbol} 获取到 {len(df)} 条财务数据 ({df['report_date'].min()} ~ {df['report_date'].max()})")
        return df

    def fetch_batch(
        self,
        symbols: List[str],
        start_year: str = "2015",
    ) -> Dict[str, pd.DataFrame]:
        """
        批量获取多只股票的财务数据

        Returns:
            {symbol: DataFrame} 字典
        """
        results = {}
        for i, sym in enumerate(symbols, 1):
            logger.info(f"[{i}/{len(symbols)}] 获取 {sym} 财务数据...")
            df = self.fetch_financial_data(sym, start_year=start_year)
            if not df.empty:
                results[sym] = df
        return results

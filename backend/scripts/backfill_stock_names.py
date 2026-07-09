"""
补全 StockInfo 表的股票代码→名称映射（全市场）。

用法:
    cd backend
    conda run -n quant python scripts/backfill_stock_names.py [a_share] [hk_stock] [us_stock]

不带参数时依次跑 a_share / hk_stock / us_stock 三个市场；
也可以只传其中几个市场名，比如只想先跑港股验证：
    conda run -n quant python scripts/backfill_stock_names.py hk_stock
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from loguru import logger

from data_engine.engine import DataEngine

DEFAULT_MARKETS = ["a_share", "hk_stock", "us_stock"]


def main():
    markets = sys.argv[1:] or DEFAULT_MARKETS
    engine = DataEngine()

    for market in markets:
        logger.info(f"===== 回填 {market} 股票名称 =====")
        try:
            count = engine.update_stock_list(market)
            logger.success(f"{market}: 更新 {count} 只股票")
        except Exception as e:
            logger.error(f"{market} 回填失败: {e}")


if __name__ == "__main__":
    main()

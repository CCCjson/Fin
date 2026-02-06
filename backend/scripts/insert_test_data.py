"""
为 688576.SH 插入测试数据
"""
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pandas as pd
import numpy as np
from datetime import datetime, timedelta
from sqlalchemy import create_engine, text
from loguru import logger

# 数据库配置
DATABASE_URL = "sqlite:///data/market.db"
engine = create_engine(DATABASE_URL)

def generate_test_data(symbol: str, start_date: str, end_date: str):
    """生成测试数据"""
    logger.info(f"生成 {symbol} 的测试数据: {start_date} ~ {end_date}")

    # 生成日期范围（只包含交易日，排除周末）
    dates = pd.date_range(start=start_date, end=end_date, freq='B')  # B = business day

    # 生成价格数据（模拟真实的价格波动）
    np.random.seed(42)  # 固定随机种子，确保可重复

    n = len(dates)
    base_price = 50.0  # 基础价格

    # 使用随机游走生成价格
    returns = np.random.normal(0.001, 0.02, n)  # 平均收益率 0.1%，波动率 2%
    close_prices = base_price * np.exp(np.cumsum(returns))

    # 生成 OHLC 数据
    data = []
    for i, date in enumerate(dates):
        close = close_prices[i]
        # 高低价在收盘价附近波动
        high = close * (1 + abs(np.random.normal(0, 0.01)))
        low = close * (1 - abs(np.random.normal(0, 0.01)))
        open_price = low + (high - low) * np.random.random()

        # 成交量
        volume = int(np.random.uniform(1000000, 5000000))
        amount = volume * close

        data.append({
            'symbol': symbol,
            'market': 'SH',  # 上交所
            'date': date.strftime('%Y-%m-%d'),
            'open': round(open_price, 2),
            'high': round(high, 2),
            'low': round(low, 2),
            'close': round(close, 2),
            'volume': volume,
            'amount': round(amount, 2),
            'turnover': round(np.random.uniform(0.5, 3.0), 2),
            'adjust_factor': 1.0
        })

    df = pd.DataFrame(data)
    logger.info(f"生成了 {len(df)} 条数据")
    return df

def insert_data(df: pd.DataFrame):
    """插入数据到数据库"""
    symbol = df['symbol'].iloc[0]

    # 先删除已存在的数据
    with engine.connect() as conn:
        conn.execute(text(f"DELETE FROM daily_quotes WHERE symbol = '{symbol}'"))
        conn.commit()
        logger.info(f"已删除 {symbol} 的旧数据")

    # 插入新数据
    df.to_sql('daily_quotes', engine, if_exists='append', index=False)
    logger.info(f"已插入 {len(df)} 条数据到 daily_quotes 表")

def main():
    symbol = "688576.SH"
    start_date = "2024-01-01"
    end_date = "2025-02-05"

    # 生成数据
    df = generate_test_data(symbol, start_date, end_date)

    # 插入数据库
    insert_data(df)

    logger.success(f"✅ {symbol} 测试数据插入完成！")
    logger.info(f"数据范围: {df['date'].min()} ~ {df['date'].max()}")
    logger.info(f"数据条数: {len(df)}")
    logger.info(f"价格范围: {df['close'].min():.2f} ~ {df['close'].max():.2f}")

if __name__ == "__main__":
    main()

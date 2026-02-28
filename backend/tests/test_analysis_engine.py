"""
测试分析引擎 - 技术指标、信号检测、形态识别
"""
import sys
from pathlib import Path
sys.path.append(str(Path(__file__).parent.parent))

from datetime import datetime, timedelta
from loguru import logger
import pandas as pd

# 配置日志
logger.remove()
logger.add(
    sys.stdout,
    format="<green>{time:HH:mm:ss}</green> | <level>{level: <8}</level> | <level>{message}</level>",
    level="INFO"
)

from data_engine import DataEngine, init_db
from analysis_engine import AnalysisEngine


def main():
    logger.info("\n" + "=" * 80)
    logger.info("测试分析引擎 - 688576.SH (N三叶草)")
    logger.info("=" * 80 + "\n")

    # 初始化
    try:
        init_db()
        data_engine = DataEngine()
        analysis_engine = AnalysisEngine()
    except Exception as e:
        logger.error(f"初始化失败: {e}")
        return

    symbol = "688576.SH"

    # 获取历史数据（最近3个月）
    logger.info("【步骤1】获取历史数据...")
    try:
        end_date = datetime.now()
        start_date = end_date - timedelta(days=90)

        df = data_engine.get_daily_data(
            symbol=symbol,
            start_date=start_date.strftime("%Y-%m-%d"),
            end_date=end_date.strftime("%Y-%m-%d")
        )

        if df.empty:
            logger.error("未获取到数据")
            return

        logger.success(f"✓ 成功获取 {len(df)} 条数据\n")

    except Exception as e:
        logger.error(f"获取数据失败: {e}")
        return

    # 测试1: 计算技术指标
    logger.info("\n【步骤2】计算技术指标...")
    logger.info("-" * 80)
    try:
        df_with_indicators = analysis_engine.add_indicators(df)

        logger.info("\n添加的指标列:")
        indicator_cols = [col for col in df_with_indicators.columns
                         if col not in ["open", "high", "low", "close", "volume", "turnover"]]

        logger.info(f"  趋势指标: ma5, ma10, ma20, ma60, ema12, ema26, macd, boll_upper/mid/lower, adx等")
        logger.info(f"  动量指标: rsi, kdj_k/d/j, cci, roc, willr")
        logger.info(f"  波动率指标: atr, keltner, donchian, hv")
        logger.info(f"  成交量指标: obv, volume_ma5/10/20, vwap, mfi")
        logger.info(f"\n共计算 {len(indicator_cols)} 个技术指标")

        # 显示最新数据
        logger.info("\n最新交易日数据:")
        latest = df_with_indicators.iloc[-1]
        logger.info(f"  日期: {latest.name}")
        logger.info(f"  收盘价: {latest['close']:.2f}")
        logger.info(f"  MA5: {latest.get('ma5', 0):.2f} | MA10: {latest.get('ma10', 0):.2f} | MA20: {latest.get('ma20', 0):.2f}")
        logger.info(f"  RSI: {latest.get('rsi', 0):.2f}")
        logger.info(f"  KDJ: K={latest.get('kdj_k', 0):.2f}, D={latest.get('kdj_d', 0):.2f}, J={latest.get('kdj_j', 0):.2f}")
        logger.info(f"  MACD: {latest.get('macd', 0):.2f} | DEA: {latest.get('macd_dea', 0):.2f}")
        logger.info(f"  布林带: 上轨={latest.get('boll_upper', 0):.2f}, 中轨={latest.get('boll_mid', 0):.2f}, 下轨={latest.get('boll_lower', 0):.2f}")

    except Exception as e:
        logger.error(f"计算指标失败: {e}")
        import traceback
        traceback.print_exc()
        return

    # 测试2: 检测交易信号
    logger.info("\n【步骤3】检测交易信号...")
    logger.info("-" * 80)
    try:
        signals = analysis_engine.detect_signals(symbol, df_with_indicators)

        if signals:
            logger.info("\n检测到的信号:")
            for signal in signals:
                signal_type = signal.signal_type.value.upper()
                emoji = "🔺" if signal_type == "BUY" else "🔻" if signal_type == "SELL" else "⏸"
                logger.info(f"  {emoji} {signal_type}: {signal.reason}")
                logger.info(f"     价格: {signal.price:.2f} | 强度: {signal.strength:.2f}")
                logger.info(f"     相关指标: {signal.indicators}")
        else:
            logger.info("当前无明显交易信号")

    except Exception as e:
        logger.error(f"检测信号失败: {e}")
        import traceback
        traceback.print_exc()

    # 测试3: 识别K线形态
    logger.info("\n【步骤4】识别K线形态...")
    logger.info("-" * 80)
    try:
        patterns = analysis_engine.detect_patterns(df_with_indicators)

        if patterns:
            logger.info("\n检测到的形态:")

            pattern_names_cn = {
                "doji": "十字星",
                "hammer": "锤子线",
                "shooting_star": "射击之星",
                "marubozu": "光头光脚",
                "engulfing_bullish": "看涨吞没",
                "engulfing_bearish": "看跌吞没",
                "harami_bullish": "看涨孕育",
                "harami_bearish": "看跌孕育",
                "morning_star": "晨星",
                "evening_star": "黄昏之星",
                "three_white_soldiers": "三个白武士",
                "three_black_crows": "三只乌鸦"
            }

            for pattern_name, indices in patterns.items():
                if indices:
                    cn_name = pattern_names_cn.get(pattern_name, pattern_name)
                    logger.info(f"  {cn_name}: 出现 {len(indices)} 次")

                    # 显示最近的一次
                    if indices:
                        latest_idx = indices[-1]
                        if isinstance(latest_idx, int) and latest_idx < len(df_with_indicators):
                            date = df_with_indicators.index[latest_idx]
                            logger.info(f"    最近出现: {date}")

        else:
            logger.info("未检测到特殊K线形态")

    except Exception as e:
        logger.error(f"识别形态失败: {e}")
        import traceback
        traceback.print_exc()

    # 测试4: 完整分析
    logger.info("\n【步骤5】运行完整分析流程...")
    logger.info("-" * 80)
    try:
        result = analysis_engine.analyze(
            symbol=symbol,
            df=df,
            detect_signals=True,
            detect_patterns=True
        )

        logger.info("\n分析结果摘要:")
        logger.info(f"  数据条数: {len(result['data'])}")
        logger.info(f"  指标列数: {len(result['data'].columns)}")
        logger.info(f"  信号数量: {len(result['signals'])}")

        pattern_count = sum(len(indices) for indices in result['patterns'].values())
        logger.info(f"  形态数量: {pattern_count}")

        # 生成交易建议
        logger.info("\n交易建议:")
        buy_signals = [s for s in result['signals'] if s.signal_type.value == 'buy']
        sell_signals = [s for s in result['signals'] if s.signal_type.value == 'sell']

        if buy_signals:
            avg_strength = sum(s.strength for s in buy_signals) / len(buy_signals)
            logger.info(f"  🔺 买入信号 {len(buy_signals)} 个，平均强度: {avg_strength:.2f}")
        if sell_signals:
            avg_strength = sum(s.strength for s in sell_signals) / len(sell_signals)
            logger.info(f"  🔻 卖出信号 {len(sell_signals)} 个，平均强度: {avg_strength:.2f}")

        if not buy_signals and not sell_signals:
            logger.info("  ⏸ 观望为主，等待明确信号")

    except Exception as e:
        logger.error(f"完整分析失败: {e}")
        import traceback
        traceback.print_exc()

    # 清理
    data_engine.close()

    logger.info("\n" + "=" * 80)
    logger.success("🎉 分析引擎测试完成！")
    logger.info("=" * 80)


if __name__ == "__main__":
    main()

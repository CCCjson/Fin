"""
策略回测代码批量生成器 - 第二批
生成 4000 个符合质量标准的量化回测代码文件（编号 1001-5000）
新增更多策略类型和变体以增加多样性
"""
import random
import os

OUTPUT_DIR = '/Users/cccjson/Desktop/Fin/code_dataset'
NUM_STRATEGIES = 5000
START_INDEX = 5001  # 从5001开始接着编号

# ========== 策略类型定义（扩展到25种）==========
STRATEGY_TYPES = [
    'dual_ma',            # 双均线
    'macd',               # MACD
    'bollinger',          # 布林带
    'rsi',                # RSI
    'channel',            # 通道突破
    'momentum',           # 动量
    'ma_rsi',             # 均线 + RSI 组合
    'macd_boll',          # MACD + 布林带组合
    'rsi_boll',           # RSI + 布林带组合
    'ma_macd',            # 均线 + MACD 组合
    'triple_ma',          # 三均线
    'keltner',            # 肯特纳通道
    'donchian_atr',       # 唐奇安通道 + ATR
    'momentum_ma',        # 动量 + 均线
    'rsi_channel',        # RSI + 通道突破
    'ema_cross',          # EMA交叉
    'vwap_revert',        # VWAP均值回归
    'obv_trend',          # OBV趋势跟踪
    'dual_rsi',           # 双周期RSI
    'macd_rsi',           # MACD + RSI组合
    'boll_channel',       # 布林带 + 通道突破
    'ma_volume',          # 均线 + 成交量
    'momentum_rsi',       # 动量 + RSI
    'ema_boll',           # EMA + 布林带
    'channel_ma',         # 通道 + 均线过滤
]

# ========== 止损止盈类型 ==========
STOPLOSS_TYPES = ['fixed_pct', 'atr_based', 'trailing', 'chandelier', 'percentage_of_atr']
TAKEPROFIT_TYPES = ['fixed_pct', 'atr_based', 'dynamic_ratio', 'risk_reward', 'trailing_tp']
POSITION_TYPES = ['fixed_frac', 'kelly_approx', 'volatility_scaled', 'equal_risk', 'max_drawdown_scaled']


def rand_short_window():
    return random.choice([3, 4, 5, 6, 7, 8, 9, 10, 12, 13, 15])

def rand_long_window():
    return random.choice([20, 22, 25, 26, 30, 35, 40, 45, 50, 55, 60, 75, 90, 100, 120, 150])

def rand_rsi_period():
    return random.choice([5, 6, 7, 8, 9, 10, 12, 14, 16, 18, 21, 24])

def rand_rsi_lower():
    return random.choice([15, 18, 20, 22, 25, 28, 30, 32, 35])

def rand_rsi_upper():
    return random.choice([65, 68, 70, 72, 75, 78, 80, 82, 85])

def rand_boll_window():
    return random.choice([10, 12, 15, 18, 20, 22, 25, 28, 30, 35, 40, 50])

def rand_boll_std():
    return random.choice([1.2, 1.3, 1.5, 1.6, 1.8, 1.9, 2.0, 2.1, 2.2, 2.3, 2.5, 2.8, 3.0])

def rand_macd_params():
    fast = random.choice([6, 8, 9, 10, 11, 12, 13, 15])
    slow = random.choice([18, 20, 22, 24, 26, 28, 30, 35])
    signal = random.choice([5, 6, 7, 8, 9, 10, 11, 12])
    return fast, slow, signal

def rand_channel_window():
    return random.choice([5, 8, 10, 12, 15, 18, 20, 22, 25, 28, 30, 35, 40, 45, 50, 55, 60])

def rand_momentum_window():
    return random.choice([3, 5, 7, 10, 12, 15, 20, 25, 30, 40, 60])

def rand_atr_period():
    return random.choice([7, 10, 12, 14, 16, 18, 20, 22, 25, 30])

def rand_commission():
    return random.choice([0.0002, 0.0003, 0.0004, 0.0005, 0.0006, 0.0007, 0.0008, 0.001, 0.0012, 0.0015])

def rand_stamp_tax():
    return random.choice([0.0005, 0.0008, 0.001, 0.0012])

def rand_stoploss_pct():
    return round(random.uniform(0.015, 0.12), 3)

def rand_takeprofit_pct():
    return round(random.uniform(0.03, 0.30), 3)

def rand_atr_multi():
    return round(random.uniform(0.8, 5.0), 1)

def rand_trailing_pct():
    return round(random.uniform(0.02, 0.15), 3)

def rand_position_frac():
    return round(random.uniform(0.05, 1.0), 2)

def rand_risk_free():
    return random.choice([0.015, 0.02, 0.025, 0.03, 0.035, 0.04])

def rand_ema_span():
    return random.choice([5, 8, 10, 12, 15, 20, 25, 30, 40, 50, 60])

def rand_volume_multi():
    return round(random.uniform(1.2, 3.5), 1)

def rand_risk_reward():
    return round(random.uniform(1.5, 4.0), 1)


def pick_two_sorted(pool):
    """从池中选两个不同的值，返回 (小, 大)，绝不死循环"""
    pair = random.sample(pool, 2)
    return min(pair), max(pair)


def pick_three_sorted(pool):
    """从池中选三个不同的值，返回 (小, 中, 大)"""
    triple = random.sample(pool, 3)
    triple.sort()
    return triple[0], triple[1], triple[2]


EMA_POOL = [5, 8, 10, 12, 15, 20, 25, 30, 40, 50, 60]
MA_SHORT_POOL = [3, 4, 5, 6, 7, 8, 9, 10, 12, 13, 15]
MA_LONG_POOL = [20, 22, 25, 26, 30, 35, 40, 45, 50, 55, 60, 75, 90, 100, 120, 150]
MA_ALL_POOL = sorted(set(MA_SHORT_POOL + MA_LONG_POOL))


# ========== 代码模板 ==========

def gen_header():
    return '''"""
自动生成的量化回测策略
"""
import pandas as pd
import numpy as np
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
import warnings
warnings.filterwarnings('ignore')
'''


def gen_params(strategy_type, params):
    lines = ["# ========== 参数设置 =========="]
    lines.append("DATA_PATH = 'stock_daily.csv'")
    for k, v in params.items():
        if isinstance(v, str):
            lines.append(f"{k} = '{v}'")
        elif isinstance(v, float):
            lines.append(f"{k} = {v}")
        else:
            lines.append(f"{k} = {v}")
    lines.append("")
    return '\n'.join(lines)


def gen_data_load():
    return """# ========== 数据加载 ==========
df = pd.read_csv(DATA_PATH, parse_dates=['date'], index_col='date')
df = df.sort_index()
df = df.dropna(subset=['open', 'high', 'low', 'close', 'volume'])
"""


# ==================== 指标计算模块 ====================

def gen_indicator_dual_ma(short_w, long_w):
    return f"""# ========== 计算均线指标 ==========
df['ma_short'] = df['close'].rolling({short_w}).mean()  # {short_w}日均线
df['ma_long'] = df['close'].rolling({long_w}).mean()    # {long_w}日均线
"""

def gen_indicator_ema_cross(short_span, long_span):
    return f"""# ========== 计算EMA指标 ==========
df['ema_short'] = df['close'].ewm(span={short_span}, adjust=False).mean()  # {short_span}日EMA
df['ema_long'] = df['close'].ewm(span={long_span}, adjust=False).mean()    # {long_span}日EMA
"""

def gen_indicator_triple_ma(short_w, mid_w, long_w):
    return f"""# ========== 计算三均线指标 ==========
df['ma_short'] = df['close'].rolling({short_w}).mean()  # {short_w}日均线
df['ma_mid'] = df['close'].rolling({mid_w}).mean()      # {mid_w}日均线
df['ma_long'] = df['close'].rolling({long_w}).mean()    # {long_w}日均线
"""

def gen_indicator_macd(fast, slow, signal):
    return f"""# ========== 计算 MACD 指标 ==========
ema_fast = df['close'].ewm(span={fast}, adjust=False).mean()
ema_slow = df['close'].ewm(span={slow}, adjust=False).mean()
df['dif'] = ema_fast - ema_slow
df['dea'] = df['dif'].ewm(span={signal}, adjust=False).mean()
df['macd_hist'] = 2 * (df['dif'] - df['dea'])
"""

def gen_indicator_boll(window, num_std):
    return f"""# ========== 计算布林带指标 ==========
df['boll_mid'] = df['close'].rolling({window}).mean()
df['boll_std'] = df['close'].rolling({window}).std()
df['boll_upper'] = df['boll_mid'] + {num_std} * df['boll_std']
df['boll_lower'] = df['boll_mid'] - {num_std} * df['boll_std']
"""

def gen_indicator_rsi(period):
    return f"""# ========== 计算 RSI 指标 ==========
delta = df['close'].diff()
gain = delta.where(delta > 0, 0.0)
loss = -delta.where(delta < 0, 0.0)
avg_gain = gain.rolling({period}).mean()
avg_loss = loss.rolling({period}).mean()
rs = avg_gain / avg_loss.replace(0, np.nan)
df['rsi'] = 100 - (100 / (1 + rs))
"""

def gen_indicator_dual_rsi(short_p, long_p):
    return f"""# ========== 计算双周期RSI指标 ==========
delta = df['close'].diff()
gain = delta.where(delta > 0, 0.0)
loss = -delta.where(delta < 0, 0.0)
# 短周期RSI
avg_gain_s = gain.rolling({short_p}).mean()
avg_loss_s = loss.rolling({short_p}).mean()
rs_s = avg_gain_s / avg_loss_s.replace(0, np.nan)
df['rsi_short'] = 100 - (100 / (1 + rs_s))
# 长周期RSI
avg_gain_l = gain.rolling({long_p}).mean()
avg_loss_l = loss.rolling({long_p}).mean()
rs_l = avg_gain_l / avg_loss_l.replace(0, np.nan)
df['rsi_long'] = 100 - (100 / (1 + rs_l))
"""

def gen_indicator_channel(window):
    return f"""# ========== 计算通道突破指标 ==========
df['channel_high'] = df['close'].shift(1).rolling({window}).max()  # 用昨日及之前数据
df['channel_low'] = df['close'].shift(1).rolling({window}).min()
"""

def gen_indicator_momentum(window):
    return f"""# ========== 计算动量指标 ==========
df['momentum'] = df['close'].pct_change({window})  # {window}日动量
df['momentum_ma'] = df['momentum'].rolling({window}).mean()
"""

def gen_indicator_atr(period):
    return f"""# ========== 计算 ATR 指标 ==========
tr1 = df['high'] - df['low']
tr2 = (df['high'] - df['close'].shift(1)).abs()
tr3 = (df['low'] - df['close'].shift(1)).abs()
df['tr'] = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)
df['atr'] = df['tr'].rolling({period}).mean()
"""

def gen_indicator_keltner(ma_window, atr_period, atr_multi):
    return f"""# ========== 计算肯特纳通道指标 ==========
df['kc_mid'] = df['close'].ewm(span={ma_window}, adjust=False).mean()
tr1 = df['high'] - df['low']
tr2 = (df['high'] - df['close'].shift(1)).abs()
tr3 = (df['low'] - df['close'].shift(1)).abs()
df['tr'] = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)
df['atr'] = df['tr'].rolling({atr_period}).mean()
df['kc_upper'] = df['kc_mid'] + {atr_multi} * df['atr']
df['kc_lower'] = df['kc_mid'] - {atr_multi} * df['atr']
"""

def gen_indicator_vwap(window):
    return f"""# ========== 计算VWAP指标 ==========
df['typical_price'] = (df['high'] + df['low'] + df['close']) / 3
df['tp_volume'] = df['typical_price'] * df['volume']
df['vwap'] = df['tp_volume'].rolling({window}).sum() / df['volume'].rolling({window}).sum()
df['vwap_std'] = df['typical_price'].rolling({window}).std()
df['vwap_upper'] = df['vwap'] + 2 * df['vwap_std']
df['vwap_lower'] = df['vwap'] - 2 * df['vwap_std']
"""

def gen_indicator_obv():
    return """# ========== 计算OBV指标 ==========
df['obv'] = (np.sign(df['close'].diff()) * df['volume']).fillna(0).cumsum()
df['obv_ema'] = df['obv'].ewm(span=20, adjust=False).mean()
df['obv_slope'] = df['obv_ema'].diff(5)  # 5日OBV斜率
"""

def gen_indicator_volume_ma(window):
    return f"""# ========== 计算成交量均线 ==========
df['vol_ma'] = df['volume'].rolling({window}).mean()  # {window}日成交量均线
df['vol_ratio'] = df['volume'] / df['vol_ma']  # 量比
"""


# ==================== 信号生成模块 ====================

def gen_signal_dual_ma():
    return """# ========== 信号生成 ==========
df['signal'] = 0
df.loc[df['ma_short'] > df['ma_long'], 'signal'] = 1   # 短均线在长均线上方，持仓
df.loc[df['ma_short'] <= df['ma_long'], 'signal'] = 0   # 短均线在长均线下方，空仓
"""

def gen_signal_ema_cross():
    return """# ========== 信号生成 ==========
df['signal'] = 0
df.loc[df['ema_short'] > df['ema_long'], 'signal'] = 1   # 短EMA在长EMA上方，持仓
df.loc[df['ema_short'] <= df['ema_long'], 'signal'] = 0   # 短EMA在长EMA下方，空仓
"""

def gen_signal_triple_ma():
    return """# ========== 信号生成 ==========
df['signal'] = 0
# 短均线 > 中均线 > 长均线，多头排列时持仓
df.loc[(df['ma_short'] > df['ma_mid']) & (df['ma_mid'] > df['ma_long']), 'signal'] = 1
"""

def gen_signal_macd():
    return """# ========== 信号生成 ==========
df['signal'] = 0
df.loc[df['dif'] > df['dea'], 'signal'] = 1   # DIF 在 DEA 上方，持仓
df.loc[df['dif'] <= df['dea'], 'signal'] = 0   # DIF 在 DEA 下方，空仓
"""

def gen_signal_macd_zero():
    return """# ========== 信号生成 ==========
df['signal'] = 0
# DIF 上穿 DEA 且在零轴上方
df.loc[(df['dif'] > df['dea']) & (df['dif'] > 0), 'signal'] = 1
"""

def gen_signal_macd_hist():
    return """# ========== 信号生成 ==========
df['signal'] = 0
# MACD柱状图由负转正时买入
df['hist_prev'] = df['macd_hist'].shift(1)
df.loc[(df['macd_hist'].shift(1) > 0) & (df['hist_prev'].shift(1) <= 0), 'signal'] = 1
# MACD柱状图由正转负时卖出
df.loc[(df['macd_hist'].shift(1) <= 0) & (df['hist_prev'].shift(1) > 0), 'signal'] = 0
df['signal'] = df['signal'].replace(0, np.nan)
df.loc[(df['macd_hist'].shift(1) <= 0) & (df['hist_prev'].shift(1) > 0), 'signal'] = 0
df['signal'] = df['signal'].ffill().fillna(0).astype(int)
"""

def gen_signal_boll_mean_revert():
    return """# ========== 信号生成 ==========
df['signal'] = 0
# 价格跌破下轨时买入（均值回归）
df.loc[df['close'].shift(1) < df['boll_lower'].shift(1), 'signal'] = 1
# 价格突破上轨时卖出
df.loc[df['close'].shift(1) > df['boll_upper'].shift(1), 'signal'] = 0
df['signal'] = df['signal'].replace(0, np.nan)
df.loc[df['close'].shift(1) > df['boll_upper'].shift(1), 'signal'] = 0
df['signal'] = df['signal'].ffill().fillna(0).astype(int)
"""

def gen_signal_boll_breakout():
    return """# ========== 信号生成 ==========
df['signal'] = 0
# 价格突破上轨时买入（趋势跟踪）
df.loc[df['close'].shift(1) > df['boll_upper'].shift(1), 'signal'] = 1
# 价格跌破中轨时卖出
df.loc[df['close'].shift(1) < df['boll_mid'].shift(1), 'signal'] = 0
df['signal'] = df['signal'].replace(0, np.nan)
df.loc[df['close'].shift(1) < df['boll_mid'].shift(1), 'signal'] = 0
df['signal'] = df['signal'].ffill().fillna(0).astype(int)
"""

def gen_signal_boll_squeeze():
    return """# ========== 信号生成 ==========
df['signal'] = 0
# 布林带收窄后价格突破上轨（波动率突破）
df['boll_width'] = (df['boll_upper'] - df['boll_lower']) / df['boll_mid']
df['boll_width_min'] = df['boll_width'].rolling(20).min()
squeeze = df['boll_width'].shift(1) < df['boll_width_min'].shift(1) * 1.1
df.loc[squeeze & (df['close'].shift(1) > df['boll_upper'].shift(1)), 'signal'] = 1
# 价格跌破中轨卖出
df.loc[df['close'].shift(1) < df['boll_mid'].shift(1), 'signal'] = 0
df['signal'] = df['signal'].replace(0, np.nan)
df.loc[df['close'].shift(1) < df['boll_mid'].shift(1), 'signal'] = 0
df['signal'] = df['signal'].ffill().fillna(0).astype(int)
"""

def gen_signal_rsi(lower, upper):
    return f"""# ========== 信号生成 ==========
df['signal'] = 0
df['rsi_prev'] = df['rsi'].shift(1)  # 用前一日RSI避免未来数据
# RSI 从超卖区域回升时买入
df.loc[df['rsi_prev'] < {lower}, 'signal'] = 1
# RSI 进入超买区域时卖出
df.loc[df['rsi_prev'] > {upper}, 'signal'] = 0
df['signal'] = df['signal'].replace(0, np.nan)
df.loc[df['rsi_prev'] > {upper}, 'signal'] = 0
df['signal'] = df['signal'].ffill().fillna(0).astype(int)
"""

def gen_signal_rsi_midline(lower, upper):
    return f"""# ========== 信号生成 ==========
df['signal'] = 0
df['rsi_prev'] = df['rsi'].shift(1)
# RSI上穿50中线时买入（趋势确认）
df.loc[(df['rsi_prev'] > 50) & (df['rsi'].shift(2) <= 50), 'signal'] = 1
# RSI下穿50中线 或 超买时卖出
df.loc[(df['rsi_prev'] < 50) | (df['rsi_prev'] > {upper}), 'signal'] = 0
df['signal'] = df['signal'].replace(0, np.nan)
df.loc[(df['rsi_prev'] < 50) | (df['rsi_prev'] > {upper}), 'signal'] = 0
df['signal'] = df['signal'].ffill().fillna(0).astype(int)
"""

def gen_signal_dual_rsi(lower, upper):
    return f"""# ========== 信号生成 ==========
df['signal'] = 0
# 短周期RSI超卖且长周期RSI在中性区间以上时买入
df.loc[(df['rsi_short'].shift(1) < {lower}) & (df['rsi_long'].shift(1) > 40), 'signal'] = 1
# 短周期RSI超买 或 长周期RSI走弱时卖出
df.loc[(df['rsi_short'].shift(1) > {upper}) | (df['rsi_long'].shift(1) < 35), 'signal'] = 0
df['signal'] = df['signal'].replace(0, np.nan)
df.loc[(df['rsi_short'].shift(1) > {upper}) | (df['rsi_long'].shift(1) < 35), 'signal'] = 0
df['signal'] = df['signal'].ffill().fillna(0).astype(int)
"""

def gen_signal_channel():
    return """# ========== 信号生成 ==========
df['signal'] = 0
# 昨日收盘价突破通道上沿，买入
df.loc[df['close'].shift(1) > df['channel_high'], 'signal'] = 1
# 昨日收盘价跌破通道下沿，卖出
df.loc[df['close'].shift(1) < df['channel_low'], 'signal'] = 0
df['signal'] = df['signal'].replace(0, np.nan)
df.loc[df['close'].shift(1) < df['channel_low'], 'signal'] = 0
df['signal'] = df['signal'].ffill().fillna(0).astype(int)
"""

def gen_signal_channel_midline():
    return """# ========== 信号生成 ==========
df['signal'] = 0
df['channel_mid'] = (df['channel_high'] + df['channel_low']) / 2
# 突破通道上沿买入
df.loc[df['close'].shift(1) > df['channel_high'], 'signal'] = 1
# 跌破通道中线卖出（更积极的退出）
df.loc[df['close'].shift(1) < df['channel_mid'].shift(1), 'signal'] = 0
df['signal'] = df['signal'].replace(0, np.nan)
df.loc[df['close'].shift(1) < df['channel_mid'].shift(1), 'signal'] = 0
df['signal'] = df['signal'].ffill().fillna(0).astype(int)
"""

def gen_signal_momentum():
    return """# ========== 信号生成 ==========
df['signal'] = 0
# 动量为正且高于动量均线时持仓
df.loc[(df['momentum'].shift(1) > 0) & (df['momentum'].shift(1) > df['momentum_ma'].shift(1)), 'signal'] = 1
"""

def gen_signal_momentum_accel():
    return """# ========== 信号生成 ==========
df['signal'] = 0
# 动量加速（动量的变化率为正）时持仓
df['momentum_delta'] = df['momentum'].diff()
df.loc[(df['momentum'].shift(1) > 0) & (df['momentum_delta'].shift(1) > 0), 'signal'] = 1
"""

def gen_signal_keltner():
    return """# ========== 信号生成 ==========
df['signal'] = 0
# 价格突破肯特纳通道上沿时买入
df.loc[df['close'].shift(1) > df['kc_upper'].shift(1), 'signal'] = 1
# 价格跌破中轨时卖出
df.loc[df['close'].shift(1) < df['kc_mid'].shift(1), 'signal'] = 0
df['signal'] = df['signal'].replace(0, np.nan)
df.loc[df['close'].shift(1) < df['kc_mid'].shift(1), 'signal'] = 0
df['signal'] = df['signal'].ffill().fillna(0).astype(int)
"""

def gen_signal_keltner_revert():
    return """# ========== 信号生成 ==========
df['signal'] = 0
# 价格触及肯特纳通道下沿时买入（均值回归）
df.loc[df['close'].shift(1) < df['kc_lower'].shift(1), 'signal'] = 1
# 价格回到中轨上方时卖出
df.loc[df['close'].shift(1) > df['kc_mid'].shift(1), 'signal'] = 0
df['signal'] = df['signal'].replace(0, np.nan)
df.loc[df['close'].shift(1) > df['kc_mid'].shift(1), 'signal'] = 0
df['signal'] = df['signal'].ffill().fillna(0).astype(int)
"""

def gen_signal_vwap_revert():
    return """# ========== 信号生成 ==========
df['signal'] = 0
# 价格跌破VWAP下轨时买入（均值回归）
df.loc[df['close'].shift(1) < df['vwap_lower'].shift(1), 'signal'] = 1
# 价格回到VWAP上方时卖出
df.loc[df['close'].shift(1) > df['vwap'].shift(1), 'signal'] = 0
df['signal'] = df['signal'].replace(0, np.nan)
df.loc[df['close'].shift(1) > df['vwap'].shift(1), 'signal'] = 0
df['signal'] = df['signal'].ffill().fillna(0).astype(int)
"""

def gen_signal_obv_trend():
    return """# ========== 信号生成 ==========
df['signal'] = 0
# OBV斜率为正且高于EMA时持仓
df.loc[(df['obv_slope'].shift(1) > 0) & (df['obv'].shift(1) > df['obv_ema'].shift(1)), 'signal'] = 1
"""

def gen_signal_ma_rsi(rsi_lower, rsi_upper):
    return f"""# ========== 信号生成 ==========
df['signal'] = 0
# 均线多头 + RSI 超卖回升时买入
df.loc[(df['ma_short'] > df['ma_long']) & (df['rsi'].shift(1) < {rsi_lower}), 'signal'] = 1
# 均线空头 或 RSI 超买时卖出
df.loc[(df['ma_short'] <= df['ma_long']) | (df['rsi'].shift(1) > {rsi_upper}), 'signal'] = 0
df['signal'] = df['signal'].replace(0, np.nan)
df.loc[(df['ma_short'] <= df['ma_long']) | (df['rsi'].shift(1) > {rsi_upper}), 'signal'] = 0
df['signal'] = df['signal'].ffill().fillna(0).astype(int)
"""

def gen_signal_macd_boll():
    return """# ========== 信号生成 ==========
df['signal'] = 0
# MACD 金叉 + 价格在布林中轨上方时买入
df.loc[(df['dif'] > df['dea']) & (df['close'].shift(1) > df['boll_mid'].shift(1)), 'signal'] = 1
# MACD 死叉 或 价格跌破布林下轨时卖出
df.loc[(df['dif'] <= df['dea']) | (df['close'].shift(1) < df['boll_lower'].shift(1)), 'signal'] = 0
df['signal'] = df['signal'].replace(0, np.nan)
df.loc[(df['dif'] <= df['dea']) | (df['close'].shift(1) < df['boll_lower'].shift(1)), 'signal'] = 0
df['signal'] = df['signal'].ffill().fillna(0).astype(int)
"""

def gen_signal_rsi_boll(rsi_lower, rsi_upper):
    return f"""# ========== 信号生成 ==========
df['signal'] = 0
# RSI 超卖 + 价格触及布林下轨时买入
df.loc[(df['rsi'].shift(1) < {rsi_lower}) & (df['close'].shift(1) < df['boll_lower'].shift(1)), 'signal'] = 1
# RSI 超买 或 价格突破布林上轨时卖出
df.loc[(df['rsi'].shift(1) > {rsi_upper}) | (df['close'].shift(1) > df['boll_upper'].shift(1)), 'signal'] = 0
df['signal'] = df['signal'].replace(0, np.nan)
df.loc[(df['rsi'].shift(1) > {rsi_upper}) | (df['close'].shift(1) > df['boll_upper'].shift(1)), 'signal'] = 0
df['signal'] = df['signal'].ffill().fillna(0).astype(int)
"""

def gen_signal_ma_macd():
    return """# ========== 信号生成 ==========
df['signal'] = 0
# 均线多头排列 + MACD 金叉时买入
df.loc[(df['ma_short'] > df['ma_long']) & (df['dif'] > df['dea']), 'signal'] = 1
# 均线死叉 或 MACD 死叉时卖出
df.loc[(df['ma_short'] <= df['ma_long']) | (df['dif'] <= df['dea']), 'signal'] = 0
"""

def gen_signal_donchian_atr():
    return """# ========== 信号生成 ==========
df['signal'] = 0
# 突破通道上沿买入
df.loc[df['close'].shift(1) > df['channel_high'], 'signal'] = 1
# 跌破通道下沿 或 ATR 放大暗示波动加剧时卖出
df['atr_ma'] = df['atr'].rolling(20).mean()
df.loc[(df['close'].shift(1) < df['channel_low']) | (df['atr'].shift(1) > 2.0 * df['atr_ma'].shift(1)), 'signal'] = 0
df['signal'] = df['signal'].replace(0, np.nan)
df.loc[(df['close'].shift(1) < df['channel_low']), 'signal'] = 0
df['signal'] = df['signal'].ffill().fillna(0).astype(int)
"""

def gen_signal_momentum_ma():
    return """# ========== 信号生成 ==========
df['signal'] = 0
# 动量为正 + 价格在均线上方时持仓
df.loc[(df['momentum'].shift(1) > 0) & (df['close'].shift(1) > df['ma_long'].shift(1)), 'signal'] = 1
"""

def gen_signal_rsi_channel(rsi_lower):
    return f"""# ========== 信号生成 ==========
df['signal'] = 0
# 价格突破通道上沿 + RSI 未超买时买入
df.loc[(df['close'].shift(1) > df['channel_high']) & (df['rsi'].shift(1) < 70), 'signal'] = 1
# 价格跌破通道下沿 或 RSI 超卖时卖出
df.loc[(df['close'].shift(1) < df['channel_low']) | (df['rsi'].shift(1) < {rsi_lower}), 'signal'] = 0
df['signal'] = df['signal'].replace(0, np.nan)
df.loc[(df['close'].shift(1) < df['channel_low']), 'signal'] = 0
df['signal'] = df['signal'].ffill().fillna(0).astype(int)
"""

def gen_signal_macd_rsi(rsi_lower, rsi_upper):
    return f"""# ========== 信号生成 ==========
df['signal'] = 0
# MACD金叉 + RSI不在超买区时买入
df.loc[(df['dif'] > df['dea']) & (df['rsi'].shift(1) < {rsi_upper}) & (df['rsi'].shift(1) > {rsi_lower}), 'signal'] = 1
# MACD死叉 或 RSI超买时卖出
df.loc[(df['dif'] <= df['dea']) | (df['rsi'].shift(1) > {rsi_upper}), 'signal'] = 0
df['signal'] = df['signal'].replace(0, np.nan)
df.loc[(df['dif'] <= df['dea']) | (df['rsi'].shift(1) > {rsi_upper}), 'signal'] = 0
df['signal'] = df['signal'].ffill().fillna(0).astype(int)
"""

def gen_signal_boll_channel():
    return """# ========== 信号生成 ==========
df['signal'] = 0
# 通道突破 + 价格在布林上轨上方（双重确认）
df.loc[(df['close'].shift(1) > df['channel_high']) & (df['close'].shift(1) > df['boll_upper'].shift(1)), 'signal'] = 1
# 跌破布林中轨 或 跌破通道下沿时卖出
df.loc[(df['close'].shift(1) < df['boll_mid'].shift(1)) | (df['close'].shift(1) < df['channel_low']), 'signal'] = 0
df['signal'] = df['signal'].replace(0, np.nan)
df.loc[(df['close'].shift(1) < df['boll_mid'].shift(1)), 'signal'] = 0
df['signal'] = df['signal'].ffill().fillna(0).astype(int)
"""

def gen_signal_ma_volume(vol_multi):
    return f"""# ========== 信号生成 ==========
df['signal'] = 0
# 均线多头 + 放量突破时买入
df.loc[(df['ma_short'] > df['ma_long']) & (df['vol_ratio'].shift(1) > {vol_multi}), 'signal'] = 1
# 均线空头 或 缩量时卖出
df.loc[(df['ma_short'] <= df['ma_long']), 'signal'] = 0
df['signal'] = df['signal'].replace(0, np.nan)
df.loc[(df['ma_short'] <= df['ma_long']), 'signal'] = 0
df['signal'] = df['signal'].ffill().fillna(0).astype(int)
"""

def gen_signal_momentum_rsi(rsi_lower):
    return f"""# ========== 信号生成 ==========
df['signal'] = 0
# 动量为正 + RSI未超买时持仓
df.loc[(df['momentum'].shift(1) > 0) & (df['rsi'].shift(1) > {rsi_lower}) & (df['rsi'].shift(1) < 75), 'signal'] = 1
"""

def gen_signal_ema_boll():
    return """# ========== 信号生成 ==========
df['signal'] = 0
# EMA多头 + 价格在布林中轨上方时买入
df.loc[(df['ema_short'] > df['ema_long']) & (df['close'].shift(1) > df['boll_mid'].shift(1)), 'signal'] = 1
# EMA空头 或 价格跌破布林下轨时卖出
df.loc[(df['ema_short'] <= df['ema_long']) | (df['close'].shift(1) < df['boll_lower'].shift(1)), 'signal'] = 0
df['signal'] = df['signal'].replace(0, np.nan)
df.loc[(df['ema_short'] <= df['ema_long']) | (df['close'].shift(1) < df['boll_lower'].shift(1)), 'signal'] = 0
df['signal'] = df['signal'].ffill().fillna(0).astype(int)
"""

def gen_signal_channel_ma():
    return """# ========== 信号生成 ==========
df['signal'] = 0
# 通道突破 + 均线多头过滤
df.loc[(df['close'].shift(1) > df['channel_high']) & (df['ma_short'] > df['ma_long']), 'signal'] = 1
# 跌破通道下沿 或 均线空头时卖出
df.loc[(df['close'].shift(1) < df['channel_low']) | (df['ma_short'] <= df['ma_long']), 'signal'] = 0
df['signal'] = df['signal'].replace(0, np.nan)
df.loc[(df['close'].shift(1) < df['channel_low']) | (df['ma_short'] <= df['ma_long']), 'signal'] = 0
df['signal'] = df['signal'].ffill().fillna(0).astype(int)
"""


# ==================== 止损模块 ====================

def gen_stoploss_fixed(pct):
    return f"""# ========== 固定百分比止损 ==========
STOPLOSS_PCT = {pct}
entry_price = np.nan
in_position = False

for i in range(1, len(df)):
    if df['signal'].iloc[i] == 1 and not in_position:
        entry_price = df['close'].iloc[i-1]  # 用前一日收盘价作为参考入场价
        in_position = True
    if in_position and df['signal'].iloc[i] == 1:
        current_low = df['low'].iloc[i-1]  # 用前一日最低价检查
        if current_low <= entry_price * (1 - STOPLOSS_PCT):
            df.iloc[i, df.columns.get_loc('signal')] = 0
            in_position = False
    if df['signal'].iloc[i] == 0:
        in_position = False
        entry_price = np.nan
"""

def gen_stoploss_atr(atr_multi):
    return f"""# ========== ATR 止损 ==========
ATR_SL_MULTI = {atr_multi}
entry_price = np.nan
in_position = False

for i in range(1, len(df)):
    if df['signal'].iloc[i] == 1 and not in_position:
        entry_price = df['close'].iloc[i-1]
        in_position = True
    if in_position and df['signal'].iloc[i] == 1:
        atr_val = df['atr'].iloc[i-1] if 'atr' in df.columns and pd.notna(df['atr'].iloc[i-1]) else 0
        stop_price = entry_price - ATR_SL_MULTI * atr_val
        if df['low'].iloc[i-1] <= stop_price and atr_val > 0:
            df.iloc[i, df.columns.get_loc('signal')] = 0
            in_position = False
    if df['signal'].iloc[i] == 0:
        in_position = False
        entry_price = np.nan
"""

def gen_stoploss_trailing(pct):
    return f"""# ========== 移动止损 ==========
TRAILING_PCT = {pct}
highest_since_entry = np.nan
in_position = False

for i in range(1, len(df)):
    if df['signal'].iloc[i] == 1 and not in_position:
        highest_since_entry = df['close'].iloc[i-1]
        in_position = True
    if in_position and df['signal'].iloc[i] == 1:
        highest_since_entry = max(highest_since_entry, df['high'].iloc[i-1])
        stop_price = highest_since_entry * (1 - TRAILING_PCT)
        if df['low'].iloc[i-1] <= stop_price:
            df.iloc[i, df.columns.get_loc('signal')] = 0
            in_position = False
    if df['signal'].iloc[i] == 0:
        in_position = False
        highest_since_entry = np.nan
"""

def gen_stoploss_chandelier(atr_multi):
    return f"""# ========== 吊灯止损（Chandelier Exit）==========
CHANDELIER_MULTI = {atr_multi}
highest_since_entry = np.nan
in_position = False

for i in range(1, len(df)):
    if df['signal'].iloc[i] == 1 and not in_position:
        highest_since_entry = df['high'].iloc[i-1]
        in_position = True
    if in_position and df['signal'].iloc[i] == 1:
        highest_since_entry = max(highest_since_entry, df['high'].iloc[i-1])
        atr_val = df['atr'].iloc[i-1] if 'atr' in df.columns and pd.notna(df['atr'].iloc[i-1]) else 0
        stop_price = highest_since_entry - CHANDELIER_MULTI * atr_val
        if df['low'].iloc[i-1] <= stop_price and atr_val > 0:
            df.iloc[i, df.columns.get_loc('signal')] = 0
            in_position = False
    if df['signal'].iloc[i] == 0:
        in_position = False
        highest_since_entry = np.nan
"""

def gen_stoploss_pct_atr(pct, atr_multi):
    return f"""# ========== 百分比与ATR取严止损 ==========
SL_PCT = {pct}
SL_ATR_MULTI = {atr_multi}
entry_price = np.nan
in_position = False

for i in range(1, len(df)):
    if df['signal'].iloc[i] == 1 and not in_position:
        entry_price = df['close'].iloc[i-1]
        in_position = True
    if in_position and df['signal'].iloc[i] == 1:
        atr_val = df['atr'].iloc[i-1] if 'atr' in df.columns and pd.notna(df['atr'].iloc[i-1]) else 0
        stop_pct = entry_price * (1 - SL_PCT)
        stop_atr = entry_price - SL_ATR_MULTI * atr_val if atr_val > 0 else 0
        stop_price = max(stop_pct, stop_atr)  # 取更严格的止损
        if df['low'].iloc[i-1] <= stop_price:
            df.iloc[i, df.columns.get_loc('signal')] = 0
            in_position = False
    if df['signal'].iloc[i] == 0:
        in_position = False
        entry_price = np.nan
"""


# ==================== 止盈模块 ====================

def gen_takeprofit_fixed(pct):
    return f"""# ========== 固定百分比止盈 ==========
TAKEPROFIT_PCT = {pct}
entry_price_tp = np.nan
in_pos_tp = False

for i in range(1, len(df)):
    if df['signal'].iloc[i] == 1 and not in_pos_tp:
        entry_price_tp = df['close'].iloc[i-1]
        in_pos_tp = True
    if in_pos_tp and df['signal'].iloc[i] == 1:
        if df['high'].iloc[i-1] >= entry_price_tp * (1 + TAKEPROFIT_PCT):
            df.iloc[i, df.columns.get_loc('signal')] = 0
            in_pos_tp = False
    if df['signal'].iloc[i] == 0:
        in_pos_tp = False
        entry_price_tp = np.nan
"""

def gen_takeprofit_atr(atr_multi):
    return f"""# ========== ATR 止盈 ==========
ATR_TP_MULTI = {atr_multi}
entry_price_tp = np.nan
in_pos_tp = False

for i in range(1, len(df)):
    if df['signal'].iloc[i] == 1 and not in_pos_tp:
        entry_price_tp = df['close'].iloc[i-1]
        in_pos_tp = True
    if in_pos_tp and df['signal'].iloc[i] == 1:
        atr_val = df['atr'].iloc[i-1] if 'atr' in df.columns and pd.notna(df['atr'].iloc[i-1]) else 0
        if atr_val > 0 and df['high'].iloc[i-1] >= entry_price_tp + ATR_TP_MULTI * atr_val:
            df.iloc[i, df.columns.get_loc('signal')] = 0
            in_pos_tp = False
    if df['signal'].iloc[i] == 0:
        in_pos_tp = False
        entry_price_tp = np.nan
"""

def gen_takeprofit_dynamic():
    return """# ========== 动态止盈（盈利回撤法）==========
PROFIT_PULLBACK = 0.4  # 盈利回撤40%时止盈
entry_price_tp = np.nan
max_profit = 0.0
in_pos_tp = False

for i in range(1, len(df)):
    if df['signal'].iloc[i] == 1 and not in_pos_tp:
        entry_price_tp = df['close'].iloc[i-1]
        max_profit = 0.0
        in_pos_tp = True
    if in_pos_tp and df['signal'].iloc[i] == 1:
        current_profit = (df['close'].iloc[i-1] - entry_price_tp) / entry_price_tp
        max_profit = max(max_profit, current_profit)
        if max_profit > 0.03 and current_profit < max_profit * (1 - PROFIT_PULLBACK):
            df.iloc[i, df.columns.get_loc('signal')] = 0
            in_pos_tp = False
    if df['signal'].iloc[i] == 0:
        in_pos_tp = False
        entry_price_tp = np.nan
        max_profit = 0.0
"""

def gen_takeprofit_risk_reward(rr_ratio):
    return f"""# ========== 风险收益比止盈 ==========
RR_RATIO = {rr_ratio}  # 风险收益比
entry_price_tp = np.nan
risk_amount = 0.0
in_pos_tp = False

for i in range(1, len(df)):
    if df['signal'].iloc[i] == 1 and not in_pos_tp:
        entry_price_tp = df['close'].iloc[i-1]
        # 用ATR估算风险
        atr_val = df['atr'].iloc[i-1] if 'atr' in df.columns and pd.notna(df['atr'].iloc[i-1]) else entry_price_tp * 0.02
        risk_amount = atr_val * 2
        in_pos_tp = True
    if in_pos_tp and df['signal'].iloc[i] == 1:
        profit = df['high'].iloc[i-1] - entry_price_tp
        if profit >= risk_amount * RR_RATIO:
            df.iloc[i, df.columns.get_loc('signal')] = 0
            in_pos_tp = False
    if df['signal'].iloc[i] == 0:
        in_pos_tp = False
        entry_price_tp = np.nan
        risk_amount = 0.0
"""

def gen_takeprofit_trailing(pct):
    return f"""# ========== 移动止盈 ==========
TP_TRAIL_PCT = {pct}
TP_ACTIVATION = 0.03  # 盈利超过3%后激活移动止盈
entry_price_tp = np.nan
highest_profit_price = np.nan
in_pos_tp = False
tp_activated = False

for i in range(1, len(df)):
    if df['signal'].iloc[i] == 1 and not in_pos_tp:
        entry_price_tp = df['close'].iloc[i-1]
        highest_profit_price = entry_price_tp
        in_pos_tp = True
        tp_activated = False
    if in_pos_tp and df['signal'].iloc[i] == 1:
        highest_profit_price = max(highest_profit_price, df['high'].iloc[i-1])
        profit_pct = (highest_profit_price - entry_price_tp) / entry_price_tp
        if profit_pct > TP_ACTIVATION:
            tp_activated = True
        if tp_activated:
            trail_stop = highest_profit_price * (1 - TP_TRAIL_PCT)
            if df['low'].iloc[i-1] <= trail_stop:
                df.iloc[i, df.columns.get_loc('signal')] = 0
                in_pos_tp = False
    if df['signal'].iloc[i] == 0:
        in_pos_tp = False
        entry_price_tp = np.nan
        highest_profit_price = np.nan
        tp_activated = False
"""


# ==================== 仓位管理模块 ====================

def gen_position_fixed(frac):
    return f"""# ========== 仓位管理（信号延迟 + 固定仓位）==========
POSITION_FRAC = {frac}
df['position'] = df['signal'].shift(1) * POSITION_FRAC  # 信号延迟一天，固定仓位比例
df['position'] = df['position'].fillna(0)
"""

def gen_position_kelly():
    return """# ========== 仓位管理（信号延迟 + Kelly近似）==========
df['pnl'] = df['close'].pct_change()
df['win_roll'] = df['pnl'].rolling(60).apply(lambda x: (x > 0).mean(), raw=True)
df['avg_win_roll'] = df['pnl'].rolling(60).apply(lambda x: x[x > 0].mean() if (x > 0).any() else 0.01, raw=True)
df['avg_loss_roll'] = df['pnl'].rolling(60).apply(lambda x: -x[x < 0].mean() if (x < 0).any() else 0.01, raw=True)
df['odds_roll'] = df['avg_win_roll'] / df['avg_loss_roll'].replace(0, 0.01)
df['kelly'] = (df['win_roll'] * df['odds_roll'] - (1 - df['win_roll'])) / df['odds_roll'].replace(0, 1)
df['kelly'] = df['kelly'].clip(0.05, 0.5)

df['position'] = df['signal'].shift(1) * df['kelly']
df['position'] = df['position'].fillna(0)
"""

def gen_position_vol_scaled():
    return """# ========== 仓位管理（信号延迟 + 波动率调仓）==========
TARGET_VOL = 0.15  # 目标年化波动率15%
df['realized_vol'] = df['close'].pct_change().rolling(20).std() * np.sqrt(252)
df['vol_scale'] = TARGET_VOL / df['realized_vol'].replace(0, np.nan)
df['vol_scale'] = df['vol_scale'].clip(0.1, 2.0).fillna(1.0)

df['position'] = df['signal'].shift(1) * df['vol_scale']
df['position'] = df['position'].fillna(0)
"""

def gen_position_equal_risk():
    return """# ========== 仓位管理（信号延迟 + 等风险仓位）==========
RISK_PER_TRADE = 0.02  # 每笔交易风险2%
df['atr_pct'] = df['atr'] / df['close'] if 'atr' in df.columns else df['close'].pct_change().rolling(14).std() * 2
df['risk_pos'] = RISK_PER_TRADE / df['atr_pct'].replace(0, np.nan)
df['risk_pos'] = df['risk_pos'].clip(0.05, 1.0).fillna(0.5)

df['position'] = df['signal'].shift(1) * df['risk_pos']
df['position'] = df['position'].fillna(0)
"""

def gen_position_maxdd_scaled():
    return """# ========== 仓位管理（信号延迟 + 最大回撤缩放）==========
# 根据近期回撤动态调整仓位
df['roll_nav'] = (1 + df['close'].pct_change()).cumprod()
df['roll_max'] = df['roll_nav'].rolling(60).max()
df['roll_dd'] = (df['roll_nav'] - df['roll_max']) / df['roll_max']
# 回撤越大仓位越小
df['dd_scale'] = 1.0 + df['roll_dd'] * 2  # 回撤10%时仓位降到80%
df['dd_scale'] = df['dd_scale'].clip(0.3, 1.0).fillna(1.0)

df['position'] = df['signal'].shift(1) * df['dd_scale']
df['position'] = df['position'].fillna(0)
"""


# ==================== 交易成本 + 收益计算 + 绩效 ====================

def gen_cost_and_returns():
    return """# ========== 交易成本 ==========
df['trade'] = df['position'].diff().abs()
df['cost'] = 0.0
df.loc[df['position'].diff() > 0, 'cost'] = COMMISSION             # 买入成本
df.loc[df['position'].diff() < 0, 'cost'] = COMMISSION + STAMP_TAX  # 卖出成本（含印花税）

# ========== 收益计算 ==========
df['daily_return'] = df['close'].pct_change()
df['strategy_return'] = df['position'] * df['daily_return'] - df['cost']
df['nav'] = (1 + df['strategy_return']).cumprod()
df['benchmark'] = (1 + df['daily_return']).cumprod()
"""

def gen_performance():
    return """# ========== 绩效评估 ==========
returns = df['strategy_return'].dropna()
trading_days = len(returns)

# 累计收益率
total_return = df['nav'].iloc[-1] - 1 if len(df['nav'].dropna()) > 0 else 0

# 年化收益率
years = trading_days / 252
annual_return = (1 + total_return) ** (1 / years) - 1 if years > 0 else 0

# 最大回撤
cummax = df['nav'].cummax()
drawdown = (df['nav'] - cummax) / cummax
max_drawdown = drawdown.min()
dd_end = drawdown.idxmin()
dd_start = df['nav'][:dd_end].idxmax() if dd_end is not None and dd_end == dd_end else None

# 夏普比率
excess_return = returns.mean() - RISK_FREE_RATE / 252
sharpe = excess_return / returns.std() * np.sqrt(252) if returns.std() > 0 else 0

# 胜率
winning_days = (returns[returns != 0] > 0).sum()
total_trading_days = (returns != 0).sum()
win_rate = winning_days / total_trading_days if total_trading_days > 0 else 0

# 盈亏比
avg_profit = returns[returns > 0].mean() if (returns > 0).any() else 0
avg_loss = abs(returns[returns < 0].mean()) if (returns < 0).any() else 1
profit_loss_ratio = avg_profit / avg_loss if avg_loss > 0 else 0

# 回撤曲线
df['drawdown'] = drawdown

print('========== 绩效报告 ==========')
print(f'累计收益率:   {total_return:.2%}')
print(f'年化收益率:   {annual_return:.2%}')
print(f'最大回撤:     {max_drawdown:.2%}')
if dd_start is not None and dd_end is not None:
    print(f'回撤区间:     {dd_start} ~ {dd_end}')
print(f'夏普比率:     {sharpe:.2f}')
print(f'胜率:         {win_rate:.2%}')
print(f'盈亏比:       {profit_loss_ratio:.2f}')
"""

def gen_visualization():
    return """# ========== 可视化 ==========
fig, (ax1, ax2) = plt.subplots(2, 1, figsize=(14, 8), sharex=True, gridspec_kw={'height_ratios': [3, 1]})

# 上图：策略净值 vs 基准
ax1.plot(df.index, df['nav'], label='策略净值', color='#2196F3', linewidth=1.5)
ax1.plot(df.index, df['benchmark'], label='买入持有基准', color='#FF9800', linewidth=1.0, alpha=0.7)
ax1.set_title('策略净值曲线', fontsize=14)
ax1.set_ylabel('净值')
ax1.legend(loc='upper left')
ax1.grid(True, alpha=0.3)

# 下图：回撤曲线
ax2.fill_between(df.index, df['drawdown'], 0, color='#F44336', alpha=0.3)
ax2.plot(df.index, df['drawdown'], color='#F44336', linewidth=0.8)
ax2.set_title('回撤曲线', fontsize=12)
ax2.set_ylabel('回撤')
ax2.set_xlabel('日期')
ax2.grid(True, alpha=0.3)

plt.tight_layout()
plt.savefig('backtest_result.png', dpi=100, bbox_inches='tight')
plt.close()
print('图表已保存: backtest_result.png')
"""


# ==================== 策略组装 ====================

def build_strategy(strategy_type, idx):
    code_parts = [gen_header()]
    params = {}
    needs_atr = False

    if strategy_type == 'dual_ma':
        sw = rand_short_window()
        lw = random.choice([x for x in MA_LONG_POOL if x > sw])
        params.update({'SHORT_WINDOW': sw, 'LONG_WINDOW': lw})
        indicators = gen_indicator_dual_ma(sw, lw)
        signal = gen_signal_dual_ma()

    elif strategy_type == 'ema_cross':
        ss, ls = pick_two_sorted(EMA_POOL)
        params.update({'EMA_SHORT': ss, 'EMA_LONG': ls})
        indicators = gen_indicator_ema_cross(ss, ls)
        signal = gen_signal_ema_cross()

    elif strategy_type == 'triple_ma':
        sw, mw, lw = pick_three_sorted(MA_ALL_POOL)
        params.update({'SHORT_WINDOW': sw, 'MID_WINDOW': mw, 'LONG_WINDOW': lw})
        indicators = gen_indicator_triple_ma(sw, mw, lw)
        signal = gen_signal_triple_ma()

    elif strategy_type == 'macd':
        fast, slow, sig = rand_macd_params()
        params.update({'MACD_FAST': fast, 'MACD_SLOW': slow, 'MACD_SIGNAL': sig})
        indicators = gen_indicator_macd(fast, slow, sig)
        signal = random.choice([gen_signal_macd(), gen_signal_macd_zero(), gen_signal_macd_hist()])

    elif strategy_type == 'bollinger':
        bw, bs = rand_boll_window(), rand_boll_std()
        params.update({'BOLL_WINDOW': bw, 'BOLL_STD': bs})
        indicators = gen_indicator_boll(bw, bs)
        signal = random.choice([gen_signal_boll_mean_revert(), gen_signal_boll_breakout(), gen_signal_boll_squeeze()])

    elif strategy_type == 'rsi':
        rp = rand_rsi_period()
        rl, ru = rand_rsi_lower(), rand_rsi_upper()
        params.update({'RSI_PERIOD': rp, 'RSI_LOWER': rl, 'RSI_UPPER': ru})
        indicators = gen_indicator_rsi(rp)
        signal = random.choice([gen_signal_rsi(rl, ru), gen_signal_rsi_midline(rl, ru)])

    elif strategy_type == 'dual_rsi':
        sp = random.choice([5, 6, 7, 8])
        lp = random.choice([14, 18, 21, 24])
        rl, ru = rand_rsi_lower(), rand_rsi_upper()
        params.update({'RSI_SHORT': sp, 'RSI_LONG': lp, 'RSI_LOWER': rl, 'RSI_UPPER': ru})
        indicators = gen_indicator_dual_rsi(sp, lp)
        signal = gen_signal_dual_rsi(rl, ru)

    elif strategy_type == 'channel':
        cw = rand_channel_window()
        params.update({'CHANNEL_WINDOW': cw})
        indicators = gen_indicator_channel(cw)
        signal = random.choice([gen_signal_channel(), gen_signal_channel_midline()])

    elif strategy_type == 'momentum':
        mw = rand_momentum_window()
        params.update({'MOMENTUM_WINDOW': mw})
        indicators = gen_indicator_momentum(mw)
        signal = random.choice([gen_signal_momentum(), gen_signal_momentum_accel()])

    elif strategy_type == 'keltner':
        ma_w = random.choice([12, 15, 18, 20, 25, 30, 40])
        atr_p = rand_atr_period()
        atr_m = rand_atr_multi()
        params.update({'KC_MA_WINDOW': ma_w, 'ATR_PERIOD': atr_p, 'ATR_MULTI': atr_m})
        indicators = gen_indicator_keltner(ma_w, atr_p, atr_m)
        signal = random.choice([gen_signal_keltner(), gen_signal_keltner_revert()])
        needs_atr = True

    elif strategy_type == 'vwap_revert':
        vw = random.choice([10, 15, 20, 30, 40])
        params.update({'VWAP_WINDOW': vw})
        indicators = gen_indicator_vwap(vw)
        signal = gen_signal_vwap_revert()

    elif strategy_type == 'obv_trend':
        indicators = gen_indicator_obv()
        signal = gen_signal_obv_trend()

    elif strategy_type == 'ma_rsi':
        sw = rand_short_window()
        lw = random.choice([x for x in MA_LONG_POOL if x > sw])
        rp = rand_rsi_period()
        rl, ru = rand_rsi_lower(), rand_rsi_upper()
        params.update({'SHORT_WINDOW': sw, 'LONG_WINDOW': lw, 'RSI_PERIOD': rp, 'RSI_LOWER': rl, 'RSI_UPPER': ru})
        indicators = gen_indicator_dual_ma(sw, lw) + '\n' + gen_indicator_rsi(rp)
        signal = gen_signal_ma_rsi(rl, ru)

    elif strategy_type == 'macd_boll':
        fast, slow, sig = rand_macd_params()
        bw, bs = rand_boll_window(), rand_boll_std()
        params.update({'MACD_FAST': fast, 'MACD_SLOW': slow, 'MACD_SIGNAL': sig, 'BOLL_WINDOW': bw, 'BOLL_STD': bs})
        indicators = gen_indicator_macd(fast, slow, sig) + '\n' + gen_indicator_boll(bw, bs)
        signal = gen_signal_macd_boll()

    elif strategy_type == 'rsi_boll':
        rp = rand_rsi_period()
        rl, ru = rand_rsi_lower(), rand_rsi_upper()
        bw, bs = rand_boll_window(), rand_boll_std()
        params.update({'RSI_PERIOD': rp, 'RSI_LOWER': rl, 'RSI_UPPER': ru, 'BOLL_WINDOW': bw, 'BOLL_STD': bs})
        indicators = gen_indicator_rsi(rp) + '\n' + gen_indicator_boll(bw, bs)
        signal = gen_signal_rsi_boll(rl, ru)

    elif strategy_type == 'ma_macd':
        sw = rand_short_window()
        lw = random.choice([x for x in MA_LONG_POOL if x > sw])
        fast, slow, sig = rand_macd_params()
        params.update({'SHORT_WINDOW': sw, 'LONG_WINDOW': lw, 'MACD_FAST': fast, 'MACD_SLOW': slow, 'MACD_SIGNAL': sig})
        indicators = gen_indicator_dual_ma(sw, lw) + '\n' + gen_indicator_macd(fast, slow, sig)
        signal = gen_signal_ma_macd()

    elif strategy_type == 'donchian_atr':
        cw = rand_channel_window()
        atr_p = rand_atr_period()
        params.update({'CHANNEL_WINDOW': cw, 'ATR_PERIOD': atr_p})
        indicators = gen_indicator_channel(cw) + '\n' + gen_indicator_atr(atr_p)
        signal = gen_signal_donchian_atr()
        needs_atr = True

    elif strategy_type == 'momentum_ma':
        mw = rand_momentum_window()
        lw = rand_long_window()
        params.update({'MOMENTUM_WINDOW': mw, 'LONG_WINDOW': lw})
        indicators = gen_indicator_momentum(mw) + f"\ndf['ma_long'] = df['close'].rolling({lw}).mean()  # {lw}日均线\n"
        signal = gen_signal_momentum_ma()

    elif strategy_type == 'rsi_channel':
        rp = rand_rsi_period()
        rl = rand_rsi_lower()
        cw = rand_channel_window()
        params.update({'RSI_PERIOD': rp, 'RSI_LOWER': rl, 'CHANNEL_WINDOW': cw})
        indicators = gen_indicator_rsi(rp) + '\n' + gen_indicator_channel(cw)
        signal = gen_signal_rsi_channel(rl)

    elif strategy_type == 'macd_rsi':
        fast, slow, sig = rand_macd_params()
        rp = rand_rsi_period()
        rl, ru = rand_rsi_lower(), rand_rsi_upper()
        params.update({'MACD_FAST': fast, 'MACD_SLOW': slow, 'MACD_SIGNAL': sig, 'RSI_PERIOD': rp, 'RSI_LOWER': rl, 'RSI_UPPER': ru})
        indicators = gen_indicator_macd(fast, slow, sig) + '\n' + gen_indicator_rsi(rp)
        signal = gen_signal_macd_rsi(rl, ru)

    elif strategy_type == 'boll_channel':
        bw, bs = rand_boll_window(), rand_boll_std()
        cw = rand_channel_window()
        params.update({'BOLL_WINDOW': bw, 'BOLL_STD': bs, 'CHANNEL_WINDOW': cw})
        indicators = gen_indicator_boll(bw, bs) + '\n' + gen_indicator_channel(cw)
        signal = gen_signal_boll_channel()

    elif strategy_type == 'ma_volume':
        sw = rand_short_window()
        lw = random.choice([x for x in MA_LONG_POOL if x > sw])
        vm = rand_volume_multi()
        vw = random.choice([10, 15, 20, 30])
        params.update({'SHORT_WINDOW': sw, 'LONG_WINDOW': lw, 'VOL_MULTI': vm, 'VOL_WINDOW': vw})
        indicators = gen_indicator_dual_ma(sw, lw) + '\n' + gen_indicator_volume_ma(vw)
        signal = gen_signal_ma_volume(vm)

    elif strategy_type == 'momentum_rsi':
        mw = rand_momentum_window()
        rp = rand_rsi_period()
        rl = rand_rsi_lower()
        params.update({'MOMENTUM_WINDOW': mw, 'RSI_PERIOD': rp, 'RSI_LOWER': rl})
        indicators = gen_indicator_momentum(mw) + '\n' + gen_indicator_rsi(rp)
        signal = gen_signal_momentum_rsi(rl)

    elif strategy_type == 'ema_boll':
        ss, ls = pick_two_sorted(EMA_POOL)
        bw, bs = rand_boll_window(), rand_boll_std()
        params.update({'EMA_SHORT': ss, 'EMA_LONG': ls, 'BOLL_WINDOW': bw, 'BOLL_STD': bs})
        indicators = gen_indicator_ema_cross(ss, ls) + '\n' + gen_indicator_boll(bw, bs)
        signal = gen_signal_ema_boll()

    elif strategy_type == 'channel_ma':
        cw = rand_channel_window()
        sw = rand_short_window()
        lw = random.choice([x for x in MA_LONG_POOL if x > sw])
        params.update({'CHANNEL_WINDOW': cw, 'SHORT_WINDOW': sw, 'LONG_WINDOW': lw})
        indicators = gen_indicator_channel(cw) + '\n' + gen_indicator_dual_ma(sw, lw)
        signal = gen_signal_channel_ma()

    else:
        sw, lw = 5, 20
        params.update({'SHORT_WINDOW': sw, 'LONG_WINDOW': lw})
        indicators = gen_indicator_dual_ma(sw, lw)
        signal = gen_signal_dual_ma()

    # 通用参数
    comm = rand_commission()
    stax = rand_stamp_tax()
    rfr = rand_risk_free()
    params.update({'COMMISSION': comm, 'STAMP_TAX': stax, 'RISK_FREE_RATE': rfr})

    # 止损止盈类型随机选择
    sl_type = random.choice(STOPLOSS_TYPES)
    tp_type = random.choice(TAKEPROFIT_TYPES)
    pos_type = random.choice(POSITION_TYPES)

    # 需要 ATR 但指标中还没有的情况，补上
    needs_atr_for_sl_tp = sl_type in ('atr_based', 'chandelier', 'percentage_of_atr') or tp_type in ('atr_based', 'risk_reward')
    if needs_atr_for_sl_tp and not needs_atr and 'atr' not in indicators.lower():
        atr_p = rand_atr_period()
        params['ATR_PERIOD'] = atr_p
        indicators += '\n' + gen_indicator_atr(atr_p)

    # 等风险仓位也需要 ATR
    if pos_type == 'equal_risk' and not needs_atr and 'atr' not in indicators.lower():
        atr_p = rand_atr_period()
        params['ATR_PERIOD'] = atr_p
        indicators += '\n' + gen_indicator_atr(atr_p)

    # 组装代码
    code_parts.append(gen_params(strategy_type, params))
    code_parts.append(gen_data_load())
    code_parts.append(indicators)
    code_parts.append(signal)

    # 止损
    if sl_type == 'fixed_pct':
        code_parts.append(gen_stoploss_fixed(rand_stoploss_pct()))
    elif sl_type == 'atr_based':
        code_parts.append(gen_stoploss_atr(rand_atr_multi()))
    elif sl_type == 'trailing':
        code_parts.append(gen_stoploss_trailing(rand_trailing_pct()))
    elif sl_type == 'chandelier':
        code_parts.append(gen_stoploss_chandelier(rand_atr_multi()))
    elif sl_type == 'percentage_of_atr':
        code_parts.append(gen_stoploss_pct_atr(rand_stoploss_pct(), rand_atr_multi()))

    # 止盈
    if tp_type == 'fixed_pct':
        code_parts.append(gen_takeprofit_fixed(rand_takeprofit_pct()))
    elif tp_type == 'atr_based':
        code_parts.append(gen_takeprofit_atr(rand_atr_multi()))
    elif tp_type == 'dynamic_ratio':
        code_parts.append(gen_takeprofit_dynamic())
    elif tp_type == 'risk_reward':
        code_parts.append(gen_takeprofit_risk_reward(rand_risk_reward()))
    elif tp_type == 'trailing_tp':
        code_parts.append(gen_takeprofit_trailing(rand_trailing_pct()))

    # 仓位管理
    if pos_type == 'fixed_frac':
        code_parts.append(gen_position_fixed(rand_position_frac()))
    elif pos_type == 'kelly_approx':
        code_parts.append(gen_position_kelly())
    elif pos_type == 'volatility_scaled':
        code_parts.append(gen_position_vol_scaled())
    elif pos_type == 'equal_risk':
        code_parts.append(gen_position_equal_risk())
    elif pos_type == 'max_drawdown_scaled':
        code_parts.append(gen_position_maxdd_scaled())

    code_parts.append(gen_cost_and_returns())
    code_parts.append(gen_performance())
    code_parts.append(gen_visualization())

    return '\n'.join(code_parts)


# ========== 主生成逻辑 ==========
def main():
    os.makedirs(OUTPUT_DIR, exist_ok=True)
    random.seed(2026)  # 第三批用新种子

    # 策略类型分配
    strategy_list = []
    base_count = NUM_STRATEGIES // len(STRATEGY_TYPES)
    remainder = NUM_STRATEGIES % len(STRATEGY_TYPES)

    for i, stype in enumerate(STRATEGY_TYPES):
        count = base_count + (1 if i < remainder else 0)
        strategy_list.extend([stype] * count)

    random.shuffle(strategy_list)

    for idx in range(NUM_STRATEGIES):
        stype = strategy_list[idx]
        code = build_strategy(stype, idx)
        file_num = START_INDEX + idx
        filename = f'strategy_{file_num:04d}_{stype}.py'
        filepath = os.path.join(OUTPUT_DIR, filename)
        with open(filepath, 'w', encoding='utf-8') as f:
            f.write(code)

        if (idx + 1) % 500 == 0:
            print(f'已生成 {idx+1}/{NUM_STRATEGIES} 个策略文件...')

    print(f'\n全部完成！共生成 {NUM_STRATEGIES} 个策略文件（编号 {START_INDEX}-{START_INDEX + NUM_STRATEGIES - 1}）')
    print(f'保存路径: {OUTPUT_DIR}')

    from collections import Counter
    type_counts = Counter(strategy_list)
    print('\n各策略类型数量:')
    for stype, count in sorted(type_counts.items()):
        print(f'  {stype}: {count}')


if __name__ == '__main__':
    main()

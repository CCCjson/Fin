"""
策略回测代码批量生成器
生成 1000 个符合质量标准的量化回测代码文件
"""
import random
import os

OUTPUT_DIR = '/Users/cccjson/Desktop/Fin/code_dataset'
NUM_STRATEGIES = 1000

# ========== 策略类型定义 ==========
STRATEGY_TYPES = [
    'dual_ma',        # 双均线
    'macd',           # MACD
    'bollinger',      # 布林带
    'rsi',            # RSI
    'channel',        # 通道突破
    'momentum',       # 动量
    'ma_rsi',         # 均线 + RSI 组合
    'macd_boll',      # MACD + 布林带组合
    'rsi_boll',       # RSI + 布林带组合
    'ma_macd',        # 均线 + MACD 组合
    'triple_ma',      # 三均线
    'keltner',        # 肯特纳通道
    'donchian_atr',   # 唐奇安通道 + ATR
    'momentum_ma',    # 动量 + 均线
    'rsi_channel',    # RSI + 通道突破
]

# ========== 止损止盈类型 ==========
STOPLOSS_TYPES = ['fixed_pct', 'atr_based', 'trailing']
TAKEPROFIT_TYPES = ['fixed_pct', 'atr_based', 'dynamic_ratio']
POSITION_TYPES = ['fixed_frac', 'kelly_approx', 'volatility_scaled']


def rand_short_window():
    return random.choice([3, 5, 7, 8, 10, 12, 15])

def rand_long_window():
    return random.choice([20, 25, 30, 40, 50, 60, 90, 120])

def rand_rsi_period():
    return random.choice([6, 9, 12, 14, 21])

def rand_rsi_lower():
    return random.choice([20, 25, 28, 30, 35])

def rand_rsi_upper():
    return random.choice([65, 70, 72, 75, 80])

def rand_boll_window():
    return random.choice([15, 20, 25, 30, 40])

def rand_boll_std():
    return random.choice([1.5, 1.8, 2.0, 2.2, 2.5, 3.0])

def rand_macd_params():
    fast = random.choice([8, 10, 12, 15])
    slow = random.choice([20, 24, 26, 30])
    signal = random.choice([7, 9, 11])
    return fast, slow, signal

def rand_channel_window():
    return random.choice([10, 15, 20, 25, 30, 40, 50, 55])

def rand_momentum_window():
    return random.choice([5, 10, 15, 20, 30, 60])

def rand_atr_period():
    return random.choice([10, 14, 20, 25])

def rand_commission():
    return random.choice([0.0003, 0.0005, 0.0006, 0.0008, 0.001])

def rand_stamp_tax():
    return random.choice([0.0005, 0.001])

def rand_stoploss_pct():
    return round(random.uniform(0.02, 0.10), 3)

def rand_takeprofit_pct():
    return round(random.uniform(0.05, 0.25), 3)

def rand_atr_multi():
    return round(random.uniform(1.0, 4.0), 1)

def rand_trailing_pct():
    return round(random.uniform(0.03, 0.12), 3)

def rand_position_frac():
    return round(random.uniform(0.1, 1.0), 2)

def rand_risk_free():
    return random.choice([0.02, 0.025, 0.03, 0.035])


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
    """生成参数区块"""
    lines = ["# ========== 参数设置 =========="]
    lines.append(f"DATA_PATH = 'stock_daily.csv'")
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


def gen_indicator_dual_ma(short_w, long_w):
    return f"""# ========== 计算均线指标 ==========
df['ma_short'] = df['close'].rolling({short_w}).mean()  # {short_w}日均线
df['ma_long'] = df['close'].rolling({long_w}).mean()    # {long_w}日均线
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


# ========== 信号生成 ==========

def gen_signal_dual_ma():
    return """# ========== 信号生成 ==========
df['signal'] = 0
df.loc[df['ma_short'] > df['ma_long'], 'signal'] = 1   # 短均线在长均线上方，持仓
df.loc[df['ma_short'] <= df['ma_long'], 'signal'] = 0   # 短均线在长均线下方，空仓
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


def gen_signal_boll_mean_revert():
    return """# ========== 信号生成 ==========
df['signal'] = 0
# 价格跌破下轨时买入（均值回归）
df.loc[df['close'].shift(1) < df['boll_lower'].shift(1), 'signal'] = 1
# 价格突破上轨时卖出
df.loc[df['close'].shift(1) > df['boll_upper'].shift(1), 'signal'] = 0
# 前向填充信号
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


def gen_signal_momentum():
    return """# ========== 信号生成 ==========
df['signal'] = 0
# 动量为正且高于动量均线时持仓
df.loc[(df['momentum'].shift(1) > 0) & (df['momentum'].shift(1) > df['momentum_ma'].shift(1)), 'signal'] = 1
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


# ========== 止损止盈 ==========

def gen_stoploss_fixed(pct):
    return f"""# ========== 固定百分比止损 ==========
STOPLOSS_PCT = {pct}
df['entry_price'] = np.nan
df['stop_triggered'] = False
entry_price = np.nan
in_position = False

for i in range(1, len(df)):
    if df['signal'].iloc[i] == 1 and not in_position:
        entry_price = df['close'].iloc[i-1]  # 用前一日收盘价作为参考入场价
        in_position = True
    if in_position and df['signal'].iloc[i] == 1:
        # 检查是否触发止损
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


# ========== 仓位管理 ==========

def gen_position_fixed(frac):
    return f"""# ========== 仓位管理（信号延迟 + 固定仓位）==========
POSITION_FRAC = {frac}
df['position'] = df['signal'].shift(1) * POSITION_FRAC  # 信号延迟一天，固定仓位比例
df['position'] = df['position'].fillna(0)
"""


def gen_position_kelly():
    return """# ========== 仓位管理（信号延迟 + Kelly近似）==========
# 计算滚动胜率和盈亏比来近似Kelly仓位
df['pnl'] = df['close'].pct_change()
df['win_roll'] = df['pnl'].rolling(60).apply(lambda x: (x > 0).mean(), raw=True)
df['avg_win_roll'] = df['pnl'].rolling(60).apply(lambda x: x[x > 0].mean() if (x > 0).any() else 0.01, raw=True)
df['avg_loss_roll'] = df['pnl'].rolling(60).apply(lambda x: -x[x < 0].mean() if (x < 0).any() else 0.01, raw=True)
df['odds_roll'] = df['avg_win_roll'] / df['avg_loss_roll'].replace(0, 0.01)
df['kelly'] = (df['win_roll'] * df['odds_roll'] - (1 - df['win_roll'])) / df['odds_roll'].replace(0, 1)
df['kelly'] = df['kelly'].clip(0.05, 0.5)  # 限制仓位在5%到50%之间

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


# ========== 交易成本 + 收益计算 + 绩效 ==========

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
# 剔除 NaN
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


# ========== 策略组装 ==========

def build_strategy(strategy_type, idx):
    """根据策略类型组装完整代码"""
    code_parts = [gen_header()]
    params = {}
    needs_atr = False

    # ========== 根据策略类型生成指标和信号 ==========
    if strategy_type == 'dual_ma':
        sw, lw = rand_short_window(), rand_long_window()
        while sw >= lw:
            lw = rand_long_window()
        params.update({'SHORT_WINDOW': sw, 'LONG_WINDOW': lw})
        indicators = gen_indicator_dual_ma(sw, lw)
        signal = gen_signal_dual_ma()

    elif strategy_type == 'triple_ma':
        sw = rand_short_window()
        mw = random.choice([15, 20, 25, 30])
        lw = rand_long_window()
        while sw >= mw:
            mw = random.choice([15, 20, 25, 30])
        while mw >= lw:
            lw = rand_long_window()
        params.update({'SHORT_WINDOW': sw, 'MID_WINDOW': mw, 'LONG_WINDOW': lw})
        indicators = gen_indicator_triple_ma(sw, mw, lw)
        signal = gen_signal_triple_ma()

    elif strategy_type == 'macd':
        fast, slow, sig = rand_macd_params()
        params.update({'MACD_FAST': fast, 'MACD_SLOW': slow, 'MACD_SIGNAL': sig})
        indicators = gen_indicator_macd(fast, slow, sig)
        signal = random.choice([gen_signal_macd(), gen_signal_macd_zero()])

    elif strategy_type == 'bollinger':
        bw, bs = rand_boll_window(), rand_boll_std()
        params.update({'BOLL_WINDOW': bw, 'BOLL_STD': bs})
        indicators = gen_indicator_boll(bw, bs)
        signal = random.choice([gen_signal_boll_mean_revert(), gen_signal_boll_breakout()])

    elif strategy_type == 'rsi':
        rp = rand_rsi_period()
        rl, ru = rand_rsi_lower(), rand_rsi_upper()
        params.update({'RSI_PERIOD': rp, 'RSI_LOWER': rl, 'RSI_UPPER': ru})
        indicators = gen_indicator_rsi(rp)
        signal = gen_signal_rsi(rl, ru)

    elif strategy_type == 'channel':
        cw = rand_channel_window()
        params.update({'CHANNEL_WINDOW': cw})
        indicators = gen_indicator_channel(cw)
        signal = gen_signal_channel()

    elif strategy_type == 'momentum':
        mw = rand_momentum_window()
        params.update({'MOMENTUM_WINDOW': mw})
        indicators = gen_indicator_momentum(mw)
        signal = gen_signal_momentum()

    elif strategy_type == 'keltner':
        ma_w = random.choice([15, 20, 25, 30])
        atr_p = rand_atr_period()
        atr_m = rand_atr_multi()
        params.update({'KC_MA_WINDOW': ma_w, 'ATR_PERIOD': atr_p, 'ATR_MULTI': atr_m})
        indicators = gen_indicator_keltner(ma_w, atr_p, atr_m)
        signal = gen_signal_keltner()
        needs_atr = True  # already included in keltner

    elif strategy_type == 'ma_rsi':
        sw, lw = rand_short_window(), rand_long_window()
        while sw >= lw:
            lw = rand_long_window()
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
        sw, lw = rand_short_window(), rand_long_window()
        while sw >= lw:
            lw = rand_long_window()
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
        indicators = gen_indicator_momentum(mw) + '\n' + gen_indicator_dual_ma(mw, lw).replace("df['ma_short']", "df['_ma_temp']")
        # Simpler approach: just add ma_long
        indicators = gen_indicator_momentum(mw) + f"\ndf['ma_long'] = df['close'].rolling({lw}).mean()  # {lw}日均线\n"
        signal = gen_signal_momentum_ma()

    elif strategy_type == 'rsi_channel':
        rp = rand_rsi_period()
        rl = rand_rsi_lower()
        cw = rand_channel_window()
        params.update({'RSI_PERIOD': rp, 'RSI_LOWER': rl, 'CHANNEL_WINDOW': cw})
        indicators = gen_indicator_rsi(rp) + '\n' + gen_indicator_channel(cw)
        signal = gen_signal_rsi_channel(rl)

    else:
        # fallback to dual_ma
        sw, lw = 5, 20
        params.update({'SHORT_WINDOW': sw, 'LONG_WINDOW': lw})
        indicators = gen_indicator_dual_ma(sw, lw)
        signal = gen_signal_dual_ma()

    # 通用参数
    comm = rand_commission()
    stax = rand_stamp_tax()
    rfr = rand_risk_free()
    params.update({'COMMISSION': comm, 'STAMP_TAX': stax, 'RISK_FREE_RATE': rfr})

    # 需要 ATR 但指标中没有的，补上
    sl_type = random.choice(STOPLOSS_TYPES)
    tp_type = random.choice(TAKEPROFIT_TYPES)
    pos_type = random.choice(POSITION_TYPES)

    if (sl_type == 'atr_based' or tp_type == 'atr_based') and not needs_atr and 'atr' not in indicators.lower():
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

    # 止盈
    if tp_type == 'fixed_pct':
        code_parts.append(gen_takeprofit_fixed(rand_takeprofit_pct()))
    elif tp_type == 'atr_based':
        code_parts.append(gen_takeprofit_atr(rand_atr_multi()))
    elif tp_type == 'dynamic_ratio':
        code_parts.append(gen_takeprofit_dynamic())

    # 仓位管理
    if pos_type == 'fixed_frac':
        code_parts.append(gen_position_fixed(rand_position_frac()))
    elif pos_type == 'kelly_approx':
        code_parts.append(gen_position_kelly())
    elif pos_type == 'volatility_scaled':
        code_parts.append(gen_position_vol_scaled())

    code_parts.append(gen_cost_and_returns())
    code_parts.append(gen_performance())
    code_parts.append(gen_visualization())

    return '\n'.join(code_parts)


# ========== 主生成逻辑 ==========
def main():
    os.makedirs(OUTPUT_DIR, exist_ok=True)
    random.seed(42)

    # 策略类型分配：保证每种类型都有足够的样本
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
        filename = f'strategy_{idx+1:04d}_{stype}.py'
        filepath = os.path.join(OUTPUT_DIR, filename)
        with open(filepath, 'w', encoding='utf-8') as f:
            f.write(code)

        if (idx + 1) % 100 == 0:
            print(f'已生成 {idx+1}/{NUM_STRATEGIES} 个策略文件...')

    print(f'\n全部完成！共生成 {NUM_STRATEGIES} 个策略文件')
    print(f'保存路径: {OUTPUT_DIR}')

    # 统计各类型数量
    from collections import Counter
    type_counts = Counter(strategy_list)
    print('\n各策略类型数量:')
    for stype, count in sorted(type_counts.items()):
        print(f'  {stype}: {count}')


if __name__ == '__main__':
    main()

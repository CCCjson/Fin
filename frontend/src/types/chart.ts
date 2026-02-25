/** K线图数据格式 (lightweight-charts 要求) */
export interface CandlestickData {
  time: string;
  open: number;
  high: number;
  low: number;
  close: number;
}

/** 成交量数据格式 */
export interface VolumeData {
  time: string;
  value: number;
  color: string;
}

/** 均线数据 */
export interface MAData {
  time: string;
  value: number;
}

/** MACD 指标数据 */
export interface MACDData {
  time: string;
  dif: number;
  dea: number;
  histogram: number;
}

/** RSI 指标数据 */
export interface RSIData {
  time: string;
  value: number;
}

/** 布林带数据 */
export interface BollData {
  time: string;
  upper: number;
  middle: number;
  lower: number;
}

/** 买卖信号 */
export interface ChartSignal {
  time: string;
  type: 'buy' | 'sell';
  reason: string;
  strength: number;
}

/** 指标配置 */
export interface IndicatorConfig {
  ma: {
    enabled: boolean;
    periods: number[];
  };
  macd: {
    enabled: boolean;
    fast: number;
    slow: number;
    signal: number;
  };
  rsi: {
    enabled: boolean;
    period: number;
  };
  boll: {
    enabled: boolean;
    period: number;
    stdDev: number;
  };
  signals: {
    enabled: boolean;
  };
}

/** 默认指标配置 */
export const DEFAULT_INDICATOR_CONFIG: IndicatorConfig = {
  ma: {
    enabled: true,
    periods: [5, 10, 20, 60],
  },
  macd: {
    enabled: false,
    fast: 12,
    slow: 26,
    signal: 9,
  },
  rsi: {
    enabled: false,
    period: 14,
  },
  boll: {
    enabled: false,
    period: 20,
    stdDev: 2,
  },
  signals: {
    enabled: false,
  },
};

/** 图表颜色主题 (A股风格: 红涨绿跌) */
export const CHART_COLORS = {
  up: '#ef5350',        // 涨 - 红色
  down: '#26a69a',      // 跌 - 绿色
  background: '#131722', // 背景
  grid: '#2B2B43',      // 网格
  text: '#D1D4DC',      // 文字
  crosshair: '#758696', // 十字光标
  ma5: '#f5c878',       // MA5 - 黄色
  ma10: '#00bcd4',      // MA10 - 青色
  ma20: '#e91e63',      // MA20 - 粉色
  ma60: '#9c27b0',      // MA60 - 紫色
  macdDif: '#ffeb3b',   // MACD DIF
  macdDea: '#2196f3',   // MACD DEA
  macdHistUp: '#ef5350', // MACD 柱状图正值
  macdHistDown: '#26a69a', // MACD 柱状图负值
  rsi: '#ff9800',       // RSI 线
  bollUpper: '#e91e63', // 布林带上轨
  bollMiddle: '#2196f3', // 布林带中轨
  bollLower: '#4caf50', // 布林带下轨
};

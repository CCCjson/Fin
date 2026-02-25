import type { StockData } from '../types';
import type { MAData, MACDData, RSIData, BollData } from '../types/chart';

/**
 * 计算简单移动平均线 (SMA)
 * @param data 股票数据
 * @param period 周期
 * @returns MA 数据数组
 */
export function calculateMA(data: StockData[], period: number): MAData[] {
  const result: MAData[] = [];

  for (let i = 0; i < data.length; i++) {
    if (i < period - 1) {
      continue;
    }

    let sum = 0;
    for (let j = 0; j < period; j++) {
      sum += data[i - j].close;
    }

    result.push({
      time: data[i].date,
      value: sum / period,
    });
  }

  return result;
}

/**
 * 计算指数移动平均线 (EMA)
 * @param data 股票数据
 * @param period 周期
 * @returns EMA 数据数组
 */
export function calculateEMA(data: StockData[], period: number): MAData[] {
  const result: MAData[] = [];
  const multiplier = 2 / (period + 1);

  // 第一个值使用 SMA
  let ema = 0;
  for (let i = 0; i < period; i++) {
    ema += data[i].close;
  }
  ema /= period;

  result.push({
    time: data[period - 1].date,
    value: ema,
  });

  // 后续值使用 EMA 公式
  for (let i = period; i < data.length; i++) {
    ema = (data[i].close - ema) * multiplier + ema;
    result.push({
      time: data[i].date,
      value: ema,
    });
  }

  return result;
}

/**
 * 计算 MACD 指标
 * @param data 股票数据
 * @param fastPeriod 快线周期 (默认12)
 * @param slowPeriod 慢线周期 (默认26)
 * @param signalPeriod 信号线周期 (默认9)
 * @returns MACD 数据数组
 */
export function calculateMACD(
  data: StockData[],
  fastPeriod: number = 12,
  slowPeriod: number = 26,
  signalPeriod: number = 9
): MACDData[] {
  const result: MACDData[] = [];

  // 计算快线和慢线 EMA
  const fastEMA = calculateEMAValues(data.map(d => d.close), fastPeriod);
  const slowEMA = calculateEMAValues(data.map(d => d.close), slowPeriod);

  // 计算 DIF (快线 - 慢线)
  const dif: number[] = [];
  const startIndex = slowPeriod - 1;

  for (let i = startIndex; i < data.length; i++) {
    const fastIdx = i - (fastPeriod - 1);
    const slowIdx = i - (slowPeriod - 1);
    if (fastIdx >= 0 && slowIdx >= 0 && fastIdx < fastEMA.length && slowIdx < slowEMA.length) {
      dif.push(fastEMA[fastIdx] - slowEMA[slowIdx]);
    }
  }

  // 计算 DEA (DIF 的 EMA)
  const dea = calculateEMAValues(dif, signalPeriod);

  // 组装结果
  const deaStartIndex = signalPeriod - 1;
  for (let i = 0; i < dea.length; i++) {
    const dataIndex = startIndex + deaStartIndex + i;
    const difValue = dif[deaStartIndex + i];
    const deaValue = dea[i];

    result.push({
      time: data[dataIndex].date,
      dif: difValue,
      dea: deaValue,
      histogram: (difValue - deaValue) * 2, // MACD 柱状图 = (DIF - DEA) * 2
    });
  }

  return result;
}

/**
 * 内部函数: 计算 EMA 值数组
 */
function calculateEMAValues(values: number[], period: number): number[] {
  const result: number[] = [];
  const multiplier = 2 / (period + 1);

  if (values.length < period) return result;

  // 第一个值使用 SMA
  let ema = 0;
  for (let i = 0; i < period; i++) {
    ema += values[i];
  }
  ema /= period;
  result.push(ema);

  // 后续值使用 EMA 公式
  for (let i = period; i < values.length; i++) {
    ema = (values[i] - ema) * multiplier + ema;
    result.push(ema);
  }

  return result;
}

/**
 * 计算 RSI 指标
 * @param data 股票数据
 * @param period 周期 (默认14)
 * @returns RSI 数据数组
 */
export function calculateRSI(data: StockData[], period: number = 14): RSIData[] {
  const result: RSIData[] = [];

  if (data.length < period + 1) return result;

  // 计算价格变动
  const changes: number[] = [];
  for (let i = 1; i < data.length; i++) {
    changes.push(data[i].close - data[i - 1].close);
  }

  // 计算初始平均涨幅和跌幅
  let avgGain = 0;
  let avgLoss = 0;

  for (let i = 0; i < period; i++) {
    if (changes[i] > 0) {
      avgGain += changes[i];
    } else {
      avgLoss += Math.abs(changes[i]);
    }
  }

  avgGain /= period;
  avgLoss /= period;

  // 计算第一个 RSI
  let rs = avgLoss === 0 ? 100 : avgGain / avgLoss;
  let rsi = 100 - (100 / (1 + rs));

  result.push({
    time: data[period].date,
    value: rsi,
  });

  // 使用 Wilder 平滑法计算后续 RSI
  for (let i = period; i < changes.length; i++) {
    const gain = changes[i] > 0 ? changes[i] : 0;
    const loss = changes[i] < 0 ? Math.abs(changes[i]) : 0;

    avgGain = (avgGain * (period - 1) + gain) / period;
    avgLoss = (avgLoss * (period - 1) + loss) / period;

    rs = avgLoss === 0 ? 100 : avgGain / avgLoss;
    rsi = 100 - (100 / (1 + rs));

    result.push({
      time: data[i + 1].date,
      value: rsi,
    });
  }

  return result;
}

/**
 * 计算布林带指标
 * @param data 股票数据
 * @param period 周期 (默认20)
 * @param stdDev 标准差倍数 (默认2)
 * @returns 布林带数据数组
 */
export function calculateBoll(
  data: StockData[],
  period: number = 20,
  stdDev: number = 2
): BollData[] {
  const result: BollData[] = [];

  for (let i = period - 1; i < data.length; i++) {
    // 计算 SMA (中轨)
    let sum = 0;
    for (let j = 0; j < period; j++) {
      sum += data[i - j].close;
    }
    const middle = sum / period;

    // 计算标准差（样本标准差，与后端 pandas rolling.std(ddof=1) 一致）
    let sumSquares = 0;
    for (let j = 0; j < period; j++) {
      sumSquares += Math.pow(data[i - j].close - middle, 2);
    }
    const std = Math.sqrt(sumSquares / (period - 1));

    result.push({
      time: data[i].date,
      upper: middle + stdDev * std,
      middle: middle,
      lower: middle - stdDev * std,
    });
  }

  return result;
}

/**
 * 计算所有均线
 * @param data 股票数据
 * @param periods 周期数组
 * @returns 各周期均线数据的 Map
 */
export function calculateAllMA(
  data: StockData[],
  periods: number[]
): Map<number, MAData[]> {
  const result = new Map<number, MAData[]>();

  for (const period of periods) {
    result.set(period, calculateMA(data, period));
  }

  return result;
}

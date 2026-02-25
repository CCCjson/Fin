import type { StockData } from '../types';
import type { ChartSignal } from '../types/chart';
import { calculateMA, calculateMACD, calculateRSI, calculateBoll } from './indicators';

/**
 * 检测买卖信号
 * 基于 MACD 金叉/死叉、均线金叉/死叉、RSI 超卖反弹/超买回落、布林带突破
 */
export function detectSignals(data: StockData[]): ChartSignal[] {
  if (data.length < 30) return [];

  const signals: ChartSignal[] = [];
  const dateSignals = new Map<string, ChartSignal>();

  // 收集各指标信号
  detectMACDSignals(data, dateSignals);
  detectMASignals(data, dateSignals);
  detectRSISignals(data, dateSignals);
  detectBollSignals(data, dateSignals);

  // 按时间排序输出
  for (const signal of dateSignals.values()) {
    signals.push(signal);
  }
  signals.sort((a, b) => (a.time < b.time ? -1 : a.time > b.time ? 1 : 0));

  return signals;
}

/** 合并同一天的信号：同方向叠加 reason，不同方向以强度高的为准 */
function mergeSignal(
  map: Map<string, ChartSignal>,
  time: string,
  type: 'buy' | 'sell',
  reason: string,
  strength: number,
) {
  const existing = map.get(time);
  if (!existing) {
    map.set(time, { time, type, reason, strength });
    return;
  }

  if (existing.type === type) {
    // 同方向：叠加原因，取较高强度
    existing.reason += ` | ${reason}`;
    existing.strength = Math.max(existing.strength, strength);
  } else {
    // 方向冲突：以强度高的为准
    if (strength > existing.strength) {
      existing.type = type;
      existing.reason = reason;
      existing.strength = strength;
    }
  }
}

/** MACD 金叉/死叉 */
function detectMACDSignals(data: StockData[], map: Map<string, ChartSignal>) {
  const macdData = calculateMACD(data);
  if (macdData.length < 2) return;

  for (let i = 1; i < macdData.length; i++) {
    const prev = macdData[i - 1];
    const curr = macdData[i];

    // DIF 上穿 DEA → 买入
    if (prev.dif <= prev.dea && curr.dif > curr.dea) {
      const detail = curr.dif < 0 ? 'MACD金叉(零轴下)' : 'MACD金叉';
      mergeSignal(map, curr.time, 'buy', detail, 0.7);
    }

    // DIF 下穿 DEA → 卖出
    if (prev.dif >= prev.dea && curr.dif < curr.dea) {
      const detail = curr.dif > 0 ? 'MACD死叉(零轴上)' : 'MACD死叉';
      mergeSignal(map, curr.time, 'sell', detail, 0.7);
    }
  }
}

/** 均线金叉/死叉 (MA5 vs MA20) */
function detectMASignals(data: StockData[], map: Map<string, ChartSignal>) {
  const ma5 = calculateMA(data, 5);
  const ma20 = calculateMA(data, 20);

  if (ma5.length === 0 || ma20.length === 0) return;

  // 对齐时间：建立 MA20 的时间索引
  const ma20Map = new Map<string, number>();
  for (const item of ma20) {
    ma20Map.set(item.time, item.value);
  }

  // 在 MA5 上遍历，找到 MA20 中对应的值
  let prevDiff: number | null = null;
  let prevTime: string | null = null;

  for (const m5 of ma5) {
    const m20Val = ma20Map.get(m5.time);
    if (m20Val === undefined) continue;

    const diff = m5.value - m20Val;

    if (prevDiff !== null && prevTime !== null) {
      // MA5 上穿 MA20 → 买入
      if (prevDiff <= 0 && diff > 0) {
        mergeSignal(map, m5.time, 'buy', 'MA金叉', 0.6);
      }
      // MA5 下穿 MA20 → 卖出
      if (prevDiff >= 0 && diff < 0) {
        mergeSignal(map, m5.time, 'sell', 'MA死叉', 0.6);
      }
    }

    prevDiff = diff;
    prevTime = m5.time;
  }
}

/** RSI 超卖反弹 / 超买回落 */
function detectRSISignals(data: StockData[], map: Map<string, ChartSignal>) {
  const rsiData = calculateRSI(data, 14);
  if (rsiData.length < 2) return;

  for (let i = 1; i < rsiData.length; i++) {
    const prev = rsiData[i - 1];
    const curr = rsiData[i];

    // RSI 上穿 30 → 超卖反弹买入
    if (prev.value <= 30 && curr.value > 30) {
      mergeSignal(map, curr.time, 'buy', 'RSI超卖反弹', 0.75);
    }

    // RSI 下穿 70 → 超买回落卖出
    if (prev.value >= 70 && curr.value < 70) {
      mergeSignal(map, curr.time, 'sell', 'RSI超买回落', 0.75);
    }
  }
}

/** 布林带突破 */
function detectBollSignals(data: StockData[], map: Map<string, ChartSignal>) {
  const bollData = calculateBoll(data, 20, 2);
  if (bollData.length < 2) return;

  // 对齐数据：boll 从 index=19 开始，对应 data[19]
  const bollStartIndex = 20 - 1;

  for (let i = 1; i < bollData.length; i++) {
    const dataIdx = bollStartIndex + i;
    const prevDataIdx = bollStartIndex + i - 1;

    if (dataIdx >= data.length || prevDataIdx >= data.length) break;

    const prevClose = data[prevDataIdx].close;
    const currClose = data[dataIdx].close;
    const prevBoll = bollData[i - 1];
    const currBoll = bollData[i];

    // 价格从下轨下方回到下轨上方 → 买入
    if (prevClose <= prevBoll.lower && currClose > currBoll.lower) {
      mergeSignal(map, currBoll.time, 'buy', 'BOLL下轨反弹', 0.65);
    }

    // 价格从上轨上方回到上轨下方 → 卖出
    if (prevClose >= prevBoll.upper && currClose < currBoll.upper) {
      mergeSignal(map, currBoll.time, 'sell', 'BOLL上轨回落', 0.65);
    }
  }
}

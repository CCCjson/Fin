import React, { useEffect, useRef, useCallback } from 'react';
import {
  createChart,
  createSeriesMarkers,
  CrosshairMode,
  ColorType,
  CandlestickSeries,
  HistogramSeries,
  LineSeries,
} from 'lightweight-charts';
import type {
  IChartApi,
  ISeriesApi,
  ISeriesMarkersPluginApi,
  CandlestickData as LWCandlestickData,
  HistogramData,
  LineData,
  Time,
} from 'lightweight-charts';
import type { StockData } from '../../types';
import type { IndicatorConfig } from '../../types/chart';
import { CHART_COLORS, DEFAULT_INDICATOR_CONFIG } from '../../types/chart';
import { calculateMA, calculateBoll, calculateMACD, calculateRSI } from '../../utils/indicators';
import { detectSignals } from '../../utils/signalDetector';

const MACD_HEIGHT = 150;
const RSI_HEIGHT = 120;

interface CandlestickChartProps {
  data: StockData[];
  height?: number;
  indicatorConfig?: IndicatorConfig;
}

export const CandlestickChart: React.FC<CandlestickChartProps> = ({
  data,
  height = 500,
  indicatorConfig = DEFAULT_INDICATOR_CONFIG,
}) => {
  // === 主图 refs ===
  const chartContainerRef = useRef<HTMLDivElement>(null);
  const chartRef = useRef<IChartApi | null>(null);
  const candlestickSeriesRef = useRef<ISeriesApi<'Candlestick'> | null>(null);
  const volumeSeriesRef = useRef<ISeriesApi<'Histogram'> | null>(null);
  const maSeriesRefs = useRef<Map<number, ISeriesApi<'Line'>>>(new Map());
  const bollSeriesRefs = useRef<{
    upper?: ISeriesApi<'Line'>;
    middle?: ISeriesApi<'Line'>;
    lower?: ISeriesApi<'Line'>;
  }>({});
  const markersPluginRef = useRef<ISeriesMarkersPluginApi<Time> | null>(null);

  // === MACD 子图 refs ===
  const macdChartContainerRef = useRef<HTMLDivElement>(null);
  const macdChartRef = useRef<IChartApi | null>(null);
  const macdDifSeriesRef = useRef<ISeriesApi<'Line'> | null>(null);
  const macdDeaSeriesRef = useRef<ISeriesApi<'Line'> | null>(null);
  const macdHistSeriesRef = useRef<ISeriesApi<'Histogram'> | null>(null);

  // === RSI 子图 refs ===
  const rsiChartContainerRef = useRef<HTMLDivElement>(null);
  const rsiChartRef = useRef<IChartApi | null>(null);
  const rsiSeriesRef = useRef<ISeriesApi<'Line'> | null>(null);

  // 转换数据格式
  const formatCandlestickData = useCallback((stockData: StockData[]): LWCandlestickData[] => {
    return stockData.map((item) => ({
      time: item.date as string,
      open: item.open,
      high: item.high,
      low: item.low,
      close: item.close,
    }));
  }, []);

  const formatVolumeData = useCallback((stockData: StockData[]): HistogramData[] => {
    return stockData.map((item) => ({
      time: item.date as string,
      value: item.volume,
      color: item.close >= item.open ? CHART_COLORS.up : CHART_COLORS.down,
    }));
  }, []);

  // 初始化主图
  useEffect(() => {
    if (!chartContainerRef.current) return;

    const container = chartContainerRef.current;

    const initChart = () => {
      if (chartRef.current) return;

      const chart = createChart(container, {
        width: container.clientWidth,
        height: height,
        layout: {
          background: { type: ColorType.Solid, color: CHART_COLORS.background },
          textColor: CHART_COLORS.text,
        },
        grid: {
          vertLines: { color: CHART_COLORS.grid },
          horzLines: { color: CHART_COLORS.grid },
        },
        crosshair: {
          mode: CrosshairMode.Normal,
          vertLine: { color: CHART_COLORS.crosshair, width: 1, style: 2, labelBackgroundColor: '#2B2B43' },
          horzLine: { color: CHART_COLORS.crosshair, width: 1, style: 2, labelBackgroundColor: '#2B2B43' },
        },
        rightPriceScale: {
          borderColor: CHART_COLORS.grid,
          scaleMargins: { top: 0.1, bottom: 0.25 },
        },
        timeScale: {
          borderColor: CHART_COLORS.grid,
          timeVisible: true,
          secondsVisible: false,
        },
      });

      chartRef.current = chart;

      const candlestickSeries = chart.addSeries(CandlestickSeries, {
        upColor: CHART_COLORS.up,
        downColor: CHART_COLORS.down,
        borderUpColor: CHART_COLORS.up,
        borderDownColor: CHART_COLORS.down,
        wickUpColor: CHART_COLORS.up,
        wickDownColor: CHART_COLORS.down,
      });
      candlestickSeriesRef.current = candlestickSeries;

      const volumeSeries = chart.addSeries(HistogramSeries, {
        priceFormat: { type: 'volume' },
        priceScaleId: 'volume',
      });
      chart.priceScale('volume').applyOptions({
        scaleMargins: { top: 0.8, bottom: 0 },
      });
      volumeSeriesRef.current = volumeSeries;

      // 主图时间轴同步 → 子图
      chart.timeScale().subscribeVisibleLogicalRangeChange((range) => {
        if (!range) return;
        macdChartRef.current?.timeScale().setVisibleLogicalRange(range);
        rsiChartRef.current?.timeScale().setVisibleLogicalRange(range);
      });

      // 主图十字光标同步 → 子图
      chart.subscribeCrosshairMove((param) => {
        if (param.time) {
          if (macdChartRef.current && macdDifSeriesRef.current) {
            macdChartRef.current.setCrosshairPosition(0, param.time, macdDifSeriesRef.current);
          }
          if (rsiChartRef.current && rsiSeriesRef.current) {
            rsiChartRef.current.setCrosshairPosition(50, param.time, rsiSeriesRef.current);
          }
        } else {
          macdChartRef.current?.clearCrosshairPosition();
          rsiChartRef.current?.clearCrosshairPosition();
        }
      });
    };

    if (container.clientWidth > 0) {
      initChart();
    }

    const resizeObserver = new ResizeObserver((entries) => {
      for (const entry of entries) {
        const { width } = entry.contentRect;
        if (width > 0) {
          if (!chartRef.current) {
            initChart();
          } else {
            chartRef.current.applyOptions({ width });
          }
          // 同步调整子图宽度
          macdChartRef.current?.applyOptions({ width });
          rsiChartRef.current?.applyOptions({ width });
        }
      }
    });
    resizeObserver.observe(container);

    return () => {
      resizeObserver.disconnect();
      if (chartRef.current) {
        chartRef.current.remove();
        chartRef.current = null;
      }
      candlestickSeriesRef.current = null;
      volumeSeriesRef.current = null;
      maSeriesRefs.current.clear();
      bollSeriesRefs.current = {};
      markersPluginRef.current = null;
    };
  }, [height]);

  // 更新 K 线数据
  useEffect(() => {
    if (!chartRef.current || !candlestickSeriesRef.current || !volumeSeriesRef.current || data.length === 0) return;

    candlestickSeriesRef.current.setData(formatCandlestickData(data));
    volumeSeriesRef.current.setData(formatVolumeData(data));
    chartRef.current.timeScale().fitContent();
  }, [data, formatCandlestickData, formatVolumeData]);

  // 更新均线
  useEffect(() => {
    if (!chartRef.current || data.length === 0) return;

    const chart = chartRef.current;
    const maColors: Record<number, string> = {
      5: CHART_COLORS.ma5, 10: CHART_COLORS.ma10, 20: CHART_COLORS.ma20, 60: CHART_COLORS.ma60,
    };

    const desiredPeriods = new Set(indicatorConfig.ma.enabled ? indicatorConfig.ma.periods : []);
    const currentPeriods = new Set(maSeriesRefs.current.keys());

    for (const period of currentPeriods) {
      if (!desiredPeriods.has(period)) {
        chart.removeSeries(maSeriesRefs.current.get(period)!);
        maSeriesRefs.current.delete(period);
      }
    }

    for (const period of desiredPeriods) {
      const maData = calculateMA(data, period);
      if (maData.length === 0) continue;

      const lineData: LineData[] = maData.map((item) => ({ time: item.time as string, value: item.value }));

      if (maSeriesRefs.current.has(period)) {
        maSeriesRefs.current.get(period)!.setData(lineData);
      } else {
        const maSeries = chart.addSeries(LineSeries, {
          color: maColors[period] || '#ffffff', lineWidth: 1,
          priceLineVisible: false, lastValueVisible: false, crosshairMarkerVisible: false,
        });
        maSeries.setData(lineData);
        maSeriesRefs.current.set(period, maSeries);
      }
    }
  }, [data, indicatorConfig.ma]);

  // 更新布林带
  useEffect(() => {
    if (!chartRef.current || data.length === 0) return;

    const chart = chartRef.current;
    const hasBoll = bollSeriesRefs.current.upper !== undefined;

    if (indicatorConfig.boll.enabled) {
      const bollData = calculateBoll(data, indicatorConfig.boll.period, indicatorConfig.boll.stdDev);
      if (bollData.length === 0) return;

      const upperData = bollData.map((item) => ({ time: item.time as string, value: item.upper }));
      const middleData = bollData.map((item) => ({ time: item.time as string, value: item.middle }));
      const lowerData = bollData.map((item) => ({ time: item.time as string, value: item.lower }));

      if (hasBoll) {
        bollSeriesRefs.current.upper!.setData(upperData);
        bollSeriesRefs.current.middle!.setData(middleData);
        bollSeriesRefs.current.lower!.setData(lowerData);
      } else {
        const upperSeries = chart.addSeries(LineSeries, {
          color: CHART_COLORS.bollUpper, lineWidth: 1, lineStyle: 2,
          priceLineVisible: false, lastValueVisible: false, crosshairMarkerVisible: false,
        });
        upperSeries.setData(upperData);
        bollSeriesRefs.current.upper = upperSeries;

        const middleSeries = chart.addSeries(LineSeries, {
          color: CHART_COLORS.bollMiddle, lineWidth: 1,
          priceLineVisible: false, lastValueVisible: false, crosshairMarkerVisible: false,
        });
        middleSeries.setData(middleData);
        bollSeriesRefs.current.middle = middleSeries;

        const lowerSeries = chart.addSeries(LineSeries, {
          color: CHART_COLORS.bollLower, lineWidth: 1, lineStyle: 2,
          priceLineVisible: false, lastValueVisible: false, crosshairMarkerVisible: false,
        });
        lowerSeries.setData(lowerData);
        bollSeriesRefs.current.lower = lowerSeries;
      }
    } else if (hasBoll) {
      chart.removeSeries(bollSeriesRefs.current.upper!);
      chart.removeSeries(bollSeriesRefs.current.middle!);
      chart.removeSeries(bollSeriesRefs.current.lower!);
      bollSeriesRefs.current = {};
    }
  }, [data, indicatorConfig.boll]);

  // === MACD 子图：创建/销毁 ===
  useEffect(() => {
    if (!indicatorConfig.macd.enabled) return;

    const container = macdChartContainerRef.current;
    if (!container || container.clientWidth === 0) return;

    const chart = createChart(container, {
      width: container.clientWidth,
      height: MACD_HEIGHT,
      layout: {
        background: { type: ColorType.Solid, color: CHART_COLORS.background },
        textColor: CHART_COLORS.text,
      },
      grid: {
        vertLines: { color: CHART_COLORS.grid },
        horzLines: { color: CHART_COLORS.grid },
      },
      crosshair: {
        mode: CrosshairMode.Normal,
        vertLine: { color: CHART_COLORS.crosshair, width: 1, style: 2, labelBackgroundColor: '#2B2B43' },
        horzLine: { color: CHART_COLORS.crosshair, width: 1, style: 2, labelBackgroundColor: '#2B2B43' },
      },
      rightPriceScale: { borderColor: CHART_COLORS.grid },
      timeScale: { borderColor: CHART_COLORS.grid, visible: false },
      handleScroll: false,
      handleScale: false,
    });
    macdChartRef.current = chart;

    macdHistSeriesRef.current = chart.addSeries(HistogramSeries, {
      priceLineVisible: false, lastValueVisible: false,
    });
    macdDifSeriesRef.current = chart.addSeries(LineSeries, {
      color: CHART_COLORS.macdDif, lineWidth: 1,
      priceLineVisible: false, lastValueVisible: false, crosshairMarkerVisible: false,
    });
    macdDeaSeriesRef.current = chart.addSeries(LineSeries, {
      color: CHART_COLORS.macdDea, lineWidth: 1,
      priceLineVisible: false, lastValueVisible: false, crosshairMarkerVisible: false,
    });

    // 零轴参考线
    macdDifSeriesRef.current.createPriceLine({
      price: 0, color: 'rgba(255,255,255,0.2)', lineWidth: 1, lineStyle: 2, axisLabelVisible: false,
    });

    // 子图十字光标 → 主图
    chart.subscribeCrosshairMove((param) => {
      if (param.time && chartRef.current && candlestickSeriesRef.current) {
        chartRef.current.setCrosshairPosition(0, param.time, candlestickSeriesRef.current);
        if (rsiChartRef.current && rsiSeriesRef.current) {
          rsiChartRef.current.setCrosshairPosition(50, param.time, rsiSeriesRef.current);
        }
      } else {
        chartRef.current?.clearCrosshairPosition();
        rsiChartRef.current?.clearCrosshairPosition();
      }
    });

    return () => {
      chart.remove();
      macdChartRef.current = null;
      macdDifSeriesRef.current = null;
      macdDeaSeriesRef.current = null;
      macdHistSeriesRef.current = null;
    };
  }, [indicatorConfig.macd.enabled]);

  // === MACD 子图：数据更新 ===
  useEffect(() => {
    if (!macdChartRef.current || !macdDifSeriesRef.current || !macdDeaSeriesRef.current || !macdHistSeriesRef.current || data.length === 0) return;

    const macdData = calculateMACD(data, indicatorConfig.macd.fast, indicatorConfig.macd.slow, indicatorConfig.macd.signal);
    if (macdData.length === 0) return;

    macdDifSeriesRef.current.setData(macdData.map((d) => ({ time: d.time as string, value: d.dif })));
    macdDeaSeriesRef.current.setData(macdData.map((d) => ({ time: d.time as string, value: d.dea })));
    macdHistSeriesRef.current.setData(
      macdData.map((d) => ({
        time: d.time as string,
        value: d.histogram,
        color: d.histogram >= 0 ? CHART_COLORS.macdHistUp : CHART_COLORS.macdHistDown,
      }))
    );

    // 同步主图时间范围
    const mainRange = chartRef.current?.timeScale().getVisibleLogicalRange();
    if (mainRange) {
      macdChartRef.current.timeScale().setVisibleLogicalRange(mainRange);
    }
  }, [data, indicatorConfig.macd]);

  // === RSI 子图：创建/销毁 ===
  useEffect(() => {
    if (!indicatorConfig.rsi.enabled) return;

    const container = rsiChartContainerRef.current;
    if (!container || container.clientWidth === 0) return;

    const chart = createChart(container, {
      width: container.clientWidth,
      height: RSI_HEIGHT,
      layout: {
        background: { type: ColorType.Solid, color: CHART_COLORS.background },
        textColor: CHART_COLORS.text,
      },
      grid: {
        vertLines: { color: CHART_COLORS.grid },
        horzLines: { color: CHART_COLORS.grid },
      },
      crosshair: {
        mode: CrosshairMode.Normal,
        vertLine: { color: CHART_COLORS.crosshair, width: 1, style: 2, labelBackgroundColor: '#2B2B43' },
        horzLine: { color: CHART_COLORS.crosshair, width: 1, style: 2, labelBackgroundColor: '#2B2B43' },
      },
      rightPriceScale: { borderColor: CHART_COLORS.grid },
      timeScale: { borderColor: CHART_COLORS.grid, visible: false },
      handleScroll: false,
      handleScale: false,
    });
    rsiChartRef.current = chart;

    const rsiSeries = chart.addSeries(LineSeries, {
      color: CHART_COLORS.rsi, lineWidth: 1,
      priceLineVisible: false, lastValueVisible: false, crosshairMarkerVisible: false,
    });
    rsiSeriesRef.current = rsiSeries;

    // 超买/超卖参考线
    rsiSeries.createPriceLine({ price: 70, color: 'rgba(239,83,80,0.5)', lineWidth: 1, lineStyle: 2, axisLabelVisible: true, title: '' });
    rsiSeries.createPriceLine({ price: 30, color: 'rgba(38,166,154,0.5)', lineWidth: 1, lineStyle: 2, axisLabelVisible: true, title: '' });
    rsiSeries.createPriceLine({ price: 50, color: 'rgba(255,255,255,0.15)', lineWidth: 1, lineStyle: 2, axisLabelVisible: false });

    // 子图十字光标 → 主图
    chart.subscribeCrosshairMove((param) => {
      if (param.time && chartRef.current && candlestickSeriesRef.current) {
        chartRef.current.setCrosshairPosition(0, param.time, candlestickSeriesRef.current);
        if (macdChartRef.current && macdDifSeriesRef.current) {
          macdChartRef.current.setCrosshairPosition(0, param.time, macdDifSeriesRef.current);
        }
      } else {
        chartRef.current?.clearCrosshairPosition();
        macdChartRef.current?.clearCrosshairPosition();
      }
    });

    return () => {
      chart.remove();
      rsiChartRef.current = null;
      rsiSeriesRef.current = null;
    };
  }, [indicatorConfig.rsi.enabled]);

  // === RSI 子图：数据更新 ===
  useEffect(() => {
    if (!rsiChartRef.current || !rsiSeriesRef.current || data.length === 0) return;

    const rsiData = calculateRSI(data, indicatorConfig.rsi.period);
    if (rsiData.length === 0) return;

    rsiSeriesRef.current.setData(rsiData.map((d) => ({ time: d.time as string, value: d.value })));

    // 同步主图时间范围
    const mainRange = chartRef.current?.timeScale().getVisibleLogicalRange();
    if (mainRange) {
      rsiChartRef.current.timeScale().setVisibleLogicalRange(mainRange);
    }
  }, [data, indicatorConfig.rsi]);

  // === 买卖信号箭头标注 ===
  useEffect(() => {
    if (!candlestickSeriesRef.current || data.length === 0) return;

    if (!indicatorConfig.signals.enabled) {
      // 移除已有的 markers plugin
      if (markersPluginRef.current) {
        markersPluginRef.current.detach();
        markersPluginRef.current = null;
      }
      return;
    }

    const signals = detectSignals(data);
    const markers = signals.map((s) => ({
      time: s.time as string,
      position: s.type === 'buy' ? ('belowBar' as const) : ('aboveBar' as const),
      color: s.type === 'buy' ? '#26a69a' : '#ef5350',
      shape: s.type === 'buy' ? ('arrowUp' as const) : ('arrowDown' as const),
      text: s.reason.length > 12 ? s.reason.slice(0, 12) : s.reason,
    }));

    if (markersPluginRef.current) {
      // 已有 plugin，更新 markers
      markersPluginRef.current.setMarkers(markers);
    } else {
      // 首次创建 plugin
      markersPluginRef.current = createSeriesMarkers(candlestickSeriesRef.current, markers);
    }

    return () => {
      if (markersPluginRef.current) {
        markersPluginRef.current.detach();
        markersPluginRef.current = null;
      }
    };
  }, [data, indicatorConfig.signals.enabled]);

  return (
    <div className="relative">
      <div ref={chartContainerRef} className="w-full" />

      {/* 主图图例 */}
      <div className="absolute top-2 left-2 flex flex-wrap gap-3 text-xs">
        {indicatorConfig.ma.enabled &&
          indicatorConfig.ma.periods.map((period) => (
            <span
              key={period}
              style={{
                color:
                  period === 5
                    ? CHART_COLORS.ma5
                    : period === 10
                    ? CHART_COLORS.ma10
                    : period === 20
                    ? CHART_COLORS.ma20
                    : CHART_COLORS.ma60,
              }}
            >
              MA{period}
            </span>
          ))}
        {indicatorConfig.boll.enabled && (
          <>
            <span style={{ color: CHART_COLORS.bollUpper }}>BOLL上轨</span>
            <span style={{ color: CHART_COLORS.bollMiddle }}>BOLL中轨</span>
            <span style={{ color: CHART_COLORS.bollLower }}>BOLL下轨</span>
          </>
        )}
      </div>

      {/* MACD 子图 */}
      {indicatorConfig.macd.enabled && (
        <div className="relative">
          <div ref={macdChartContainerRef} className="w-full" />
          <div className="absolute top-1 left-2 flex gap-3 text-xs z-10">
            <span style={{ color: CHART_COLORS.macdDif }}>DIF</span>
            <span style={{ color: CHART_COLORS.macdDea }}>DEA</span>
            <span className="text-gray-500">MACD({indicatorConfig.macd.fast},{indicatorConfig.macd.slow},{indicatorConfig.macd.signal})</span>
          </div>
        </div>
      )}

      {/* RSI 子图 */}
      {indicatorConfig.rsi.enabled && (
        <div className="relative">
          <div ref={rsiChartContainerRef} className="w-full" />
          <div className="absolute top-1 left-2 text-xs z-10">
            <span style={{ color: CHART_COLORS.rsi }}>RSI({indicatorConfig.rsi.period})</span>
          </div>
        </div>
      )}
    </div>
  );
};

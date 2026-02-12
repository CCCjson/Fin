import React, { useEffect, useRef, useCallback } from 'react';
import {
  createChart,
  CrosshairMode,
  ColorType,
  CandlestickSeries,
  HistogramSeries,
  LineSeries,
} from 'lightweight-charts';
import type {
  IChartApi,
  ISeriesApi,
  CandlestickData as LWCandlestickData,
  HistogramData,
  LineData,
} from 'lightweight-charts';
import type { StockData } from '../../types';
import type { IndicatorConfig } from '../../types/chart';
import { CHART_COLORS, DEFAULT_INDICATOR_CONFIG } from '../../types/chart';
import { calculateMA, calculateBoll } from '../../utils/indicators';

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

  // 初始化图表
  useEffect(() => {
    if (!chartContainerRef.current) return;

    const container = chartContainerRef.current;

    // 实际创建图表的函数
    const initChart = () => {
      // 防止重复创建
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
          vertLine: {
            color: CHART_COLORS.crosshair,
            width: 1,
            style: 2,
            labelBackgroundColor: '#2B2B43',
          },
          horzLine: {
            color: CHART_COLORS.crosshair,
            width: 1,
            style: 2,
            labelBackgroundColor: '#2B2B43',
          },
        },
        rightPriceScale: {
          borderColor: CHART_COLORS.grid,
          scaleMargins: {
            top: 0.1,
            bottom: 0.25, // 留出成交量空间
          },
        },
        timeScale: {
          borderColor: CHART_COLORS.grid,
          timeVisible: true,
          secondsVisible: false,
        },
      });

      chartRef.current = chart;

      // 创建 K 线系列 (v5 API)
      const candlestickSeries = chart.addSeries(CandlestickSeries, {
        upColor: CHART_COLORS.up,
        downColor: CHART_COLORS.down,
        borderUpColor: CHART_COLORS.up,
        borderDownColor: CHART_COLORS.down,
        wickUpColor: CHART_COLORS.up,
        wickDownColor: CHART_COLORS.down,
      });
      candlestickSeriesRef.current = candlestickSeries;

      // 创建成交量系列 (v5 API)
      const volumeSeries = chart.addSeries(HistogramSeries, {
        priceFormat: {
          type: 'volume',
        },
        priceScaleId: 'volume',
      });

      chart.priceScale('volume').applyOptions({
        scaleMargins: {
          top: 0.8,
          bottom: 0,
        },
      });

      volumeSeriesRef.current = volumeSeries;
    };

    // 如果容器宽度为 0（display:none 等情况），延迟到可见时再初始化
    if (container.clientWidth > 0) {
      initChart();
    }

    // 使用 ResizeObserver 监听容器尺寸变化（替代 window.resize）
    // 能捕获：CSS visibility 切换、侧边栏展开/收起、窗口缩放等所有场景
    const resizeObserver = new ResizeObserver((entries) => {
      for (const entry of entries) {
        const { width } = entry.contentRect;
        if (width > 0) {
          if (!chartRef.current) {
            // 容器从 hidden 变为可见，首次初始化图表
            initChart();
          } else {
            // 容器宽度变化，更新图表宽度
            chartRef.current.applyOptions({ width });
          }
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
    };
  }, [height]);

  // 更新数据
  useEffect(() => {
    if (!chartRef.current || !candlestickSeriesRef.current || !volumeSeriesRef.current || data.length === 0) {
      return;
    }

    const chart = chartRef.current;

    // 更新 K 线数据
    const candlestickData = formatCandlestickData(data);
    candlestickSeriesRef.current.setData(candlestickData);

    // 更新成交量数据
    const volumeData = formatVolumeData(data);
    volumeSeriesRef.current.setData(volumeData);

    // 自动调整时间范围
    chart.timeScale().fitContent();
  }, [data, formatCandlestickData, formatVolumeData]);

  // 更新均线
  useEffect(() => {
    if (!chartRef.current || data.length === 0) return;

    const chart = chartRef.current;
    const maColors: Record<number, string> = {
      5: CHART_COLORS.ma5,
      10: CHART_COLORS.ma10,
      20: CHART_COLORS.ma20,
      60: CHART_COLORS.ma60,
    };

    // 移除旧的均线系列
    maSeriesRefs.current.forEach((series) => {
      chart.removeSeries(series);
    });
    maSeriesRefs.current.clear();

    // 添加新的均线系列
    if (indicatorConfig.ma.enabled) {
      indicatorConfig.ma.periods.forEach((period) => {
        const maData = calculateMA(data, period);
        if (maData.length > 0) {
          const maSeries = chart.addSeries(LineSeries, {
            color: maColors[period] || '#ffffff',
            lineWidth: 1,
            priceLineVisible: false,
            lastValueVisible: false,
            crosshairMarkerVisible: false,
          });

          const lineData: LineData[] = maData.map((item) => ({
            time: item.time as string,
            value: item.value,
          }));

          maSeries.setData(lineData);
          maSeriesRefs.current.set(period, maSeries);
        }
      });
    }
  }, [data, indicatorConfig.ma]);

  // 更新布林带
  useEffect(() => {
    if (!chartRef.current || data.length === 0) return;

    const chart = chartRef.current;

    // 移除旧的布林带系列
    if (bollSeriesRefs.current.upper) {
      chart.removeSeries(bollSeriesRefs.current.upper);
    }
    if (bollSeriesRefs.current.middle) {
      chart.removeSeries(bollSeriesRefs.current.middle);
    }
    if (bollSeriesRefs.current.lower) {
      chart.removeSeries(bollSeriesRefs.current.lower);
    }
    bollSeriesRefs.current = {};

    // 添加新的布林带系列
    if (indicatorConfig.boll.enabled) {
      const bollData = calculateBoll(data, indicatorConfig.boll.period, indicatorConfig.boll.stdDev);

      if (bollData.length > 0) {
        // 上轨
        const upperSeries = chart.addSeries(LineSeries, {
          color: CHART_COLORS.bollUpper,
          lineWidth: 1,
          lineStyle: 2,
          priceLineVisible: false,
          lastValueVisible: false,
          crosshairMarkerVisible: false,
        });
        upperSeries.setData(
          bollData.map((item) => ({
            time: item.time as string,
            value: item.upper,
          }))
        );
        bollSeriesRefs.current.upper = upperSeries;

        // 中轨
        const middleSeries = chart.addSeries(LineSeries, {
          color: CHART_COLORS.bollMiddle,
          lineWidth: 1,
          priceLineVisible: false,
          lastValueVisible: false,
          crosshairMarkerVisible: false,
        });
        middleSeries.setData(
          bollData.map((item) => ({
            time: item.time as string,
            value: item.middle,
          }))
        );
        bollSeriesRefs.current.middle = middleSeries;

        // 下轨
        const lowerSeries = chart.addSeries(LineSeries, {
          color: CHART_COLORS.bollLower,
          lineWidth: 1,
          lineStyle: 2,
          priceLineVisible: false,
          lastValueVisible: false,
          crosshairMarkerVisible: false,
        });
        lowerSeries.setData(
          bollData.map((item) => ({
            time: item.time as string,
            value: item.lower,
          }))
        );
        bollSeriesRefs.current.lower = lowerSeries;
      }
    }
  }, [data, indicatorConfig.boll]);

  return (
    <div className="relative">
      <div ref={chartContainerRef} className="w-full" />

      {/* 图例 */}
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
    </div>
  );
};

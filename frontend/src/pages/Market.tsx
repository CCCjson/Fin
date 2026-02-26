import React, { useState, useEffect, useRef, useCallback } from 'react';
import { useLocation } from 'react-router-dom';
import { StockSymbolInput } from '../components/common/StockSymbolInput';
import { CandlestickChart } from '../components/charts/CandlestickChart';
import { IndicatorPanel } from '../components/charts/IndicatorPanel';
import { marketService } from '../services/marketService';
import type { StockData } from '../types';
import type { IndicatorConfig } from '../types/chart';
import { DEFAULT_INDICATOR_CONFIG } from '../types/chart';

/**
 * 判断缓存数据是否足够新（3 天内视为新鲜，覆盖周末和短假期）
 * 避免不必要的 akshare 网络请求
 */
function isDataFresh(latestDateStr: string, endDateStr: string): boolean {
  if (!latestDateStr || !endDateStr) return false;
  const latest = new Date(latestDateStr);
  const end = new Date(endDateStr);
  const diffDays = (end.getTime() - latest.getTime()) / (86400000);
  return diffDays <= 3;
}

export const Market: React.FC = () => {
  const [symbol, setSymbol] = useState('688576.SH');
  const [data, setData] = useState<StockData[]>([]);
  const [loading, setLoading] = useState(false);
  const [updating, setUpdating] = useState(false);  // 后台静默刷新中
  const [error, setError] = useState<string | null>(null);
  const [startDate, setStartDate] = useState('2025-01-01');
  const [endDate, setEndDate] = useState(new Date().toISOString().split('T')[0]);
  const [indicatorConfig, setIndicatorConfig] = useState<IndicatorConfig>(DEFAULT_INDICATOR_CONFIG);
  const hasLoadedRef = useRef(false);
  const location = useLocation();

  /**
   * 两阶段加载：
   *   阶段1: db_only=true，毫秒级返回缓存数据，先渲染图表
   *   阶段2: db_only=false，后台联网补齐最新数据，补完静默刷新
   */
  const loadData = useCallback(async (forceFullLoad = false) => {
    try {
      setError(null);

      if (!forceFullLoad) {
        // ── 阶段1: 快速加载缓存 ──
        setLoading(true);
        try {
          const cached = await marketService.getDailyData(symbol, startDate, endDate, true);
          if (cached.data && cached.data.length > 0) {
            setData(cached.data);
            hasLoadedRef.current = true;
            setLoading(false);

            // ── 智能判断：缓存足够新则跳过阶段2 ──
            const latestCachedDate = cached.data[cached.data.length - 1]?.date;
            if (isDataFresh(latestCachedDate, endDate)) {
              return; // 数据足够新，无需联网
            }

            // ── 阶段2: 后台静默补齐 ──
            setUpdating(true);
            try {
              const fresh = await marketService.getDailyData(symbol, startDate, endDate, false);
              if (fresh.data && fresh.data.length > 0) {
                setData(fresh.data);
              }
            } catch {
              // 静默刷新失败不影响已展示的缓存数据
              console.warn('后台数据同步失败，使用缓存数据');
            } finally {
              setUpdating(false);
            }
            return;
          }
        } catch {
          // 缓存也失败了，走 fallback
        }
      }

      // ── Fallback / 强制全量加载: 直接联网拉取 ──
      setLoading(true);
      const response = await marketService.getDailyData(symbol, startDate, endDate, false);
      setData(response.data || []);
      hasLoadedRef.current = true;
    } catch (err) {
      console.error('Failed to load data:', err);
      setError(err instanceof Error ? err.message : '加载数据失败，请检查网络连接');
    } finally {
      setLoading(false);
      setUpdating(false);
    }
  }, [symbol, startDate, endDate]);

  // 首次挂载加载
  useEffect(() => {
    loadData();
  }, []);  // eslint-disable-line react-hooks/exhaustive-deps

  // Keep-Alive 模式：导航回本页面时，如果之前加载失败则自动重试
  useEffect(() => {
    if (location.pathname === '/market' && !hasLoadedRef.current && !loading) {
      loadData();
    }
  }, [location.pathname]);  // eslint-disable-line react-hooks/exhaustive-deps

  const latestData = data[data.length - 1];
  const prevData = data[data.length - 2];
  const priceChange = latestData && prevData ? latestData.close - prevData.close : 0;
  const priceChangePct = prevData ? (priceChange / prevData.close) * 100 : 0;

  return (
    <div className="min-h-screen bg-gradient-dark p-3 md:p-6 pb-20 md:pb-6">
      <div className="max-w-7xl mx-auto space-y-4 md:space-y-6">
        <h1 className="text-2xl md:text-3xl font-bold text-white">行情数据</h1>

        {/* 查询表单 */}
        <div className="bg-gradient-card border border-border shadow-card p-4 md:p-6 rounded-lg">
          <div className="grid grid-cols-1 md:grid-cols-4 gap-3 md:gap-4">
            <div>
              <label className="block text-sm font-medium text-gray-300 mb-2">
                股票代码
              </label>
              <StockSymbolInput
                value={symbol}
                onChange={(s) => setSymbol(s)}
                placeholder="输入代码或名称搜索"
              />
            </div>
            <div>
              <label className="block text-sm font-medium text-gray-300 mb-2">
                开始日期
              </label>
              <input
                type="date"
                value={startDate}
                onChange={(e) => setStartDate(e.target.value)}
                className="w-full px-3 py-2 bg-dark-light text-white rounded-lg border border-border focus:border-primary focus:ring-2 focus:ring-primary/20 outline-none"
              />
            </div>
            <div>
              <label className="block text-sm font-medium text-gray-300 mb-2">
                结束日期
              </label>
              <input
                type="date"
                value={endDate}
                onChange={(e) => setEndDate(e.target.value)}
                className="w-full px-3 py-2 bg-dark-light text-white rounded-lg border border-border focus:border-primary focus:ring-2 focus:ring-primary/20 outline-none"
              />
            </div>
            <div className="flex items-end">
              <button
                onClick={() => loadData(true)}
                disabled={loading}
                className="w-full px-4 py-2 bg-primary text-white rounded-xl hover:bg-primary-dark shadow-glow-blue transition disabled:opacity-50"
              >
                {loading ? '加载中...' : '查询'}
              </button>
            </div>
          </div>
        </div>

        {/* 最新数据 */}
        {latestData && (
          <div className="grid grid-cols-3 md:grid-cols-6 gap-2 md:gap-4">
            <div className="bg-gradient-card border border-border shadow-card p-3 md:p-4 rounded-xl hover:shadow-glow-blue transition-all">
              <div className="text-gray-400 text-xs md:text-sm mb-1">最新价</div>
              <div className="text-lg md:text-2xl font-bold text-primary-light">¥{latestData.close.toFixed(2)}</div>
            </div>
            <div className="bg-gradient-card border border-border shadow-card p-3 md:p-4 rounded-xl hover:shadow-glow-blue transition-all">
              <div className="text-gray-400 text-xs md:text-sm mb-1">涨跌幅</div>
              <div className={`text-lg md:text-xl font-semibold ${priceChange >= 0 ? 'text-bull' : 'text-bear'}`}>
                {priceChange >= 0 ? '+' : ''}{priceChangePct.toFixed(2)}%
              </div>
            </div>
            <div className="bg-gradient-card border border-border shadow-card p-3 md:p-4 rounded-xl hover:shadow-glow-blue transition-all">
              <div className="text-gray-400 text-xs md:text-sm mb-1">开盘价</div>
              <div className="text-lg md:text-xl font-semibold text-white">¥{latestData.open.toFixed(2)}</div>
            </div>
            <div className="bg-gradient-card border border-border shadow-card p-3 md:p-4 rounded-xl hover:shadow-glow-green transition-all">
              <div className="text-gray-400 text-xs md:text-sm mb-1">最高价</div>
              <div className="text-lg md:text-xl font-semibold text-bull">¥{latestData.high.toFixed(2)}</div>
            </div>
            <div className="bg-gradient-card border border-border shadow-card p-3 md:p-4 rounded-xl hover:shadow-glow-red transition-all">
              <div className="text-gray-400 text-xs md:text-sm mb-1">最低价</div>
              <div className="text-lg md:text-xl font-semibold text-bear">¥{latestData.low.toFixed(2)}</div>
            </div>
            <div className="bg-gradient-card border border-border shadow-card p-3 md:p-4 rounded-xl hover:shadow-glow-blue transition-all">
              <div className="text-gray-400 text-xs md:text-sm mb-1">成交量</div>
              <div className="text-lg md:text-xl font-semibold text-accent-cyan">{(latestData.volume / 10000).toFixed(2)}万</div>
            </div>
          </div>
        )}

        {/* K线图 */}
        <div className="bg-gradient-card border border-border shadow-card p-3 md:p-6 rounded-lg">
          <div className="flex items-center justify-between mb-3 md:mb-4">
            <h2 className="text-lg md:text-xl font-semibold text-white">K线图</h2>
            {updating && (
              <span className="text-xs text-gray-400 animate-pulse">正在同步最新数据...</span>
            )}
          </div>

          {/* 指标面板 */}
          <div className="mb-3 md:mb-4">
            <IndicatorPanel config={indicatorConfig} onChange={setIndicatorConfig} />
          </div>

          {data.length > 0 ? (
            <>
              {/* 手机端较矮，桌面端较高，用 CSS 切换包裹容器即可，只渲染一个图表实例 */}
              <div className="h-[350px] md:h-[500px]">
                <CandlestickChart
                  data={data}
                  height={typeof window !== 'undefined' && window.innerWidth < 768 ? 350 : 500}
                  indicatorConfig={indicatorConfig}
                />
              </div>
            </>
          ) : (
            <div className="h-64 md:h-96 flex flex-col items-center justify-center gap-3">
              {loading ? (
                <span className="text-gray-400">加载中...</span>
              ) : error ? (
                <>
                  <span className="text-red-400 text-sm text-center px-4">{error}</span>
                  <button
                    onClick={() => loadData(true)}
                    className="px-4 py-1.5 bg-primary text-white rounded-lg hover:bg-primary-dark transition text-sm"
                  >
                    重试
                  </button>
                </>
              ) : (
                <span className="text-gray-400">暂无数据</span>
              )}
            </div>
          )}
        </div>

        {/* 数据表格 - 桌面端 */}
        <div className="hidden md:block bg-gradient-card border border-border shadow-card p-6 rounded-lg">
          <h2 className="text-xl font-semibold text-white mb-4">历史数据</h2>
          <div className="overflow-x-auto">
            <table className="w-full text-sm">
              <thead className="bg-dark-light text-gray-300 border-b border-border">
                <tr>
                  <th className="px-4 py-2 text-left">日期</th>
                  <th className="px-4 py-2 text-right">开盘</th>
                  <th className="px-4 py-2 text-right">最高</th>
                  <th className="px-4 py-2 text-right">最低</th>
                  <th className="px-4 py-2 text-right">收盘</th>
                  <th className="px-4 py-2 text-right">涨跌</th>
                  <th className="px-4 py-2 text-right">成交量</th>
                </tr>
              </thead>
              <tbody className="text-gray-300">
                {data.slice(-20).reverse().map((item, idx, arr) => {
                  const prevItem = idx < arr.length - 1 ? arr[idx + 1] : null;
                  const change = prevItem ? item.close - prevItem.close : 0;
                  const changePct = prevItem ? (change / prevItem.close) * 100 : 0;

                  return (
                    <tr key={idx} className="border-b border-border hover:bg-dark-light transition-colors">
                      <td className="px-4 py-2">{item.date}</td>
                      <td className="px-4 py-2 text-right">¥{item.open.toFixed(2)}</td>
                      <td className="px-4 py-2 text-right text-bull font-medium">¥{item.high.toFixed(2)}</td>
                      <td className="px-4 py-2 text-right text-bear font-medium">¥{item.low.toFixed(2)}</td>
                      <td className="px-4 py-2 text-right font-semibold text-primary-light">¥{item.close.toFixed(2)}</td>
                      <td className={`px-4 py-2 text-right font-medium ${change >= 0 ? 'text-bull' : 'text-bear'}`}>
                        {change >= 0 ? '+' : ''}{changePct.toFixed(2)}%
                      </td>
                      <td className="px-4 py-2 text-right text-accent-cyan">{(item.volume / 10000).toFixed(2)}万</td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        </div>

        {/* 数据表格 - 手机端卡片 */}
        <div className="md:hidden bg-gradient-card border border-border shadow-card p-3 rounded-lg">
          <h2 className="text-lg font-semibold text-white mb-3">历史数据</h2>
          <div className="space-y-1.5">
            {data.slice(-20).reverse().map((item, idx, arr) => {
              const prevItem = idx < arr.length - 1 ? arr[idx + 1] : null;
              const change = prevItem ? item.close - prevItem.close : 0;
              const changePct = prevItem ? (change / prevItem.close) * 100 : 0;

              return (
                <div key={idx} className="flex items-center justify-between py-2 px-2 rounded-lg hover:bg-dark-light/50">
                  <div>
                    <div className="text-xs text-gray-500">{item.date}</div>
                    <div className="text-sm font-semibold text-primary-light">¥{item.close.toFixed(2)}</div>
                  </div>
                  <div className="text-right">
                    <div className={`text-sm font-medium ${change >= 0 ? 'text-bull' : 'text-bear'}`}>
                      {change >= 0 ? '+' : ''}{changePct.toFixed(2)}%
                    </div>
                    <div className="text-xs text-gray-500">量 {(item.volume / 10000).toFixed(1)}万</div>
                  </div>
                </div>
              );
            })}
          </div>
        </div>
      </div>
    </div>
  );
};

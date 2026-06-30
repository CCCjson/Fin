import React, { useState, useEffect, useRef } from 'react';
import { useNavigate } from 'react-router-dom';
import { StockSymbolInput } from '../components/common/StockSymbolInput';
import { signalService } from '../services/signalService';
import type { ScanProgressEvent, DataFreshness, TodayStatus, BackfillProgressEvent } from '../services/signalService';
import type { Signal, SignalStatistics } from '../types';
import { Card } from '../components/common/Card';

interface ScanProgress {
  current: number;
  total: number;
  symbol: string;
  totalSignals: number;
  buySignals: number;
  sellSignals: number;
  failed: number;
}

interface BackfillProgress {
  currentDay: number;
  totalDays: number;
  currentDate: string;
  cumulativeSignals: number;
  daySignals: number;
  dayBuy: number;
  daySell: number;
}

export const Signals: React.FC = () => {
  const navigate = useNavigate();
  const [signals, setSignals] = useState<Signal[]>([]);
  const [statistics, setStatistics] = useState<SignalStatistics | null>(null);
  const [loading, setLoading] = useState(false);
  const [scanning, setScanning] = useState(false);
  const [scanResult, setScanResult] = useState<string | null>(null);
  const [scanProgress, setScanProgress] = useState<ScanProgress | null>(null);
  const [symbol, setSymbol] = useState('');
  const [signalType, setSignalType] = useState('');
  const [startDate, setStartDate] = useState('2010-01-01');
  const [endDate, setEndDate] = useState(new Date().toISOString().split('T')[0]);
  const [page, setPage] = useState(1);
  const [pageSize] = useState(50);
  const [totalSignals, setTotalSignals] = useState(0);
  const abortControllerRef = useRef<AbortController | null>(null);
  const latestProgressRef = useRef<ScanProgress | null>(null);
  const rafIdRef = useRef<number>(0);
  const [freshness, setFreshness] = useState<DataFreshness | null>(null);
  const [showStaleAlert, setShowStaleAlert] = useState(false);
  const [checkingFreshness, setCheckingFreshness] = useState(false);
  const [, setTodayStatus] = useState<TodayStatus | null>(null);
  const [backfilling, setBackfilling] = useState(false);
  const [backfillProgress, setBackfillProgress] = useState<BackfillProgress | null>(null);
  const [backfillResult, setBackfillResult] = useState<string | null>(null);
  const [gapCount, setGapCount] = useState<number | null>(null);
  const backfillAbortRef = useRef<AbortController | null>(null);

  useEffect(() => {
    loadData();
  }, []);

  const loadData = async (targetPage?: number) => {
    try {
      setLoading(true);
      const currentPage = targetPage ?? page;
      const offset = (currentPage - 1) * pageSize;

      const params = {
        symbol: symbol || undefined,
        signal_type: signalType || undefined,
        start_date: startDate,
        end_date: endDate,
        limit: pageSize,
        offset,
      };

      const [signalsRes, statsRes] = await Promise.all([
        signalService.getSignals(params),
        signalService.getStatistics({
          symbol: symbol || undefined,
        }),
      ]);

      setSignals(signalsRes?.signals || []);
      setTotalSignals(signalsRes?.total || 0);
      setStatistics(statsRes || null);
    } catch (error) {
      console.error('Failed to load signals:', error);
    } finally {
      setLoading(false);
    }
  };

  const totalPages = Math.max(1, Math.ceil(totalSignals / pageSize));

  const handlePageChange = (newPage: number) => {
    if (newPage < 1 || newPage > totalPages || newPage === page) return;
    setPage(newPage);
    loadData(newPage);
  };

  const handleSearch = () => {
    setPage(1);
    loadData(1);
  };

  const handleScanMarket = async () => {
    // 先检查数据新鲜度
    try {
      setCheckingFreshness(true);
      setScanResult(null);
      setTodayStatus(null);
      const data = await signalService.getDataFreshness();
      setFreshness(data);
      if (data.is_stale) {
        // 数据过期，弹出提醒让用户选择
        setShowStaleAlert(true);
        return;
      }
      // 数据新鲜，检查今日是否已生成信号（防重复扫描）
      const status = await signalService.getTodayStatus();
      setTodayStatus(status);
      if (status.has_today_signals) {
        setScanResult(`今日信号已生成（${status.signal_count} 个），无需重复扫描`);
        return;
      }
      // 今日尚无信号，执行扫描
      await startScan(true);
    } catch (error) {
      console.error('Failed to check data freshness:', error);
      // 检查失败也允许继续扫描
      await startScan(true);
    } finally {
      setCheckingFreshness(false);
    }
  };

  const startScan = async (dbOnly: boolean) => {
    setShowStaleAlert(false);
    try {
      setScanning(true);
      setScanResult(null);
      setScanProgress(null);

      const abortController = new AbortController();
      abortControllerRef.current = abortController;

      await signalService.scanMarketStream(
        { lookback_days: 60, save_to_db: true, db_only: dbOnly },
        (event: ScanProgressEvent) => {
          if (event.event === 'start') {
            const initial: ScanProgress = {
              current: 0,
              total: event.total || 0,
              symbol: '',
              totalSignals: 0,
              buySignals: 0,
              sellSignals: 0,
              failed: 0,
            };
            latestProgressRef.current = initial;
            setScanProgress(initial);
          } else if (event.event === 'progress') {
            // 只写 ref，用 rAF 节流渲染，避免高频 setState 被 batch 导致进度条卡住
            latestProgressRef.current = {
              current: event.current || 0,
              total: event.total || 0,
              symbol: event.symbol || '',
              totalSignals: event.cumulative?.total_signals || 0,
              buySignals: event.cumulative?.buy_signals || 0,
              sellSignals: event.cumulative?.sell_signals || 0,
              failed: event.cumulative?.failed || 0,
            };
            if (!rafIdRef.current) {
              rafIdRef.current = requestAnimationFrame(() => {
                rafIdRef.current = 0;
                setScanProgress(latestProgressRef.current);
              });
            }
          } else if (event.event === 'complete') {
            // 取消待执行的 rAF，立即刷新到最终状态
            if (rafIdRef.current) {
              cancelAnimationFrame(rafIdRef.current);
              rafIdRef.current = 0;
            }
            setScanProgress(latestProgressRef.current);
            setScanResult(
              `扫描完成！共扫描 ${event.symbols_scanned} 只股票，生成 ${event.total_signals} 个信号（买入: ${event.buy_signals}, 卖出: ${event.sell_signals}${event.failed ? `，失败: ${event.failed}` : ''}）`
            );
          }
        },
        abortController.signal,
      );

      // 刷新数据，回到第一页
      setPage(1);
      await loadData(1);
    } catch (error: unknown) {
      if (error instanceof Error && error.name === 'AbortError') {
        setScanResult('扫描已取消');
      } else {
        console.error('Failed to scan market:', error);
        setScanResult('扫描失败，请重试');
      }
    } finally {
      if (rafIdRef.current) {
        cancelAnimationFrame(rafIdRef.current);
        rafIdRef.current = 0;
      }
      latestProgressRef.current = null;
      setScanning(false);
      setScanProgress(null);
      abortControllerRef.current = null;
    }
  };

  const handleCancelScan = () => {
    abortControllerRef.current?.abort();
  };

  // 检测信号缺口
  const checkGaps = async () => {
    try {
      const gaps = await signalService.detectGaps(30);
      setGapCount(gaps.count);
    } catch {
      setGapCount(null);
    }
  };

  // 启动时检测缺口
  useEffect(() => {
    checkGaps();
  }, []);

  // 信号回补
  const handleBackfill = async () => {
    try {
      setBackfilling(true);
      setBackfillResult(null);
      setBackfillProgress(null);

      const abortController = new AbortController();
      backfillAbortRef.current = abortController;

      await signalService.backfillStream(
        { lookback_days: 30, save_to_db: true, db_only: true },
        (event: BackfillProgressEvent) => {
          if (event.event === 'start') {
            setBackfillProgress({
              currentDay: 0,
              totalDays: event.missing_dates?.length || 0,
              currentDate: '',
              cumulativeSignals: 0,
              daySignals: 0,
              dayBuy: 0,
              daySell: 0,
            });
          } else if (event.event === 'day_complete') {
            setBackfillProgress({
              currentDay: event.day_index || 0,
              totalDays: event.total_days || 0,
              currentDate: event.date || '',
              cumulativeSignals: event.cumulative_signals || 0,
              daySignals: event.day_signals || 0,
              dayBuy: event.day_buy || 0,
              daySell: event.day_sell || 0,
            });
          } else if (event.event === 'complete') {
            setBackfillResult(
              event.backfilled_days === 0
                ? event.message || '无需回补'
                : `回补完成！共回补 ${event.backfilled_days} 天，生成 ${event.total_signals} 个信号（买入: ${event.buy_signals}, 卖出: ${event.sell_signals}）`
            );
            setGapCount(0);
          }
        },
        abortController.signal,
      );

      setPage(1);
      await loadData(1);
    } catch (error: unknown) {
      if (error instanceof Error && error.name === 'AbortError') {
        setBackfillResult('回补已取消');
      } else {
        setBackfillResult('回补失败，请重试');
      }
    } finally {
      setBackfilling(false);
      setBackfillProgress(null);
      backfillAbortRef.current = null;
    }
  };

  const handleCancelBackfill = () => {
    backfillAbortRef.current?.abort();
  };

  const getStrengthColor = (strength: number) => {
    if (strength >= 0.8) return 'text-bull';
    if (strength >= 0.6) return 'text-primary-light';
    if (strength >= 0.4) return 'text-accent-orange';
    return 'text-gray-400';
  };

  const getStrengthLabel = (strength: number) => {
    if (strength >= 0.8) return '强';
    if (strength >= 0.6) return '中';
    if (strength >= 0.4) return '弱';
    return '极弱';
  };

  return (
    <div className="min-h-screen bg-gradient-dark p-3 md:p-6 pb-20 md:pb-6">
      <div className="max-w-7xl mx-auto space-y-4 md:space-y-6">
        <div className="flex flex-col gap-2 md:flex-row md:items-center md:justify-between">
          <h1 className="text-2xl md:text-3xl font-bold text-white">信号分析</h1>
          <div className="flex items-center gap-2 md:gap-3">
            {scanning && (
              <button
                onClick={handleCancelScan}
                className="px-3 md:px-4 py-1.5 md:py-2 bg-red-500/20 text-red-400 border border-red-500/30 rounded-xl hover:bg-red-500/30 transition-all flex items-center gap-2 text-sm"
              >
                <svg className="h-4 w-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                  <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M6 18L18 6M6 6l12 12" />
                </svg>
                取消
              </button>
            )}
            {/* 信号回补按钮 */}
            {backfilling && (
              <button
                onClick={handleCancelBackfill}
                className="px-3 md:px-4 py-1.5 md:py-2 bg-red-500/20 text-red-400 border border-red-500/30 rounded-xl hover:bg-red-500/30 transition-all flex items-center gap-2 text-sm"
              >
                <svg className="h-4 w-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                  <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M6 18L18 6M6 6l12 12" />
                </svg>
                取消
              </button>
            )}
            <button
              onClick={handleBackfill}
              disabled={backfilling || scanning || gapCount === 0}
              className="px-3 md:px-4 py-1.5 md:py-2 bg-gradient-to-r from-amber-500 to-orange-600 text-white rounded-xl hover:from-amber-600 hover:to-orange-700 shadow-lg shadow-amber-500/25 transition-all disabled:opacity-50 disabled:cursor-not-allowed flex items-center gap-2 text-sm"
            >
              {backfilling ? (
                <>
                  <svg className="animate-spin h-4 w-4" viewBox="0 0 24 24">
                    <circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4" fill="none" />
                    <path className="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4zm2 5.291A7.962 7.962 0 014 12H0c0 3.042 1.135 5.824 3 7.938l3-2.647z" />
                  </svg>
                  回补中...
                </>
              ) : (
                <>
                  <svg className="h-4 w-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                    <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M12 8v4l3 3m6-3a9 9 0 11-18 0 9 9 0 0118 0z" />
                  </svg>
                  信号回补
                  {gapCount !== null && gapCount > 0 && (
                    <span className="bg-white/20 px-1.5 py-0.5 rounded-md text-xs">{gapCount}天</span>
                  )}
                </>
              )}
            </button>
            <button
              onClick={handleScanMarket}
              disabled={scanning || checkingFreshness || backfilling}
              className="px-4 md:px-6 py-1.5 md:py-2 bg-gradient-to-r from-green-500 to-emerald-600 text-white rounded-xl hover:from-green-600 hover:to-emerald-700 shadow-lg shadow-green-500/25 transition-all disabled:opacity-50 disabled:cursor-not-allowed flex items-center gap-2 text-sm md:text-base"
            >
              {scanning || checkingFreshness ? (
                <>
                  <svg className="animate-spin h-4 w-4 md:h-5 md:w-5" viewBox="0 0 24 24">
                    <circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4" fill="none" />
                    <path className="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4zm2 5.291A7.962 7.962 0 014 12H0c0 3.042 1.135 5.824 3 7.938l3-2.647z" />
                  </svg>
                  {checkingFreshness ? '检查中...' : '扫描中...'}
                </>
              ) : (
                <>
                  <svg className="h-4 w-4 md:h-5 md:w-5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                    <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M4 4v5h.582m15.356 2A8.001 8.001 0 004.582 9m0 0H9m11 11v-5h-.581m0 0a8.003 8.003 0 01-15.357-2m15.357 2H15" />
                  </svg>
                  获取今日信号
                </>
              )}
            </button>
          </div>
        </div>

        {/* 扫描进度条 */}
        {scanning && scanProgress && scanProgress.total > 0 && (
          <Card className="p-5 space-y-3">
            <div className="flex items-center justify-between text-sm">
              <span className="text-gray-300">
                正在扫描: <span className="text-primary-light font-medium">{scanProgress.symbol}</span>
              </span>
              <span className="text-gray-400">
                {scanProgress.current} / {scanProgress.total}
                <span className="ml-2 text-white font-medium">
                  {Math.round((scanProgress.current / scanProgress.total) * 100)}%
                </span>
              </span>
            </div>
            {/* 进度条 */}
            <div className="w-full h-3 bg-dark-light rounded-full overflow-hidden">
              <div
                className="h-full bg-gradient-to-r from-green-500 to-emerald-400 rounded-full transition-all duration-300 ease-out"
                style={{ width: `${(scanProgress.current / scanProgress.total) * 100}%` }}
              />
            </div>
            {/* 实时统计 */}
            <div className="flex items-center gap-6 text-xs text-gray-400">
              <span>
                信号: <span className="text-white font-medium">{scanProgress.totalSignals}</span>
              </span>
              <span>
                买入: <span className="text-bull font-medium">{scanProgress.buySignals}</span>
              </span>
              <span>
                卖出: <span className="text-bear font-medium">{scanProgress.sellSignals}</span>
              </span>
              {scanProgress.failed > 0 && (
                <span>
                  失败: <span className="text-red-400 font-medium">{scanProgress.failed}</span>
                </span>
              )}
            </div>
          </Card>
        )}

        {/* 信号回补进度条 */}
        {backfilling && backfillProgress && backfillProgress.totalDays > 0 && (
          <div className="bg-gradient-card border border-amber-500/30 shadow-card p-5 rounded-xl space-y-3">
            <div className="flex items-center justify-between text-sm">
              <span className="text-gray-300">
                正在回补: <span className="text-amber-400 font-medium">{backfillProgress.currentDate}</span>
              </span>
              <span className="text-gray-400">
                {backfillProgress.currentDay} / {backfillProgress.totalDays} 天
                <span className="ml-2 text-white font-medium">
                  {Math.round((backfillProgress.currentDay / backfillProgress.totalDays) * 100)}%
                </span>
              </span>
            </div>
            <div className="w-full h-3 bg-dark-light rounded-full overflow-hidden">
              <div
                className="h-full bg-gradient-to-r from-amber-500 to-orange-400 rounded-full transition-all duration-300 ease-out"
                style={{ width: `${(backfillProgress.currentDay / backfillProgress.totalDays) * 100}%` }}
              />
            </div>
            <div className="flex items-center gap-6 text-xs text-gray-400">
              <span>
                当日信号: <span className="text-white font-medium">{backfillProgress.daySignals}</span>
                <span className="ml-1 text-bull">({backfillProgress.dayBuy}买</span>
                <span className="text-bear"> {backfillProgress.daySell}卖)</span>
              </span>
              <span>
                累计: <span className="text-amber-400 font-medium">{backfillProgress.cumulativeSignals}</span>
              </span>
            </div>
          </div>
        )}

        {/* 回补结果提示 */}
        {backfillResult && (
          <div className={`p-4 rounded-xl border ${
            backfillResult.includes('失败') || backfillResult.includes('取消')
              ? 'bg-red-500/10 border-red-500/30 text-red-400'
              : backfillResult.includes('无需')
              ? 'bg-primary/10 border-primary/40 text-primary-light'
              : 'bg-amber-500/10 border-amber-500/30 text-amber-300'
          }`}>
            {backfillResult}
          </div>
        )}

        {/* 数据过期提醒 */}
        {showStaleAlert && freshness && (
          <div className="bg-amber-500/10 border border-amber-500/30 p-5 rounded-xl space-y-3">
            <div className="flex items-start gap-3">
              <svg className="h-5 w-5 text-amber-400 mt-0.5 flex-shrink-0" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M12 9v2m0 4h.01m-6.938 4h13.856c1.54 0 2.502-1.667 1.732-2.5L13.732 4c-.77-.833-1.964-.833-2.732 0L4.082 16.5c-.77.833.192 2.5 1.732 2.5z" />
              </svg>
              <div className="flex-1">
                <p className="text-amber-300 font-medium">
                  行情数据截止到 {freshness.latest_date || '无数据'}，请先更新行情
                </p>
                <p className="text-amber-400/70 text-sm mt-1">
                  前往「信号追踪」页面点击「更新行情」按钮更新全市场数据，或直接用现有数据扫描
                </p>
              </div>
              <button
                onClick={() => setShowStaleAlert(false)}
                className="text-amber-400/60 hover:text-amber-300 transition-colors"
              >
                <svg className="h-5 w-5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                  <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M6 18L18 6M6 6l12 12" />
                </svg>
              </button>
            </div>
            <div className="flex items-center gap-3 pl-8">
              <button
                onClick={() => startScan(true)}
                className="px-4 py-2 bg-green-500/20 text-green-400 border border-green-500/30 rounded-lg hover:bg-green-500/30 transition-all text-sm font-medium"
              >
                使用现有数据扫描
              </button>
              <button
                onClick={() => navigate('/tracking')}
                className="px-4 py-2 bg-amber-500/20 text-amber-400 border border-amber-500/30 rounded-lg hover:bg-amber-500/30 transition-all text-sm font-medium"
              >
                前往更新行情
              </button>
            </div>
          </div>
        )}

        {/* 扫描结果提示 */}
        {scanResult && (
          <div className={`p-4 rounded-xl border ${
            scanResult.includes('失败') || scanResult.includes('取消')
              ? 'bg-red-500/10 border-red-500/30 text-red-400'
              : scanResult.includes('无需重复')
              ? 'bg-primary/10 border-primary/40 text-primary-light'
              : 'bg-green-500/10 border-green-500/30 text-green-400'
          }`}>
            {scanResult}
          </div>
        )}

        {/* 统计卡片 */}
        {statistics && (
          <div className="grid grid-cols-2 md:grid-cols-4 gap-2 md:gap-4">
            <Card glow className="p-3 md:p-6">
              <div className="text-gray-400 text-xs md:text-sm mb-1 md:mb-2">总信号数</div>
              <div className="text-xl md:text-3xl font-bold text-primary-light">
                {statistics.total_signals || 0}
              </div>
            </Card>
            <Card className="p-3 md:p-6 hover:shadow-glow-green transition-all">
              <div className="text-gray-400 text-xs md:text-sm mb-1 md:mb-2">买入信号</div>
              <div className="text-xl md:text-3xl font-bold text-bull">
                {statistics.buy_signals || 0}
              </div>
            </Card>
            <Card className="p-3 md:p-6 hover:shadow-glow-red transition-all">
              <div className="text-gray-400 text-xs md:text-sm mb-1 md:mb-2">卖出信号</div>
              <div className="text-xl md:text-3xl font-bold text-bear">
                {statistics.sell_signals || 0}
              </div>
            </Card>
            <Card glow className="p-3 md:p-6 col-span-2 md:col-span-1">
              <div className="text-gray-400 text-xs md:text-sm mb-1 md:mb-2">平均强度</div>
              <div className="text-xl md:text-3xl font-bold text-accent-cyan">
                {statistics.avg_strength ? (statistics.avg_strength * 100).toFixed(1) : 0}%
              </div>
            </Card>
          </div>
        )}

        {/* 筛选表单 */}
        <Card className="p-4 md:p-6">
          <div className="grid grid-cols-1 md:grid-cols-5 gap-4">
            <div>
              <label className="block text-sm font-medium text-gray-300 mb-2">
                股票代码
              </label>
              <StockSymbolInput
                value={symbol}
                onChange={(s) => setSymbol(s)}
                placeholder="全部"
              />
            </div>
            <div>
              <label className="block text-sm font-medium text-gray-300 mb-2">
                信号类型
              </label>
              <select
                value={signalType}
                onChange={(e) => setSignalType(e.target.value)}
                className="w-full px-3 py-2 bg-dark-light text-white rounded-lg border border-border focus:border-primary focus:ring-2 focus:ring-primary/20 outline-none transition-all"
              >
                <option value="">全部</option>
                <option value="BUY">买入</option>
                <option value="SELL">卖出</option>
              </select>
            </div>
            <div>
              <label className="block text-sm font-medium text-gray-300 mb-2">
                开始日期
              </label>
              <input
                type="date"
                value={startDate}
                onChange={(e) => setStartDate(e.target.value)}
                className="w-full px-3 py-2 bg-dark-light text-white rounded-lg border border-border focus:border-primary focus:ring-2 focus:ring-primary/20 outline-none transition-all"
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
                className="w-full px-3 py-2 bg-dark-light text-white rounded-lg border border-border focus:border-primary focus:ring-2 focus:ring-primary/20 outline-none transition-all"
              />
            </div>
            <div className="flex items-end">
              <button
                onClick={handleSearch}
                disabled={loading}
                className="w-full px-4 py-2 bg-primary text-dark rounded-xl hover:bg-primary-dark shadow-glow-blue transition-all disabled:opacity-50"
              >
                {loading ? '查询中...' : '查询'}
              </button>
            </div>
          </div>
        </Card>

        {/* 信号列表 */}
        <Card className="p-3 md:p-6">
          <div className="flex items-center justify-between mb-3 md:mb-4">
            <h2 className="text-lg md:text-xl font-semibold text-white">信号历史</h2>
            {totalSignals > 0 && (
              <span className="text-xs md:text-sm text-gray-400">
                共 <span className="text-white font-medium">{totalSignals}</span> 条
              </span>
            )}
          </div>

          {/* 桌面端表格 */}
          <div className="hidden md:block overflow-x-auto rounded-lg border border-border">
            <table className="w-full text-sm">
              <thead className="bg-dark-light text-gray-300 border-b border-border">
                <tr>
                  <th className="px-4 py-3 text-left">时间</th>
                  <th className="px-4 py-3 text-left">股票</th>
                  <th className="px-4 py-3 text-center">类型</th>
                  <th className="px-4 py-3 text-center">强度</th>
                  <th className="px-4 py-3 text-right">价格</th>
                  <th className="px-4 py-3 text-right">止损</th>
                  <th className="px-4 py-3 text-right">止盈</th>
                  <th className="px-4 py-3 text-left">策略</th>
                </tr>
              </thead>
              <tbody className="text-gray-300">
                {signals.length === 0 ? (
                  <tr>
                    <td colSpan={8} className="px-4 py-8 text-center text-gray-500">
                      {loading ? '加载中...' : '暂无信号数据，点击"获取今日信号"开始扫描'}
                    </td>
                  </tr>
                ) : (
                  signals.map((signal) => (
                    <tr
                      key={signal.id}
                      className="border-b border-border hover:bg-dark-light transition-colors"
                    >
                      <td className="px-4 py-3">{signal.date}</td>
                      <td className="px-4 py-3 font-medium text-white">
                        {signal.symbol}
                      </td>
                      <td className="px-4 py-3 text-center">
                        <span
                          className={`px-3 py-1 rounded-lg text-xs font-semibold ${
                            signal.signal_type === 'BUY'
                              ? 'bg-bull/20 text-bull border border-bull/30'
                              : 'bg-bear/20 text-bear border border-bear/30'
                          }`}
                        >
                          {signal.signal_type === 'BUY' ? '买入' : '卖出'}
                        </span>
                      </td>
                      <td className="px-4 py-3 text-center">
                        <div className="flex items-center justify-center gap-2">
                          <div className="w-16 h-2 bg-dark-light rounded-full overflow-hidden">
                            <div
                              className={`h-full ${getStrengthColor(signal.strength)} bg-current`}
                              style={{ width: `${signal.strength * 100}%` }}
                            />
                          </div>
                          <span className={`text-xs font-semibold ${getStrengthColor(signal.strength)}`}>
                            {getStrengthLabel(signal.strength)}
                          </span>
                        </div>
                      </td>
                      <td className="px-4 py-3 text-right text-primary-light">
                        ¥{signal.price.toFixed(2)}
                      </td>
                      <td className="px-4 py-3 text-right text-bear">
                        {signal.stop_loss ? `¥${signal.stop_loss.toFixed(2)}` : '-'}
                      </td>
                      <td className="px-4 py-3 text-right text-bull">
                        {signal.take_profit ? `¥${signal.take_profit.toFixed(2)}` : '-'}
                      </td>
                      <td className="px-4 py-3">
                        <span className="text-accent-purple text-xs">
                          {signal.strategy || '-'}
                        </span>
                      </td>
                    </tr>
                  ))
                )}
              </tbody>
            </table>
          </div>

          {/* 手机端卡片列表 */}
          <div className="md:hidden space-y-2">
            {signals.length === 0 ? (
              <div className="py-8 text-center text-gray-500 text-sm">
                {loading ? '加载中...' : '暂无信号数据，点击"获取今日信号"开始扫描'}
              </div>
            ) : (
              signals.map((signal) => (
                <div
                  key={signal.id}
                  className="bg-dark-light/30 rounded-lg p-3 border border-border/50"
                >
                  <div className="flex items-center justify-between mb-2">
                    <div className="flex items-center gap-2">
                      <span className="text-white font-medium text-sm">{signal.symbol}</span>
                      <span
                        className={`px-2 py-0.5 rounded text-[10px] font-semibold ${
                          signal.signal_type === 'BUY'
                            ? 'bg-bull/20 text-bull'
                            : 'bg-bear/20 text-bear'
                        }`}
                      >
                        {signal.signal_type === 'BUY' ? '买入' : '卖出'}
                      </span>
                      <span className={`text-[10px] font-semibold ${getStrengthColor(signal.strength)}`}>
                        {getStrengthLabel(signal.strength)}
                      </span>
                    </div>
                    <span className="text-primary-light text-sm font-medium">¥{signal.price.toFixed(2)}</span>
                  </div>
                  <div className="flex items-center justify-between text-xs text-gray-500">
                    <span>{signal.date}</span>
                    <div className="flex gap-3">
                      {signal.stop_loss && <span className="text-bear">止损 ¥{signal.stop_loss.toFixed(2)}</span>}
                      {signal.take_profit && <span className="text-bull">止盈 ¥{signal.take_profit.toFixed(2)}</span>}
                    </div>
                  </div>
                  {signal.strategy && (
                    <div className="text-accent-purple text-[10px] mt-1">{signal.strategy}</div>
                  )}
                </div>
              ))
            )}
          </div>

          {/* 分页控件 - 桌面端 */}
          {totalSignals > pageSize && (
            <div className="hidden md:flex items-center justify-between mt-4 pt-4 border-t border-border">
              <span className="text-sm text-gray-400">
                第 {(page - 1) * pageSize + 1}-{Math.min(page * pageSize, totalSignals)} 条，共 {totalSignals} 条
              </span>
              <div className="flex items-center gap-2">
                <button
                  onClick={() => handlePageChange(1)}
                  disabled={page === 1 || loading}
                  className="px-3 py-1.5 text-sm bg-dark-light text-gray-300 rounded-lg border border-border hover:bg-border disabled:opacity-30 disabled:cursor-not-allowed transition-all"
                >
                  首页
                </button>
                <button
                  onClick={() => handlePageChange(page - 1)}
                  disabled={page === 1 || loading}
                  className="px-3 py-1.5 text-sm bg-dark-light text-gray-300 rounded-lg border border-border hover:bg-border disabled:opacity-30 disabled:cursor-not-allowed transition-all"
                >
                  上一页
                </button>
                {(() => {
                  const pages: number[] = [];
                  let start = Math.max(1, page - 2);
                  let end = Math.min(totalPages, page + 2);
                  if (end - start < 4) {
                    if (start === 1) end = Math.min(totalPages, start + 4);
                    else start = Math.max(1, end - 4);
                  }
                  for (let i = start; i <= end; i++) pages.push(i);
                  return pages.map((p) => (
                    <button
                      key={p}
                      onClick={() => handlePageChange(p)}
                      disabled={loading}
                      className={`px-3 py-1.5 text-sm rounded-lg border transition-all ${
                        p === page
                          ? 'bg-primary text-dark border-primary shadow-glow-blue'
                          : 'bg-dark-light text-gray-300 border-border hover:bg-border'
                      } disabled:cursor-not-allowed`}
                    >
                      {p}
                    </button>
                  ));
                })()}
                <button
                  onClick={() => handlePageChange(page + 1)}
                  disabled={page === totalPages || loading}
                  className="px-3 py-1.5 text-sm bg-dark-light text-gray-300 rounded-lg border border-border hover:bg-border disabled:opacity-30 disabled:cursor-not-allowed transition-all"
                >
                  下一页
                </button>
                <button
                  onClick={() => handlePageChange(totalPages)}
                  disabled={page === totalPages || loading}
                  className="px-3 py-1.5 text-sm bg-dark-light text-gray-300 rounded-lg border border-border hover:bg-border disabled:opacity-30 disabled:cursor-not-allowed transition-all"
                >
                  末页
                </button>
              </div>
            </div>
          )}

          {/* 分页控件 - 手机端 */}
          {totalSignals > pageSize && (
            <div className="md:hidden flex items-center justify-between mt-3 pt-3 border-t border-border">
              <button
                onClick={() => handlePageChange(page - 1)}
                disabled={page === 1 || loading}
                className="px-4 py-2 text-sm bg-dark-light text-gray-300 rounded-lg border border-border active:bg-border disabled:opacity-30"
              >
                上一页
              </button>
              <span className="text-xs text-gray-500">{page} / {totalPages}</span>
              <button
                onClick={() => handlePageChange(page + 1)}
                disabled={page === totalPages || loading}
                className="px-4 py-2 text-sm bg-dark-light text-gray-300 rounded-lg border border-border active:bg-border disabled:opacity-30"
              >
                下一页
              </button>
            </div>
          )}
        </Card>
      </div>
    </div>
  );
};

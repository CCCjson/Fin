import React, { useState, useEffect, useRef } from 'react';
import { useNavigate } from 'react-router-dom';
import { StockSymbolInput } from '../components/common/StockSymbolInput';
import { signalService } from '../services/signalService';
import type { ScanProgressEvent, DataFreshness } from '../services/signalService';
import type { Signal, SignalStatistics } from '../types';

interface ScanProgress {
  current: number;
  total: number;
  symbol: string;
  totalSignals: number;
  buySignals: number;
  sellSignals: number;
  failed: number;
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
  const [startDate, setStartDate] = useState('2025-01-01');
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
      const data = await signalService.getDataFreshness();
      setFreshness(data);
      if (data.is_stale) {
        // 数据过期，弹出提醒让用户选择
        setShowStaleAlert(true);
        return;
      }
      // 数据新鲜，直接用 db_only 模式扫描
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
    <div className="min-h-screen bg-gradient-dark p-6">
      <div className="max-w-7xl mx-auto space-y-6">
        <div className="flex items-center justify-between">
          <h1 className="text-3xl font-bold text-white">信号分析</h1>
          <div className="flex items-center gap-3">
            {scanning && (
              <button
                onClick={handleCancelScan}
                className="px-4 py-2 bg-red-500/20 text-red-400 border border-red-500/30 rounded-xl hover:bg-red-500/30 transition-all flex items-center gap-2"
              >
                <svg className="h-4 w-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                  <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M6 18L18 6M6 6l12 12" />
                </svg>
                取消
              </button>
            )}
            <button
              onClick={handleScanMarket}
              disabled={scanning || checkingFreshness}
              className="px-6 py-2 bg-gradient-to-r from-green-500 to-emerald-600 text-white rounded-xl hover:from-green-600 hover:to-emerald-700 shadow-lg shadow-green-500/25 transition-all disabled:opacity-50 disabled:cursor-not-allowed flex items-center gap-2"
            >
              {scanning || checkingFreshness ? (
                <>
                  <svg className="animate-spin h-5 w-5" viewBox="0 0 24 24">
                    <circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4" fill="none" />
                    <path className="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4zm2 5.291A7.962 7.962 0 014 12H0c0 3.042 1.135 5.824 3 7.938l3-2.647z" />
                  </svg>
                  {checkingFreshness ? '检查中...' : '扫描中...'}
                </>
              ) : (
                <>
                  <svg className="h-5 w-5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
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
          <div className="bg-gradient-card border border-border shadow-card p-5 rounded-xl space-y-3">
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
          <div className={`p-4 rounded-xl border ${scanResult.includes('失败') || scanResult.includes('取消') ? 'bg-red-500/10 border-red-500/30 text-red-400' : 'bg-green-500/10 border-green-500/30 text-green-400'}`}>
            {scanResult}
          </div>
        )}

        {/* 统计卡片 */}
        {statistics && (
          <div className="grid grid-cols-1 md:grid-cols-4 gap-4">
            <div className="bg-gradient-card p-6 rounded-xl border border-border shadow-card hover:shadow-glow-blue transition-all">
              <div className="text-gray-400 text-sm mb-2">总信号数</div>
              <div className="text-3xl font-bold text-primary-light">
                {statistics.total_signals || 0}
              </div>
            </div>
            <div className="bg-gradient-card p-6 rounded-xl border border-border shadow-card hover:shadow-glow-green transition-all">
              <div className="text-gray-400 text-sm mb-2">买入信号</div>
              <div className="text-3xl font-bold text-bull">
                {statistics.buy_signals || 0}
              </div>
            </div>
            <div className="bg-gradient-card p-6 rounded-xl border border-border shadow-card hover:shadow-glow-red transition-all">
              <div className="text-gray-400 text-sm mb-2">卖出信号</div>
              <div className="text-3xl font-bold text-bear">
                {statistics.sell_signals || 0}
              </div>
            </div>
            <div className="bg-gradient-card p-6 rounded-xl border border-border shadow-card hover:shadow-glow-blue transition-all">
              <div className="text-gray-400 text-sm mb-2">平均强度</div>
              <div className="text-3xl font-bold text-accent-cyan">
                {statistics.avg_strength ? (statistics.avg_strength * 100).toFixed(1) : 0}%
              </div>
            </div>
          </div>
        )}

        {/* 筛选表单 */}
        <div className="bg-gradient-card border border-border shadow-card p-6 rounded-xl">
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
                className="w-full px-4 py-2 bg-primary text-white rounded-xl hover:bg-primary-dark shadow-glow-blue transition-all disabled:opacity-50"
              >
                {loading ? '查询中...' : '查询'}
              </button>
            </div>
          </div>
        </div>

        {/* 信号列表 */}
        <div className="bg-gradient-card border border-border shadow-card p-6 rounded-xl">
          <div className="flex items-center justify-between mb-4">
            <h2 className="text-xl font-semibold text-white">信号历史</h2>
            {totalSignals > 0 && (
              <span className="text-sm text-gray-400">
                共 <span className="text-white font-medium">{totalSignals}</span> 条信号
              </span>
            )}
          </div>
          <div className="overflow-x-auto rounded-lg border border-border">
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
                          <div
                            className="w-16 h-2 bg-dark-light rounded-full overflow-hidden"
                          >
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

          {/* 分页控件 */}
          {totalSignals > pageSize && (
            <div className="flex items-center justify-between mt-4 pt-4 border-t border-border">
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
                {/* 页码按钮 */}
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
                          ? 'bg-primary text-white border-primary shadow-glow-blue'
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
        </div>
      </div>
    </div>
  );
};

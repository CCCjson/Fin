import React, { useState, useEffect } from 'react';
import { trackingService } from '../services/trackingService';
import type { TrackingStats, StrategyMetrics, TrackedSignal, UpdateResult } from '../services/trackingService';

export const SignalTracking: React.FC = () => {
  const [stats, setStats] = useState<TrackingStats | null>(null);
  const [signals, setSignals] = useState<TrackedSignal[]>([]);
  const [totalSignals, setTotalSignals] = useState(0);
  const [loading, setLoading] = useState(false);
  const [updating, setUpdating] = useState(false);
  const [updateResult, setUpdateResult] = useState<UpdateResult | null>(null);

  // Filters
  const [filterStrategy, setFilterStrategy] = useState('');
  const [filterOutcome, setFilterOutcome] = useState('');
  const [filterSignalType, setFilterSignalType] = useState('');
  const [filterTrackingStatus, setFilterTrackingStatus] = useState('tracking,completed');
  const [page, setPage] = useState(1);
  const pageSize = 50;

  useEffect(() => {
    loadStats();
    loadSignals();
  }, []);

  const loadStats = async () => {
    try {
      const data = await trackingService.getStats();
      setStats(data);
    } catch (error) {
      console.error('Failed to load tracking stats:', error);
    }
  };

  const loadSignals = async (targetPage?: number) => {
    try {
      setLoading(true);
      const currentPage = targetPage ?? page;
      const offset = (currentPage - 1) * pageSize;
      const data = await trackingService.getSignals({
        strategy: filterStrategy || undefined,
        outcome: filterOutcome || undefined,
        signal_type: filterSignalType || undefined,
        tracking_status: filterTrackingStatus || undefined,
        limit: pageSize,
        offset,
      });
      setSignals(data.signals || []);
      setTotalSignals(data.total || 0);
    } catch (error) {
      console.error('Failed to load tracked signals:', error);
    } finally {
      setLoading(false);
    }
  };

  const handleUpdate = async () => {
    try {
      setUpdating(true);
      setUpdateResult(null);
      const result = await trackingService.updateTracking();
      setUpdateResult(result);
      // Reload data
      await Promise.all([loadStats(), loadSignals(1)]);
      setPage(1);
    } catch (error) {
      console.error('Failed to update tracking:', error);
    } finally {
      setUpdating(false);
    }
  };

  const handleSearch = () => {
    setPage(1);
    loadSignals(1);
  };

  const totalPages = Math.max(1, Math.ceil(totalSignals / pageSize));

  const handlePageChange = (newPage: number) => {
    if (newPage < 1 || newPage > totalPages || newPage === page) return;
    setPage(newPage);
    loadSignals(newPage);
  };

  const formatReturn = (val: number | null) => {
    if (val === null || val === undefined) return '-';
    const sign = val >= 0 ? '+' : '';
    return `${sign}${val.toFixed(2)}%`;
  };

  const getReturnColor = (val: number | null) => {
    if (val === null || val === undefined) return 'text-gray-500';
    if (val > 0) return 'text-bull';
    if (val < 0) return 'text-bear';
    return 'text-gray-400';
  };

  const getOutcomeBadge = (outcome: string | null) => {
    if (!outcome) return <span className="text-gray-500 text-xs">-</span>;
    switch (outcome) {
      case 'win':
        return <span className="px-2 py-0.5 rounded-lg text-xs font-semibold bg-bull/20 text-bull border border-bull/30">WIN</span>;
      case 'loss':
        return <span className="px-2 py-0.5 rounded-lg text-xs font-semibold bg-bear/20 text-bear border border-bear/30">LOSS</span>;
      case 'neutral':
        return <span className="px-2 py-0.5 rounded-lg text-xs font-semibold bg-gray-500/20 text-gray-400 border border-gray-500/30">NEUTRAL</span>;
      default:
        return <span className="text-gray-500 text-xs">{outcome}</span>;
    }
  };

  const strategyEntries = stats ? Object.entries(stats.by_strategy).sort((a, b) => b[1].win_rate - a[1].win_rate) : [];
  const strategies = strategyEntries.map(([name]) => name);

  const renderMetricCard = (label: string, value: string | number, subLabel?: string, color?: string) => (
    <div className="bg-gradient-card p-6 rounded-xl border border-border shadow-card hover:shadow-glow-blue transition-all">
      <div className="text-gray-400 text-sm mb-2">{label}</div>
      <div className={`text-3xl font-bold ${color || 'text-primary-light'}`}>
        {value}
      </div>
      {subLabel && <div className="text-xs text-gray-500 mt-1">{subLabel}</div>}
    </div>
  );

  const overall = stats?.overall;

  return (
    <div className="min-h-screen bg-gradient-dark p-6">
      <div className="max-w-7xl mx-auto space-y-6">
        {/* Header */}
        <div className="flex items-center justify-between">
          <h1 className="text-3xl font-bold text-white">信号追踪</h1>
          <div className="flex items-center gap-3">
            <button
              onClick={handleUpdate}
              disabled={updating}
              className="px-5 py-2 bg-gradient-to-r from-violet-500 to-purple-600 text-white rounded-xl hover:from-violet-600 hover:to-purple-700 shadow-lg shadow-violet-500/25 transition-all disabled:opacity-50 disabled:cursor-not-allowed flex items-center gap-2"
            >
              {updating ? (
                <>
                  <svg className="animate-spin h-5 w-5" viewBox="0 0 24 24">
                    <circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4" fill="none" />
                    <path className="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4zm2 5.291A7.962 7.962 0 014 12H0c0 3.042 1.135 5.824 3 7.938l3-2.647z" />
                  </svg>
                  追踪中...
                </>
              ) : (
                <>
                  <svg className="h-5 w-5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                    <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M4 4v5h.582m15.356 2A8.001 8.001 0 004.582 9m0 0H9m11 11v-5h-.581m0 0a8.003 8.003 0 01-15.357-2m15.357 2H15" />
                  </svg>
                  更新追踪
                </>
              )}
            </button>
          </div>
        </div>

        {/* Tracking update result */}
        {updateResult && (
          <div className="p-4 rounded-xl border bg-violet-500/10 border-violet-500/30 text-violet-300">
            追踪更新完成：新建 {updateResult.created} 条，更新 {updateResult.updated} 条，完成 {updateResult.completed} 条
          </div>
        )}

        {/* Overview Stats Cards */}
        {overall && (
          <div className="grid grid-cols-1 md:grid-cols-5 gap-4">
            {renderMetricCard(
              '总胜率',
              `${overall.win_rate}%`,
              `${overall.win}胜 / ${overall.loss}负 / ${overall.neutral}平`,
              overall.win_rate >= 50 ? 'text-bull' : 'text-bear'
            )}
            {renderMetricCard(
              '平均10日收益',
              overall.avg_return_10d !== null ? `${overall.avg_return_10d > 0 ? '+' : ''}${overall.avg_return_10d}%` : '-',
              undefined,
              overall.avg_return_10d !== null && overall.avg_return_10d > 0 ? 'text-bull' : 'text-bear'
            )}
            {renderMetricCard(
              '平均最大浮盈',
              overall.avg_max_gain !== null ? `+${overall.avg_max_gain}%` : '-',
              undefined,
              'text-bull'
            )}
            {renderMetricCard(
              '平均最大浮亏',
              overall.avg_max_loss !== null ? `${overall.avg_max_loss}%` : '-',
              undefined,
              'text-bear'
            )}
            {renderMetricCard(
              '止盈 / 止损触发率',
              `${overall.take_profit_hit_rate}%`,
              `止损: ${overall.stop_loss_hit_rate}%`,
              'text-accent-cyan'
            )}
          </div>
        )}

        {/* Strategy Comparison Table */}
        {strategyEntries.length > 0 && (
          <div className="bg-gradient-card border border-border shadow-card p-6 rounded-xl">
            <h2 className="text-xl font-semibold text-white mb-4">策略对比</h2>
            <div className="overflow-x-auto rounded-lg border border-border">
              <table className="w-full text-sm">
                <thead className="bg-dark-light text-gray-300 border-b border-border">
                  <tr>
                    <th className="px-4 py-3 text-left">策略</th>
                    <th className="px-4 py-3 text-right">信号数</th>
                    <th className="px-4 py-3 text-right">已追踪</th>
                    <th className="px-4 py-3 text-right">胜率</th>
                    <th className="px-4 py-3 text-right">5日均收益</th>
                    <th className="px-4 py-3 text-right">10日均收益</th>
                    <th className="px-4 py-3 text-right">最大浮盈</th>
                    <th className="px-4 py-3 text-right">最大浮亏</th>
                    <th className="px-4 py-3 text-right">止盈率</th>
                    <th className="px-4 py-3 text-right">止损率</th>
                  </tr>
                </thead>
                <tbody className="text-gray-300">
                  {strategyEntries.map(([name, m]: [string, StrategyMetrics]) => {
                    const isTop = m.win_rate >= 60;
                    return (
                      <tr
                        key={name}
                        className={`border-b border-border hover:bg-dark-light transition-colors ${isTop ? 'bg-bull/5' : ''}`}
                      >
                        <td className="px-4 py-3 font-medium text-white">{name}</td>
                        <td className="px-4 py-3 text-right">{m.total}</td>
                        <td className="px-4 py-3 text-right">{m.tracked}</td>
                        <td className={`px-4 py-3 text-right font-semibold ${m.win_rate >= 50 ? 'text-bull' : 'text-bear'}`}>
                          {m.win_rate}%
                        </td>
                        <td className={`px-4 py-3 text-right ${getReturnColor(m.avg_return_5d)}`}>
                          {formatReturn(m.avg_return_5d)}
                        </td>
                        <td className={`px-4 py-3 text-right ${getReturnColor(m.avg_return_10d)}`}>
                          {formatReturn(m.avg_return_10d)}
                        </td>
                        <td className="px-4 py-3 text-right text-bull">
                          {m.avg_max_gain !== null ? `+${m.avg_max_gain}%` : '-'}
                        </td>
                        <td className="px-4 py-3 text-right text-bear">
                          {m.avg_max_loss !== null ? `${m.avg_max_loss}%` : '-'}
                        </td>
                        <td className="px-4 py-3 text-right">{m.take_profit_hit_rate}%</td>
                        <td className="px-4 py-3 text-right">{m.stop_loss_hit_rate}%</td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            </div>
          </div>
        )}

        {/* Filters */}
        <div className="bg-gradient-card border border-border shadow-card p-6 rounded-xl">
          <div className="grid grid-cols-1 md:grid-cols-5 gap-4">
            <div>
              <label className="block text-sm font-medium text-gray-300 mb-2">策略</label>
              <select
                value={filterStrategy}
                onChange={(e) => setFilterStrategy(e.target.value)}
                className="w-full px-3 py-2 bg-dark-light text-white rounded-lg border border-border focus:border-primary focus:ring-2 focus:ring-primary/20 outline-none transition-all"
              >
                <option value="">全部</option>
                {strategies.map((s) => (
                  <option key={s} value={s}>{s}</option>
                ))}
              </select>
            </div>
            <div>
              <label className="block text-sm font-medium text-gray-300 mb-2">结果</label>
              <select
                value={filterOutcome}
                onChange={(e) => setFilterOutcome(e.target.value)}
                className="w-full px-3 py-2 bg-dark-light text-white rounded-lg border border-border focus:border-primary focus:ring-2 focus:ring-primary/20 outline-none transition-all"
              >
                <option value="">全部</option>
                <option value="win">WIN</option>
                <option value="loss">LOSS</option>
                <option value="neutral">NEUTRAL</option>
              </select>
            </div>
            <div>
              <label className="block text-sm font-medium text-gray-300 mb-2">信号类型</label>
              <select
                value={filterSignalType}
                onChange={(e) => setFilterSignalType(e.target.value)}
                className="w-full px-3 py-2 bg-dark-light text-white rounded-lg border border-border focus:border-primary focus:ring-2 focus:ring-primary/20 outline-none transition-all"
              >
                <option value="">全部</option>
                <option value="BUY">买入</option>
                <option value="SELL">卖出</option>
              </select>
            </div>
            <div>
              <label className="block text-sm font-medium text-gray-300 mb-2">追踪状态</label>
              <select
                value={filterTrackingStatus}
                onChange={(e) => setFilterTrackingStatus(e.target.value)}
                className="w-full px-3 py-2 bg-dark-light text-white rounded-lg border border-border focus:border-primary focus:ring-2 focus:ring-primary/20 outline-none transition-all"
              >
                <option value="tracking,completed">有数据</option>
                <option value="">全部</option>
                <option value="pending">待追踪</option>
                <option value="tracking">追踪中</option>
                <option value="completed">已完成</option>
              </select>
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

        {/* Signal Detail Table */}
        <div className="bg-gradient-card border border-border shadow-card p-6 rounded-xl">
          <div className="flex items-center justify-between mb-4">
            <h2 className="text-xl font-semibold text-white">追踪明细</h2>
            {totalSignals > 0 && (
              <span className="text-sm text-gray-400">
                共 <span className="text-white font-medium">{totalSignals}</span> 条
              </span>
            )}
          </div>
          <div className="overflow-x-auto rounded-lg border border-border">
            <table className="w-full text-sm">
              <thead className="bg-dark-light text-gray-300 border-b border-border">
                <tr>
                  <th className="px-3 py-3 text-left">日期</th>
                  <th className="px-3 py-3 text-left">股票</th>
                  <th className="px-3 py-3 text-left">策略</th>
                  <th className="px-3 py-3 text-center">信号</th>
                  <th className="px-3 py-3 text-right">触发价</th>
                  <th className="px-3 py-3 text-center">已追踪</th>
                  <th className="px-3 py-3 text-right">1日</th>
                  <th className="px-3 py-3 text-right">3日</th>
                  <th className="px-3 py-3 text-right">5日</th>
                  <th className="px-3 py-3 text-right">10日</th>
                  <th className="px-3 py-3 text-right">20日</th>
                  <th className="px-3 py-3 text-right">浮盈</th>
                  <th className="px-3 py-3 text-right">浮亏</th>
                  <th className="px-3 py-3 text-center">结果</th>
                </tr>
              </thead>
              <tbody className="text-gray-300">
                {signals.length === 0 ? (
                  <tr>
                    <td colSpan={14} className="px-4 py-8 text-center text-gray-500">
                      {loading ? '加载中...' : '暂无追踪数据，点击"更新追踪"开始'}
                    </td>
                  </tr>
                ) : (
                  signals.map((s) => (
                    <tr key={s.id} className="border-b border-border hover:bg-dark-light transition-colors">
                      <td className="px-3 py-3 whitespace-nowrap">{s.signal_date}</td>
                      <td className="px-3 py-3 font-medium text-white whitespace-nowrap" title={s.symbol}>
                        {s.name}
                      </td>
                      <td className="px-3 py-3">
                        <span className="text-accent-purple text-xs">{s.strategy || '-'}</span>
                      </td>
                      <td className="px-3 py-3 text-center">
                        <span className={`px-2 py-0.5 rounded-lg text-xs font-semibold ${
                          s.signal_type === 'BUY'
                            ? 'bg-bull/20 text-bull border border-bull/30'
                            : 'bg-bear/20 text-bear border border-bear/30'
                        }`}>
                          {s.signal_type === 'BUY' ? '买' : '卖'}
                        </span>
                      </td>
                      <td className="px-3 py-3 text-right text-primary-light">
                        {s.signal_price.toFixed(2)}
                      </td>
                      <td className="px-3 py-3 text-center">
                        <span className={`px-1.5 py-0.5 rounded text-xs font-medium ${
                          s.tracked_days >= 20
                            ? 'bg-bull/15 text-bull'
                            : s.tracked_days >= 5
                            ? 'bg-primary/15 text-primary-light'
                            : s.tracked_days > 0
                            ? 'bg-amber-500/15 text-amber-400'
                            : 'bg-gray-500/15 text-gray-500'
                        }`}>
                          {s.tracked_days}天
                        </span>
                      </td>
                      <td className={`px-3 py-3 text-right ${getReturnColor(s.return_1d)}`}>{formatReturn(s.return_1d)}</td>
                      <td className={`px-3 py-3 text-right ${getReturnColor(s.return_3d)}`}>{formatReturn(s.return_3d)}</td>
                      <td className={`px-3 py-3 text-right ${getReturnColor(s.return_5d)}`}>{formatReturn(s.return_5d)}</td>
                      <td className={`px-3 py-3 text-right font-medium ${getReturnColor(s.return_10d)}`}>{formatReturn(s.return_10d)}</td>
                      <td className={`px-3 py-3 text-right ${getReturnColor(s.return_20d)}`}>{formatReturn(s.return_20d)}</td>
                      <td className="px-3 py-3 text-right text-bull">
                        {s.max_gain !== null ? `+${s.max_gain.toFixed(1)}%` : '-'}
                      </td>
                      <td className="px-3 py-3 text-right text-bear">
                        {s.max_loss !== null ? `${s.max_loss.toFixed(1)}%` : '-'}
                      </td>
                      <td className="px-3 py-3 text-center">{getOutcomeBadge(s.outcome)}</td>
                    </tr>
                  ))
                )}
              </tbody>
            </table>
          </div>

          {/* Pagination */}
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

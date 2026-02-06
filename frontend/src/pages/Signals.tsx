import React, { useState, useEffect } from 'react';
import { signalService } from '../services/signalService';
import type { Signal, SignalStatistics } from '../types';

export const Signals: React.FC = () => {
  const [signals, setSignals] = useState<Signal[]>([]);
  const [statistics, setStatistics] = useState<SignalStatistics | null>(null);
  const [loading, setLoading] = useState(false);
  const [symbol, setSymbol] = useState('');
  const [signalType, setSignalType] = useState('');
  const [startDate, setStartDate] = useState('2025-01-01');
  const [endDate, setEndDate] = useState('2025-12-31');

  useEffect(() => {
    loadData();
  }, []);

  const loadData = async () => {
    try {
      setLoading(true);
      const params = {
        symbol: symbol || undefined,
        signal_type: signalType || undefined,
        start_date: startDate,
        end_date: endDate,
        limit: 100,
      };

      const [signalsRes, statsRes] = await Promise.all([
        signalService.getSignals(params),
        signalService.getStatistics({
          symbol: symbol || undefined,
          start_date: startDate,
          end_date: endDate,
        }),
      ]);

      setSignals(signalsRes.signals || []);
      setStatistics(statsRes || null);
    } catch (error) {
      console.error('Failed to load signals:', error);
    } finally {
      setLoading(false);
    }
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
        <h1 className="text-3xl font-bold text-white">信号分析</h1>

        {/* 统计卡片 */}
        {statistics && (
          <div className="grid grid-cols-1 md:grid-cols-4 gap-4">
            <div className="bg-gradient-card p-6 rounded-xl border border-border shadow-card hover:shadow-glow-blue transition-all">
              <div className="text-gray-400 text-sm mb-2">总信号数</div>
              <div className="text-3xl font-bold text-primary-light">
                {statistics.total_signals}
              </div>
            </div>
            <div className="bg-gradient-card p-6 rounded-xl border border-border shadow-card hover:shadow-glow-green transition-all">
              <div className="text-gray-400 text-sm mb-2">买入信号</div>
              <div className="text-3xl font-bold text-bull">
                {statistics.buy_signals}
              </div>
            </div>
            <div className="bg-gradient-card p-6 rounded-xl border border-border shadow-card hover:shadow-glow-red transition-all">
              <div className="text-gray-400 text-sm mb-2">卖出信号</div>
              <div className="text-3xl font-bold text-bear">
                {statistics.sell_signals}
              </div>
            </div>
            <div className="bg-gradient-card p-6 rounded-xl border border-border shadow-card hover:shadow-glow-blue transition-all">
              <div className="text-gray-400 text-sm mb-2">平均强度</div>
              <div className="text-3xl font-bold text-accent-cyan">
                {(statistics.avg_strength * 100).toFixed(1)}%
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
              <input
                type="text"
                value={symbol}
                onChange={(e) => setSymbol(e.target.value)}
                placeholder="全部"
                className="w-full px-3 py-2 bg-dark-light text-white rounded-lg border border-border focus:border-primary focus:ring-2 focus:ring-primary/20 outline-none transition-all"
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
                onClick={loadData}
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
          <h2 className="text-xl font-semibold text-white mb-4">信号历史</h2>
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
                      {loading ? '加载中...' : '暂无信号数据'}
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
        </div>
      </div>
    </div>
  );
};

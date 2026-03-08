import React, { useState, useRef } from 'react';
import { backtestService } from '../../services/backtestService';
import { StockSymbolInput } from '../common/StockSymbolInput';
import { LineChart, Line, XAxis, YAxis, CartesianGrid, Tooltip, ResponsiveContainer } from 'recharts';

const STRATEGIES = [
  { value: 'MA_CROSS', label: '均线交叉', defaultGrid: { fast_period: [3, 5, 8, 10], slow_period: [15, 20, 30, 60] } },
  { value: 'MACD', label: 'MACD', defaultGrid: { fast_period: [8, 12, 16], slow_period: [20, 26, 30], signal_period: [7, 9, 12] } },
  { value: 'RSI', label: 'RSI', defaultGrid: { period: [10, 14, 20], oversold: [25, 30, 35], overbought: [65, 70, 75] } },
  { value: 'BOLLINGER', label: '布林带', defaultGrid: { period: [15, 20, 25], num_std: [1.5, 2.0, 2.5] } },
  { value: 'MOMENTUM', label: '动量策略', defaultGrid: { lookback: [10, 15, 20, 30], buy_threshold: [0.03, 0.05, 0.08] } },
];

interface WalkForwardResult {
  idx: number;
  window: { train_start: string; train_end: string; test_start: string; test_end: string };
  best_params: Record<string, any>;
  train_sharpe: number;
  test_sharpe: number;
  test_return: number;
  test_max_dd: number;
  test_win_rate: number;
  test_trades: number;
  status: string;
  error?: string;
}

export const WalkForwardPanel: React.FC = () => {
  const [symbol, setSymbol] = useState('000001.SZ');
  const [strategy, setStrategy] = useState('MA_CROSS');
  const [market, setMarket] = useState('a_share');
  const [startDate, setStartDate] = useState('2015-01-01');
  const [endDate, setEndDate] = useState(new Date().toISOString().slice(0, 10));
  const [trainMonths, setTrainMonths] = useState(12);
  const [testMonths, setTestMonths] = useState(3);
  const [stepMonths, setStepMonths] = useState(3);
  const [paramGridText, setParamGridText] = useState('');
  const [initialCapital, setInitialCapital] = useState(100000);

  const [loading, setLoading] = useState(false);
  const [error, setError] = useState('');
  const [progress, setProgress] = useState({ current: 0, total: 0 });
  const [windowResults, setWindowResults] = useState<WalkForwardResult[]>([]);
  const [summary, setSummary] = useState<any>(null);
  const [oosEquity, setOosEquity] = useState<any[]>([]);
  const [paramHistory, setParamHistory] = useState<Record<string, any>[]>([]);
  const [done, setDone] = useState(false);
  const abortRef = useRef<AbortController | null>(null);

  const selectedStrategy = STRATEGIES.find(s => s.value === strategy);

  const getParamGrid = (): Record<string, any[]> => {
    if (paramGridText.trim()) {
      try { return JSON.parse(paramGridText); } catch { return {}; }
    }
    return selectedStrategy?.defaultGrid || {};
  };

  const handleSubmit = async () => {
    const grid = getParamGrid();
    if (Object.keys(grid).length === 0) {
      setError('请配置参数搜索范围');
      return;
    }

    setLoading(true);
    setError('');
    setWindowResults([]);
    setSummary(null);
    setOosEquity([]);
    setParamHistory([]);
    setDone(false);
    setProgress({ current: 0, total: 0 });

    const controller = new AbortController();
    abortRef.current = controller;

    try {
      await backtestService.runWalkForward(
        {
          symbol, strategy, market, initial_capital: initialCapital,
          train_months: trainMonths, test_months: testMonths, step_months: stepMonths,
          start_date: startDate, end_date: endDate,
          param_grid: grid,
        },
        (event: any) => {
          if (event.event === 'start') {
            setProgress({ current: 0, total: event.total_windows || 0 });
          } else if (event.event === 'window_done') {
            setProgress({ current: event.current, total: event.total });
            setWindowResults(prev => [...prev, event]);
          } else if (event.event === 'complete') {
            setSummary(event.summary);
            setOosEquity(event.oos_equity || []);
            setParamHistory(event.param_history || []);
            setDone(true);
          }
        },
        controller.signal,
      );
    } catch (e: any) {
      if (e.name !== 'AbortError') setError(e.message || 'Walk-Forward 失败');
    } finally {
      setLoading(false);
      abortRef.current = null;
    }
  };

  const handleCancel = () => {
    abortRef.current?.abort();
    setLoading(false);
  };

  const overfitColor = (ratio: number) => {
    if (ratio < 1.5) return 'text-bull';
    if (ratio < 2.5) return 'text-accent-orange';
    return 'text-bear';
  };

  return (
    <div className="space-y-4">
      {/* Config Form */}
      {!loading && !done && (
        <div className="bg-gradient-card border border-border shadow-card p-3 md:p-6 rounded-xl">
          <h2 className="text-base md:text-lg font-semibold text-white mb-1">Walk-Forward 验证</h2>
          <p className="text-gray-500 text-xs mb-4">滚动窗口训练+测试，检测参数过拟合</p>

          <div className="grid grid-cols-2 md:grid-cols-4 gap-3">
            <div>
              <label className="block text-xs text-gray-400 mb-1">股票代码</label>
              <StockSymbolInput value={symbol} onChange={setSymbol} placeholder="000001.SZ" />
            </div>
            <div>
              <label className="block text-xs text-gray-400 mb-1">策略</label>
              <select value={strategy} onChange={e => setStrategy(e.target.value)} className="form-input">
                {STRATEGIES.map(s => <option key={s.value} value={s.value}>{s.label}</option>)}
              </select>
            </div>
            <div>
              <label className="block text-xs text-gray-400 mb-1">市场</label>
              <select value={market} onChange={e => setMarket(e.target.value)} className="form-input">
                <option value="a_share">A股</option>
                <option value="us">美股</option>
                <option value="hk">港股</option>
              </select>
            </div>
            <div>
              <label className="block text-xs text-gray-400 mb-1">初始资金</label>
              <input type="number" value={initialCapital} onChange={e => setInitialCapital(parseInt(e.target.value) || 100000)} className="form-input" />
            </div>
            <div>
              <label className="block text-xs text-gray-400 mb-1">开始日期</label>
              <input type="date" value={startDate} onChange={e => setStartDate(e.target.value)} className="form-input" />
            </div>
            <div>
              <label className="block text-xs text-gray-400 mb-1">结束日期</label>
              <input type="date" value={endDate} onChange={e => setEndDate(e.target.value)} className="form-input" />
            </div>
            <div>
              <label className="block text-xs text-gray-400 mb-1">训练期(月)</label>
              <input type="number" value={trainMonths} min={3} max={60} onChange={e => setTrainMonths(parseInt(e.target.value) || 12)} className="form-input" />
            </div>
            <div>
              <label className="block text-xs text-gray-400 mb-1">测试期(月)</label>
              <input type="number" value={testMonths} min={1} max={24} onChange={e => setTestMonths(parseInt(e.target.value) || 3)} className="form-input" />
            </div>
            <div>
              <label className="block text-xs text-gray-400 mb-1">步长(月)</label>
              <input type="number" value={stepMonths} min={1} max={24} onChange={e => setStepMonths(parseInt(e.target.value) || 3)} className="form-input" />
            </div>
          </div>

          {/* Param Grid */}
          <div className="mt-3">
            <label className="block text-xs text-gray-400 mb-1">
              参数搜索范围 (JSON)
              {selectedStrategy && (
                <button
                  onClick={() => setParamGridText(JSON.stringify(selectedStrategy.defaultGrid, null, 2))}
                  className="ml-2 text-primary-light hover:text-primary text-[10px]"
                >
                  填入默认值
                </button>
              )}
            </label>
            <textarea
              value={paramGridText || (selectedStrategy ? JSON.stringify(selectedStrategy.defaultGrid, null, 2) : '')}
              onChange={e => setParamGridText(e.target.value)}
              rows={3}
              className="form-input w-full font-mono text-xs"
              placeholder='{"fast_period": [3, 5, 8], "slow_period": [15, 20, 30]}'
            />
          </div>

          <button onClick={handleSubmit} disabled={loading}
            className="mt-4 w-full py-3 bg-accent-cyan hover:bg-accent-cyan/80 text-dark rounded-xl font-semibold disabled:opacity-50 transition-all">
            启动 Walk-Forward 验证
          </button>

          {error && <p className="text-red-400 text-sm mt-2 bg-red-400/10 px-3 py-2 rounded">{error}</p>}
        </div>
      )}

      {/* Progress */}
      {loading && (
        <div className="bg-gradient-card border border-border shadow-card p-4 rounded-xl">
          <div className="flex items-center justify-between mb-2">
            <span className="text-sm text-white">Walk-Forward 进度</span>
            <button onClick={handleCancel} className="text-xs text-bear hover:text-bear/80">取消</button>
          </div>
          <div className="w-full bg-dark-light rounded-full h-2 mb-2">
            <div
              className="bg-accent-cyan h-2 rounded-full transition-all"
              style={{ width: `${progress.total > 0 ? (progress.current / progress.total) * 100 : 0}%` }}
            />
          </div>
          <span className="text-xs text-gray-400">
            {progress.current} / {progress.total} 窗口完成
          </span>

          {windowResults.length > 0 && (
            <div className="mt-3 max-h-40 overflow-y-auto space-y-1 text-xs">
              {windowResults.slice(-5).map((r, i) => (
                <div key={i} className="flex justify-between text-gray-400 bg-dark-light/50 px-2 py-1 rounded">
                  <span>{r.window.test_start} ~ {r.window.test_end}</span>
                  <span className={r.test_sharpe > 0 ? 'text-bull' : 'text-bear'}>
                    Sharpe: {r.test_sharpe}
                  </span>
                </div>
              ))}
            </div>
          )}
        </div>
      )}

      {/* Results */}
      {done && summary && (
        <div className="space-y-4">
          <div className="flex items-center justify-between">
            <span className="text-sm text-gray-400">Walk-Forward 完成</span>
            <button onClick={() => { setDone(false); setSummary(null); setWindowResults([]); }}
              className="text-xs px-4 py-1.5 bg-accent-cyan/20 text-accent-cyan border border-accent-cyan/30 rounded-lg hover:bg-accent-cyan/30 transition-all">
              新建验证
            </button>
          </div>

          {/* Overfit Dashboard */}
          <div className="bg-gradient-card border border-border shadow-card p-4 rounded-xl">
            <h3 className="text-base font-semibold text-white mb-3">过拟合诊断</h3>
            <div className="grid grid-cols-2 md:grid-cols-4 gap-3">
              <div className="text-center p-3 bg-dark-light rounded-lg border border-border">
                <div className="text-gray-400 text-xs mb-1">训练期 Sharpe</div>
                <div className="text-xl font-bold text-primary-light">{summary.avg_train_sharpe}</div>
              </div>
              <div className="text-center p-3 bg-dark-light rounded-lg border border-border">
                <div className="text-gray-400 text-xs mb-1">测试期 Sharpe</div>
                <div className="text-xl font-bold text-accent-cyan">{summary.avg_test_sharpe}</div>
              </div>
              <div className="text-center p-3 bg-dark-light rounded-lg border border-border">
                <div className="text-gray-400 text-xs mb-1">平均测试收益</div>
                <div className={`text-xl font-bold ${summary.avg_test_return >= 0 ? 'text-bull' : 'text-bear'}`}>
                  {summary.avg_test_return}%
                </div>
              </div>
              <div className="text-center p-3 bg-dark-light rounded-lg border border-border">
                <div className="text-gray-400 text-xs mb-1">过拟合比率</div>
                <div className={`text-xl font-bold ${overfitColor(summary.overfit_ratio)}`}>
                  {summary.overfit_ratio}x
                </div>
                <div className="text-[10px] text-gray-500 mt-0.5">
                  {summary.overfit_ratio < 1.5 ? '良好' : summary.overfit_ratio < 2.5 ? '注意' : '严重过拟合'}
                </div>
              </div>
            </div>
          </div>

          {/* OOS Equity Curve */}
          {oosEquity.length > 0 && (
            <div className="bg-gradient-card border border-border shadow-card p-4 rounded-xl">
              <h3 className="text-base font-semibold text-white mb-3">样本外净值曲线</h3>
              <ResponsiveContainer width="100%" height={300}>
                <LineChart data={oosEquity} margin={{ top: 5, right: 20, left: 10, bottom: 5 }}>
                  <CartesianGrid strokeDasharray="3 3" stroke="#374151" />
                  <XAxis dataKey="date" stroke="#9ca3af" tick={{ fontSize: 10 }} />
                  <YAxis stroke="#9ca3af" tickFormatter={v => (v / 1000).toFixed(0) + 'k'} />
                  <Tooltip
                    contentStyle={{ backgroundColor: '#1e1e1e', border: '1px solid #374151', borderRadius: 8 }}
                    formatter={(v: number) => [`${v.toLocaleString('zh-CN', { maximumFractionDigits: 0 })}`, '净值']}
                  />
                  <Line type="monotone" dataKey="total_value" stroke="#22d3ee" strokeWidth={2} dot={false} />
                </LineChart>
              </ResponsiveContainer>
            </div>
          )}

          {/* Window Results Table */}
          <div className="bg-gradient-card border border-border shadow-card p-4 rounded-xl">
            <h3 className="text-base font-semibold text-white mb-3">
              窗口详情 ({windowResults.filter(r => r.status === 'completed').length} 完成)
            </h3>
            <div className="overflow-x-auto rounded-lg border border-border">
              <table className="w-full text-xs">
                <thead className="bg-dark-light text-gray-300 border-b border-border">
                  <tr>
                    <th className="px-3 py-2 text-left">#</th>
                    <th className="px-3 py-2 text-left">训练期</th>
                    <th className="px-3 py-2 text-left">测试期</th>
                    <th className="px-3 py-2 text-right">训练 Sharpe</th>
                    <th className="px-3 py-2 text-right">测试 Sharpe</th>
                    <th className="px-3 py-2 text-right">测试收益</th>
                    <th className="px-3 py-2 text-right">最大回撤</th>
                    <th className="px-3 py-2 text-left">最优参数</th>
                  </tr>
                </thead>
                <tbody className="text-gray-300">
                  {windowResults.map((r, i) => (
                    <tr key={i} className="border-b border-border hover:bg-dark-light transition-colors">
                      <td className="px-3 py-2">{r.idx + 1}</td>
                      <td className="px-3 py-2 text-gray-500">
                        {r.window.train_start.slice(5)} ~ {r.window.train_end.slice(5)}
                      </td>
                      <td className="px-3 py-2">
                        {r.window.test_start.slice(5)} ~ {r.window.test_end.slice(5)}
                      </td>
                      <td className="px-3 py-2 text-right font-mono">{r.train_sharpe ?? '-'}</td>
                      <td className={`px-3 py-2 text-right font-mono ${(r.test_sharpe ?? 0) >= 0 ? 'text-bull' : 'text-bear'}`}>
                        {r.test_sharpe ?? '-'}
                      </td>
                      <td className={`px-3 py-2 text-right font-mono ${(r.test_return ?? 0) >= 0 ? 'text-bull' : 'text-bear'}`}>
                        {r.test_return != null ? `${r.test_return}%` : '-'}
                      </td>
                      <td className="px-3 py-2 text-right font-mono text-bear">
                        {r.test_max_dd != null ? `${r.test_max_dd}%` : '-'}
                      </td>
                      <td className="px-3 py-2 font-mono text-gray-500 max-w-[200px] truncate">
                        {r.best_params ? Object.entries(r.best_params).map(([k, v]) => `${k}=${v}`).join(' ') : '-'}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </div>
        </div>
      )}
    </div>
  );
};

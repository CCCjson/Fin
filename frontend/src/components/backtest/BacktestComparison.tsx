import React, { useState, useEffect } from 'react';
import { backtestService } from '../../services/backtestService';
import { EquityCurveChart } from './EquityCurveChart';

const COLORS = ['#3B82F6', '#EF4444', '#10B981', '#F59E0B', '#8B5CF6'];

interface BacktestComparisonProps {
  taskIds: string[];
  onClose: () => void;
}

const METRIC_LABELS: Record<string, string> = {
  total_return_pct: '总收益率(%)',
  annual_return: '年化收益(%)',
  sharpe_ratio: '夏普比率',
  sortino_ratio: 'Sortino',
  max_drawdown_pct: '最大回撤(%)',
  volatility: '波动率',
  win_rate: '胜率(%)',
  profit_factor: '盈亏比',
  total_trades: '交易次数',
};

export const BacktestComparison: React.FC<BacktestComparisonProps> = ({ taskIds, onClose }) => {
  const [data, setData] = useState<any[]>([]);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    const load = async () => {
      try {
        setLoading(true);
        const res = await backtestService.compareBacktestsByIds(taskIds);
        setData(res.comparisons || []);
      } catch (e) {
        console.error('Compare failed:', e);
      } finally {
        setLoading(false);
      }
    };
    load();
  }, [taskIds]);

  if (loading) {
    return (
      <div className="fixed inset-0 bg-black/60 z-50 flex items-center justify-center">
        <div className="bg-dark-card p-8 rounded-xl text-white">加载中...</div>
      </div>
    );
  }

  const metricKeys = Object.keys(METRIC_LABELS);

  // 找每个指标的最优值
  const bestValues: Record<string, number> = {};
  metricKeys.forEach(key => {
    const values = data.map(d => d.metrics?.[key] ?? 0);
    if (key === 'max_drawdown_pct' || key === 'volatility') {
      bestValues[key] = Math.min(...values); // 越小越好
    } else {
      bestValues[key] = Math.max(...values); // 越大越好
    }
  });

  const curves = data.map((d, idx) => ({
    name: d.name || d.task_id.slice(-8),
    color: COLORS[idx % COLORS.length],
    data: d.equity_curve || [],
  }));

  return (
    <div className="fixed inset-0 bg-black/60 z-50 flex items-center justify-center p-4">
      <div className="bg-dark-card border border-border rounded-2xl max-w-5xl w-full max-h-[90vh] overflow-y-auto">
        <div className="flex justify-between items-center p-4 md:p-6 border-b border-border">
          <h2 className="text-xl font-bold text-white">策略对比 ({data.length}个)</h2>
          <button onClick={onClose}
            className="text-gray-400 hover:text-white transition-colors text-xl p-1">
            x
          </button>
        </div>

        <div className="p-4 md:p-6 space-y-6">
          {/* 指标对比表格 */}
          <div className="overflow-x-auto rounded-lg border border-border">
            <table className="w-full text-sm">
              <thead className="bg-dark-light text-gray-300 border-b border-border">
                <tr>
                  <th className="px-4 py-3 text-left">指标</th>
                  {data.map((d, idx) => (
                    <th key={d.task_id} className="px-4 py-3 text-center">
                      <div className="flex items-center justify-center gap-1.5">
                        <span className="w-2.5 h-2.5 rounded-full" style={{ backgroundColor: COLORS[idx % COLORS.length] }} />
                        <span className="truncate max-w-[120px]">{d.name || d.task_id.slice(-8)}</span>
                      </div>
                    </th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {metricKeys.map(key => (
                  <tr key={key} className="border-b border-border">
                    <td className="px-4 py-2.5 text-gray-400">{METRIC_LABELS[key]}</td>
                    {data.map(d => {
                      const val = d.metrics?.[key] ?? 0;
                      const isBest = data.length > 1 && Math.abs(val - bestValues[key]) < 0.001;
                      return (
                        <td key={d.task_id} className={`px-4 py-2.5 text-center font-mono ${
                          isBest ? 'text-primary-light font-bold' : 'text-gray-300'
                        }`}>
                          {typeof val === 'number' ? val.toFixed(2) : val}
                        </td>
                      );
                    })}
                  </tr>
                ))}
              </tbody>
            </table>
          </div>

          {/* 资金曲线对比 */}
          {curves.some(c => c.data.length > 0) && (
            <EquityCurveChart comparisons={curves} />
          )}
        </div>
      </div>
    </div>
  );
};

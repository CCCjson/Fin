import React, { useState } from 'react';
import { Card } from '../common/Card';
import type { BatchRankingItem } from '../../types';

type SortKey = 'sharpe_ratio' | 'total_return_pct' | 'annual_return' | 'max_drawdown_pct' | 'win_rate' | 'profit_factor';

interface Props {
  ranking: BatchRankingItem[];
  onSelectTask: (taskId: string) => void;
}

export const BatchRanking: React.FC<Props> = ({ ranking, onSelectTask }) => {
  const [sortKey, setSortKey] = useState<SortKey>('sharpe_ratio');
  const [sortAsc, setSortAsc] = useState(false);

  const sorted = [...ranking].sort((a, b) => {
    const av = a[sortKey] ?? 0;
    const bv = b[sortKey] ?? 0;
    // 回撤越小越好
    if (sortKey === 'max_drawdown_pct') {
      return sortAsc ? bv - av : av - bv;
    }
    return sortAsc ? av - bv : bv - av;
  }).map((item, idx) => ({ ...item, rank: idx + 1 }));

  const handleSort = (key: SortKey) => {
    if (sortKey === key) setSortAsc(!sortAsc);
    else { setSortKey(key); setSortAsc(false); }
  };

  const arrow = (key: SortKey) => {
    if (sortKey !== key) return '';
    return sortAsc ? ' \u2191' : ' \u2193';
  };

  if (!ranking.length) {
    return (
      <Card className="p-6 text-center text-gray-500 text-sm">
        暂无排行数据
      </Card>
    );
  }

  return (
    <Card className="overflow-hidden">
      <div className="px-4 py-3 border-b border-border">
        <h3 className="text-sm font-semibold text-white">排行榜</h3>
        <p className="text-xs text-gray-500 mt-0.5">点击行查看详细结果 | 点击表头排序</p>
      </div>
      <div className="overflow-x-auto">
        <table className="w-full text-xs">
          <thead>
            <tr className="bg-dark-light text-gray-400">
              <th className="px-3 py-2 text-left">#</th>
              <th className="px-3 py-2 text-left">标签</th>
              <th className="px-3 py-2 text-right cursor-pointer hover:text-white" onClick={() => handleSort('total_return_pct')}>
                收益率{arrow('total_return_pct')}
              </th>
              <th className="px-3 py-2 text-right cursor-pointer hover:text-white" onClick={() => handleSort('annual_return')}>
                年化{arrow('annual_return')}
              </th>
              <th className="px-3 py-2 text-right cursor-pointer hover:text-white" onClick={() => handleSort('sharpe_ratio')}>
                夏普{arrow('sharpe_ratio')}
              </th>
              <th className="px-3 py-2 text-right cursor-pointer hover:text-white" onClick={() => handleSort('max_drawdown_pct')}>
                回撤{arrow('max_drawdown_pct')}
              </th>
              <th className="px-3 py-2 text-right cursor-pointer hover:text-white" onClick={() => handleSort('win_rate')}>
                胜率{arrow('win_rate')}
              </th>
              <th className="px-3 py-2 text-right cursor-pointer hover:text-white" onClick={() => handleSort('profit_factor')}>
                盈亏比{arrow('profit_factor')}
              </th>
              <th className="px-3 py-2 text-right">交易数</th>
            </tr>
          </thead>
          <tbody>
            {sorted.map(item => {
              const isTop3 = item.rank <= 3;
              return (
                <tr
                  key={item.task_id}
                  onClick={() => onSelectTask(item.task_id)}
                  className={`border-b border-border/50 cursor-pointer transition-all hover:bg-primary/10 ${
                    isTop3 ? 'bg-accent-purple/5' : ''
                  }`}
                >
                  <td className="px-3 py-2">
                    {item.rank <= 3 ? (
                      <span className={`inline-flex items-center justify-center w-5 h-5 rounded-full text-[10px] font-bold ${
                        item.rank === 1 ? 'bg-yellow-500/20 text-yellow-400' :
                        item.rank === 2 ? 'bg-gray-400/20 text-gray-300' :
                        'bg-orange-500/20 text-orange-400'
                      }`}>
                        {item.rank}
                      </span>
                    ) : (
                      <span className="text-gray-500">{item.rank}</span>
                    )}
                  </td>
                  <td className="px-3 py-2 text-white font-medium max-w-[180px] truncate">
                    {item.label}
                  </td>
                  <td className={`px-3 py-2 text-right font-mono ${item.total_return_pct >= 0 ? 'text-bull' : 'text-bear'}`}>
                    {item.total_return_pct >= 0 ? '+' : ''}{item.total_return_pct.toFixed(2)}%
                  </td>
                  <td className={`px-3 py-2 text-right font-mono ${item.annual_return >= 0 ? 'text-bull' : 'text-bear'}`}>
                    {item.annual_return >= 0 ? '+' : ''}{item.annual_return.toFixed(2)}%
                  </td>
                  <td className={`px-3 py-2 text-right font-mono ${item.sharpe_ratio >= 1 ? 'text-bull' : item.sharpe_ratio >= 0 ? 'text-white' : 'text-bear'}`}>
                    {item.sharpe_ratio.toFixed(2)}
                  </td>
                  <td className="px-3 py-2 text-right font-mono text-bear">
                    -{Math.abs(item.max_drawdown_pct).toFixed(2)}%
                  </td>
                  <td className="px-3 py-2 text-right font-mono text-white">
                    {item.win_rate.toFixed(1)}%
                  </td>
                  <td className="px-3 py-2 text-right font-mono text-white">
                    {item.profit_factor.toFixed(2)}
                  </td>
                  <td className="px-3 py-2 text-right font-mono text-gray-400">
                    {item.total_trades}
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
    </Card>
  );
};

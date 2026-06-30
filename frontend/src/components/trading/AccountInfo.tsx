import React from 'react';
import { Card } from '../common/Card';
import type { Account } from '../../types';

interface AccountInfoProps {
  account: Account | null;
}

export const AccountInfo: React.FC<AccountInfoProps> = ({ account }) => {
  if (!account) {
    return <div className="text-gray-400">加载中...</div>;
  }

  const initialCash = account.initial_cash ?? account.total_value;
  const profit = account.total_value - initialCash;
  const returnPct = account.return_pct ?? (initialCash > 0 ? (profit / initialCash) * 100 : 0);
  const profitColor = profit >= 0 ? 'text-bull' : 'text-bear';

  return (
    <Card className="p-6">
      <h2 className="text-2xl font-bold text-white mb-6">账户信息</h2>

      <div className="grid grid-cols-2 md:grid-cols-3 lg:grid-cols-5 gap-4">
        <div className="bg-dark-light p-4 rounded-lg border border-border">
          <div className="text-gray-400 text-sm mb-1">总资产</div>
          <div className="text-2xl font-bold text-primary-light">
            ¥{account.total_value.toLocaleString('zh-CN', { minimumFractionDigits: 2 })}
          </div>
        </div>

        <div className="bg-dark-light p-4 rounded-lg border border-border">
          <div className="text-gray-400 text-sm mb-1">可用资金</div>
          <div className="text-xl font-semibold text-white">
            ¥{account.cash.toLocaleString('zh-CN', { minimumFractionDigits: 2 })}
          </div>
        </div>

        <div className="bg-dark-light p-4 rounded-lg border border-border">
          <div className="text-gray-400 text-sm mb-1">持仓市值</div>
          <div className="text-xl font-semibold text-accent-cyan">
            ¥{account.market_value.toLocaleString('zh-CN', { minimumFractionDigits: 2 })}
          </div>
        </div>

        <div className="bg-dark-light p-4 rounded-lg border border-border">
          <div className="text-gray-400 text-sm mb-1">总盈亏</div>
          <div className={`text-xl font-semibold ${profitColor}`}>
            {profit >= 0 ? '+' : ''}{profit.toLocaleString('zh-CN', { minimumFractionDigits: 2 })}
          </div>
          <div className={`text-sm ${profitColor}`}>
            {returnPct >= 0 ? '+' : ''}{returnPct.toFixed(2)}%
          </div>
        </div>

        <div className="bg-dark-light p-4 rounded-lg border border-border">
          <div className="text-gray-400 text-sm mb-1">累计手续费</div>
          <div className="text-xl font-semibold text-yellow-400">
            ¥{(account.total_commission ?? 0).toLocaleString('zh-CN', { minimumFractionDigits: 2 })}
          </div>
          <div className="text-sm text-gray-500">{account.total_trades ?? 0} 笔交易</div>
        </div>
      </div>

      {account.positions && account.positions.length > 0 && (
        <div className="mt-6">
          <h3 className="text-lg font-semibold text-white mb-4">持仓</h3>
          <div className="overflow-x-auto rounded-lg border border-border">
            <table className="w-full text-sm">
              <thead className="bg-dark-light text-gray-300 border-b border-border">
                <tr>
                  <th className="px-4 py-3 text-left">股票</th>
                  <th className="px-4 py-3 text-right">数量</th>
                  <th className="px-4 py-3 text-right">成本</th>
                  <th className="px-4 py-3 text-right">现价</th>
                  <th className="px-4 py-3 text-right">盈亏</th>
                </tr>
              </thead>
              <tbody className="text-gray-300">
                {account.positions.map((pos) => (
                  <tr key={pos.symbol} className="border-b border-border hover:bg-dark-light transition-colors">
                    <td className="px-4 py-3 font-medium text-white">{pos.symbol}</td>
                    <td className="px-4 py-3 text-right">{pos.quantity}</td>
                    <td className="px-4 py-3 text-right">¥{pos.avg_cost.toFixed(2)}</td>
                    <td className="px-4 py-3 text-right text-primary-light">¥{pos.current_price.toFixed(2)}</td>
                    <td
                      className={`px-4 py-3 text-right font-semibold ${
                        pos.unrealized_pnl >= 0 ? 'text-bull' : 'text-bear'
                      }`}
                    >
                      ¥{pos.unrealized_pnl.toFixed(2)} ({pos.unrealized_pnl_pct.toFixed(2)}%)
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      )}
    </Card>
  );
};

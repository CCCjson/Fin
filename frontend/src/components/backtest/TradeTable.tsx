import React from 'react';

interface Trade {
  date: string;
  symbol?: string;
  action?: string;
  side?: string;
  quantity: number;
  price: number;
  commission?: number;
  slippage?: number;
  amount?: number;
  reason?: string;  // "signal" | "stop_loss" | "trailing_stop"
}

interface TradeTableProps {
  trades: Trade[];
  maxRows?: number;
}

export const TradeTable: React.FC<TradeTableProps> = ({ trades, maxRows = 50 }) => {
  const displayed = trades.slice(0, maxRows);
  const getAction = (t: Trade) => t.action || t.side || '';
  const isBuy = (t: Trade) => getAction(t) === 'BUY';
  const isRiskSell = (t: Trade) => !isBuy(t) && (t.reason === 'stop_loss' || t.reason === 'trailing_stop');
  const reasonLabel = (t: Trade) => {
    if (t.reason === 'stop_loss') return '止损';
    if (t.reason === 'trailing_stop') return '追踪止损';
    return '';
  };
  const hasSymbolColumn = displayed.some(t => !!t.symbol);

  return (
    <div className="bg-gradient-card border border-border shadow-card p-3 md:p-6 rounded-xl">
      <h3 className="text-lg font-semibold text-white mb-4">
        交易记录 ({trades.length}笔)
      </h3>

      {displayed.length === 0 ? (
        <div className="text-center text-gray-500 py-8">暂无交易记录</div>
      ) : (
        <>
          {/* Desktop */}
          <div className="hidden md:block overflow-x-auto rounded-lg border border-border">
            <table className="w-full text-sm">
              <thead className="bg-dark-light text-gray-300 border-b border-border">
                <tr>
                  <th className="px-4 py-3 text-left">日期</th>
                  {hasSymbolColumn && <th className="px-4 py-3 text-left">股票</th>}
                  <th className="px-4 py-3 text-center">方向</th>
                  <th className="px-4 py-3 text-right">价格</th>
                  <th className="px-4 py-3 text-right">数量</th>
                  <th className="px-4 py-3 text-right">金额</th>
                  <th className="px-4 py-3 text-right">手续费</th>
                </tr>
              </thead>
              <tbody className="text-gray-300">
                {displayed.map((t, i) => (
                  <tr key={i} className="border-b border-border hover:bg-dark-light transition-colors">
                    <td className="px-4 py-3">{t.date}</td>
                    {hasSymbolColumn && <td className="px-4 py-3 font-medium text-white">{t.symbol || '-'}</td>}
                    <td className="px-4 py-3 text-center">
                      <span className={`px-3 py-1 rounded-lg text-xs font-semibold ${
                        isBuy(t)
                          ? 'bg-bull/20 text-bull border border-bull/30'
                          : isRiskSell(t)
                            ? 'bg-accent-orange/20 text-accent-orange border border-accent-orange/30'
                            : 'bg-bear/20 text-bear border border-bear/30'
                      }`}>
                        {isBuy(t) ? '买入' : isRiskSell(t) ? reasonLabel(t) : '卖出'}
                      </span>
                    </td>
                    <td className="px-4 py-3 text-right font-mono text-primary-light">
                      {t.price.toFixed(2)}
                    </td>
                    <td className="px-4 py-3 text-right font-mono">{t.quantity}</td>
                    <td className="px-4 py-3 text-right font-mono">
                      {(t.amount || t.price * t.quantity).toLocaleString('zh-CN', { maximumFractionDigits: 2 })}
                    </td>
                    <td className="px-4 py-3 text-right text-gray-500 font-mono">
                      {(t.commission || 0).toFixed(2)}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>

          {/* Mobile */}
          <div className="md:hidden space-y-2">
            {displayed.map((t, i) => (
              <div key={i} className="bg-dark-light rounded-lg p-3 border border-border">
                <div className="flex items-center justify-between mb-2">
                  <span className="text-xs text-gray-400">{t.date}</span>
                  <span className={`px-2 py-0.5 rounded-lg text-xs font-semibold ${
                    isBuy(t)
                      ? 'bg-bull/20 text-bull border border-bull/30'
                      : isRiskSell(t)
                        ? 'bg-accent-orange/20 text-accent-orange border border-accent-orange/30'
                        : 'bg-bear/20 text-bear border border-bear/30'
                  }`}>
                    {isBuy(t) ? '买入' : isRiskSell(t) ? reasonLabel(t) : '卖出'}
                  </span>
                </div>
                <div className="flex items-center justify-between text-sm">
                  <span className="text-gray-400">{t.price.toFixed(2)} x {t.quantity}</span>
                  <span className="font-mono text-white">
                    {(t.amount || t.price * t.quantity).toLocaleString('zh-CN', { maximumFractionDigits: 2 })}
                  </span>
                </div>
              </div>
            ))}
          </div>
        </>
      )}
    </div>
  );
};

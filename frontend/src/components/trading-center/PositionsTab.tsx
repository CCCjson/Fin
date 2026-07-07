/**
 * Tab 2: 持仓总览
 */
import React, { useEffect, useState } from 'react';
import { toast } from '../common/Toast';
import { Card } from '../common/Card';
import { useAutomationStore } from '../../stores/automationStore';
import type { PositionItem } from '../../stores/automationStore';

const brokerFilters = [
  { label: '全部', value: '' },
  { label: 'Paper', value: 'paper' },
  { label: 'QMT', value: 'qmt' },
  { label: 'EasyTrader', value: 'easytrader' },
];

export const PositionsTab: React.FC = () => {
  const { positions, fetchPositions, submitManualOrder } = useAutomationStore();
  const [brokerFilter, setBrokerFilter] = useState('');
  const [sellTarget, setSellTarget] = useState<PositionItem | null>(null);
  const [sellQty, setSellQty] = useState(0);
  const [sellLoading, setSellLoading] = useState(false);

  useEffect(() => {
    // 加载所有在线 broker 的持仓
    loadPositions();
  }, [brokerFilter]);

  const loadPositions = () => {
    if (brokerFilter) {
      fetchPositions(brokerFilter);
    } else {
      // 加载 paper 持仓（默认）
      fetchPositions('paper');
    }
  };

  const handleSellClick = (pos: PositionItem) => {
    setSellTarget(pos);
    setSellQty(pos.available);
  };

  const handleSellConfirm = async () => {
    if (!sellTarget || sellQty <= 0) return;
    setSellLoading(true);
    const result = await submitManualOrder({
      broker_type: sellTarget.broker,
      symbol: sellTarget.symbol,
      action: 'SELL',
      quantity: sellQty,
      price: sellTarget.current_price,
    });
    setSellLoading(false);
    if (result.success) {
      setSellTarget(null);
      loadPositions();
    } else {
      toast.error(result.message);
    }
  };

  const totalValue = positions.reduce((sum, p) => sum + p.market_value, 0);
  const totalPnl = positions.reduce((sum, p) => sum + p.unrealized_pnl, 0);

  return (
    <div className="space-y-4">
      {/* 筛选器 + 汇总 */}
      <div className="flex items-center justify-between flex-wrap gap-3">
        <div className="flex gap-1">
          {brokerFilters.map((f) => (
            <button
              key={f.value}
              onClick={() => setBrokerFilter(f.value)}
              className={`px-3 py-1.5 rounded-lg text-xs font-medium transition-colors ${
                brokerFilter === f.value
                  ? 'bg-primary text-dark'
                  : 'bg-dark-card text-gray-400 hover:text-white'
              }`}
            >
              {f.label}
            </button>
          ))}
        </div>
        <div className="flex items-center gap-4 text-sm">
          <span className="text-gray-400">
            持仓市值 <span className="text-white font-medium">¥{totalValue.toLocaleString('zh-CN', { maximumFractionDigits: 0 })}</span>
          </span>
          <span className="text-gray-400">
            浮动盈亏{' '}
            <span className={`font-medium ${totalPnl >= 0 ? 'text-red-400' : 'text-green-400'}`}>
              {totalPnl >= 0 ? '+' : ''}¥{totalPnl.toLocaleString('zh-CN', { maximumFractionDigits: 0 })}
            </span>
          </span>
        </div>
      </div>

      {/* 持仓表格 */}
      <Card className="overflow-hidden">
        {positions.length === 0 ? (
          <div className="text-center py-16">
            <div className="text-4xl mb-3 opacity-40">📊</div>
            <p className="text-gray-500">暂无持仓</p>
            <p className="text-gray-600 text-xs mt-1">在"手动下单"Tab 中买入股票～</p>
          </div>
        ) : (
          <div className="overflow-x-auto">
            <table className="w-full text-sm">
              <thead>
                <tr className="text-gray-500 text-xs border-b border-border/50">
                  <th className="text-left px-4 py-3 font-medium">股票</th>
                  <th className="text-right px-4 py-3 font-medium">数量</th>
                  <th className="text-right px-4 py-3 font-medium">成本</th>
                  <th className="text-right px-4 py-3 font-medium">现价</th>
                  <th className="text-right px-4 py-3 font-medium">市值</th>
                  <th className="text-right px-4 py-3 font-medium">盈亏</th>
                  <th className="text-right px-4 py-3 font-medium">盈亏%</th>
                  <th className="text-right px-4 py-3 font-medium">来源</th>
                  <th className="text-center px-4 py-3 font-medium">操作</th>
                </tr>
              </thead>
              <tbody>
                {positions.map((pos) => {
                  const pnlColor = pos.unrealized_pnl >= 0 ? 'text-red-400' : 'text-green-400';
                  return (
                    <tr key={`${pos.broker}-${pos.symbol}`} className="border-b border-border/30 hover:bg-dark/30 transition-colors">
                      <td className="px-4 py-3">
                        <div className="text-white font-medium">{pos.symbol}</div>
                        <div className="text-gray-500 text-xs">{pos.name}</div>
                      </td>
                      <td className="px-4 py-3 text-right text-gray-300">{pos.quantity}</td>
                      <td className="px-4 py-3 text-right text-gray-300">{pos.avg_cost.toFixed(2)}</td>
                      <td className="px-4 py-3 text-right text-white">{pos.current_price.toFixed(2)}</td>
                      <td className="px-4 py-3 text-right text-white">¥{pos.market_value.toLocaleString('zh-CN', { maximumFractionDigits: 0 })}</td>
                      <td className={`px-4 py-3 text-right font-medium ${pnlColor}`}>
                        {pos.unrealized_pnl >= 0 ? '+' : ''}{pos.unrealized_pnl.toFixed(2)}
                      </td>
                      <td className={`px-4 py-3 text-right font-medium ${pnlColor}`}>
                        {pos.unrealized_pnl_pct >= 0 ? '+' : ''}{pos.unrealized_pnl_pct.toFixed(2)}%
                      </td>
                      <td className="px-4 py-3 text-right">
                        <span className="text-xs px-1.5 py-0.5 rounded bg-primary/10 text-primary">
                          {pos.broker}
                        </span>
                      </td>
                      <td className="px-4 py-3 text-center">
                        {pos.available > 0 && (
                          <button
                            onClick={() => handleSellClick(pos)}
                            className="px-2.5 py-1 text-xs font-medium bg-green-500/15 text-green-400 rounded-lg hover:bg-green-500/25 transition-colors"
                          >
                            卖出
                          </button>
                        )}
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        )}
      </Card>

      {/* 快速卖出确认框 */}
      {sellTarget && (
        <div className="fixed inset-0 bg-black/60 z-50 flex items-center justify-center p-4" onClick={() => setSellTarget(null)}>
          <div className="bg-dark-card border border-border rounded-2xl w-full max-w-sm p-6" onClick={(e) => e.stopPropagation()}>
            <h3 className="text-lg font-bold text-white mb-4">确认卖出</h3>
            <div className="space-y-3 text-sm">
              <div className="flex justify-between">
                <span className="text-gray-400">股票</span>
                <span className="text-white font-medium">{sellTarget.symbol}</span>
              </div>
              <div className="flex justify-between">
                <span className="text-gray-400">现价</span>
                <span className="text-white">{sellTarget.current_price.toFixed(2)}</span>
              </div>
              <div className="flex justify-between items-center">
                <span className="text-gray-400">卖出数量</span>
                <input
                  type="number"
                  value={sellQty}
                  onChange={(e) => setSellQty(Math.min(parseInt(e.target.value) || 0, sellTarget.available))}
                  min={1}
                  max={sellTarget.available}
                  className="w-24 bg-dark border border-border rounded-lg px-2 py-1 text-white text-sm text-right focus:border-primary outline-none"
                />
              </div>
              <div className="flex justify-between">
                <span className="text-gray-400">可用</span>
                <span className="text-gray-300">{sellTarget.available}</span>
              </div>
              <div className="flex justify-between">
                <span className="text-gray-400">预估金额</span>
                <span className="text-white font-medium">¥{(sellQty * sellTarget.current_price).toLocaleString('zh-CN', { maximumFractionDigits: 0 })}</span>
              </div>
            </div>
            <div className="flex gap-3 mt-6">
              <button
                onClick={handleSellConfirm}
                disabled={sellLoading || sellQty <= 0}
                className="flex-1 py-2.5 bg-green-500/20 hover:bg-green-500/30 text-green-400 rounded-lg text-sm font-medium transition-colors disabled:opacity-50"
              >
                {sellLoading ? '卖出中...' : '确认卖出'}
              </button>
              <button
                onClick={() => setSellTarget(null)}
                className="flex-1 py-2.5 bg-dark-light text-gray-300 rounded-lg text-sm font-medium hover:bg-gray-700 transition-colors"
              >
                取消
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
};

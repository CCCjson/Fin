/**
 * Tab 4: 执行历史
 */
import React, { useEffect, useState } from 'react';
import { useAutomationStore } from '../../stores/automationStore';

const statusLabels: Record<string, { text: string; color: string }> = {
  FILLED: { text: '已成交', color: 'text-green-400 bg-green-500/10' },
  REJECTED: { text: '已拒绝', color: 'text-red-400 bg-red-500/10' },
  EXPIRED: { text: '已过期', color: 'text-yellow-400 bg-yellow-500/10' },
  FAILED: { text: '失败', color: 'text-red-400 bg-red-500/10' },
  EXECUTING: { text: '执行中', color: 'text-blue-400 bg-blue-500/10' },
};

const brokerLabels: Record<string, string> = {
  paper: 'Paper',
  qmt: 'QMT',
  easytrader: 'EasyTrader',
};

export const ExecutionHistoryTab: React.FC = () => {
  const { executionHistory, fetchExecutionHistory } = useAutomationStore();
  const [statusFilter, setStatusFilter] = useState('');
  const [brokerFilter, setBrokerFilter] = useState('');

  useEffect(() => {
    fetchExecutionHistory({
      status: statusFilter || undefined,
      broker_type: brokerFilter || undefined,
    });
  }, [statusFilter, brokerFilter]);

  const statusFilters = [
    { label: '全部', value: '' },
    { label: '已成交', value: 'FILLED' },
    { label: '已拒绝', value: 'REJECTED' },
    { label: '已过期', value: 'EXPIRED' },
    { label: '失败', value: 'FAILED' },
  ];

  const brokerFilters = [
    { label: '全部', value: '' },
    { label: 'Paper', value: 'paper' },
    { label: 'QMT', value: 'qmt' },
    { label: 'EasyTrader', value: 'easytrader' },
  ];

  return (
    <div className="space-y-4">
      {/* 筛选栏 */}
      <div className="flex items-center gap-4 flex-wrap">
        <div className="flex items-center gap-2">
          <span className="text-xs text-gray-500">状态:</span>
          <div className="flex gap-1">
            {statusFilters.map((f) => (
              <button
                key={f.value}
                onClick={() => setStatusFilter(f.value)}
                className={`px-2.5 py-1 rounded-lg text-xs font-medium transition-colors ${
                  statusFilter === f.value
                    ? 'bg-primary text-white'
                    : 'bg-dark-card text-gray-400 hover:text-white'
                }`}
              >
                {f.label}
              </button>
            ))}
          </div>
        </div>
        <div className="flex items-center gap-2">
          <span className="text-xs text-gray-500">券商:</span>
          <div className="flex gap-1">
            {brokerFilters.map((f) => (
              <button
                key={f.value}
                onClick={() => setBrokerFilter(f.value)}
                className={`px-2.5 py-1 rounded-lg text-xs font-medium transition-colors ${
                  brokerFilter === f.value
                    ? 'bg-primary text-white'
                    : 'bg-dark-card text-gray-400 hover:text-white'
                }`}
              >
                {f.label}
              </button>
            ))}
          </div>
        </div>
      </div>

      {/* 表格 */}
      <div className="bg-gradient-card rounded-xl border border-border shadow-card overflow-hidden">
        {executionHistory.length === 0 ? (
          <div className="text-center py-16">
            <div className="text-4xl mb-3 opacity-40">📜</div>
            <p className="text-gray-500">暂无执行记录</p>
          </div>
        ) : (
          <div className="overflow-x-auto">
            <table className="w-full text-sm">
              <thead>
                <tr className="text-gray-500 text-xs border-b border-border/50">
                  <th className="text-left px-4 py-3 font-medium">时间</th>
                  <th className="text-left px-4 py-3 font-medium">股票</th>
                  <th className="text-center px-4 py-3 font-medium">方向</th>
                  <th className="text-right px-4 py-3 font-medium">数量</th>
                  <th className="text-right px-4 py-3 font-medium">成交价</th>
                  <th className="text-right px-4 py-3 font-medium">手续费</th>
                  <th className="text-center px-4 py-3 font-medium">来源</th>
                  <th className="text-center px-4 py-3 font-medium">券商</th>
                  <th className="text-center px-4 py-3 font-medium">状态</th>
                </tr>
              </thead>
              <tbody>
                {executionHistory.map((order) => {
                  const st = statusLabels[order.status] || { text: order.status, color: 'text-gray-400 bg-gray-500/10' };
                  const dirColor = order.signal_type === 'BUY' ? 'text-red-400 bg-red-500/10' : 'text-green-400 bg-green-500/10';
                  return (
                    <tr key={order.order_id} className="border-b border-border/30 hover:bg-dark/30 transition-colors">
                      <td className="px-4 py-3 text-gray-400 text-xs whitespace-nowrap">
                        {order.updated_at ? new Date(order.updated_at).toLocaleString('zh-CN', {
                          month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit',
                        }) : '-'}
                      </td>
                      <td className="px-4 py-3">
                        <div className="text-white font-medium">{order.symbol}</div>
                        <div className="text-gray-500 text-xs">{order.name}</div>
                      </td>
                      <td className="px-4 py-3 text-center">
                        <span className={`text-xs px-1.5 py-0.5 rounded font-medium ${dirColor}`}>
                          {order.signal_type === 'BUY' ? '买入' : '卖出'}
                        </span>
                      </td>
                      <td className="px-4 py-3 text-right text-gray-300">
                        {order.actual_quantity || order.suggested_quantity}
                      </td>
                      <td className="px-4 py-3 text-right text-white">
                        {order.actual_price ? order.actual_price.toFixed(2) : order.suggested_price?.toFixed(2) || '-'}
                      </td>
                      <td className="px-4 py-3 text-right text-gray-400">
                        {order.commission ? `¥${order.commission.toFixed(2)}` : '-'}
                      </td>
                      <td className="px-4 py-3 text-center">
                        <span className="text-xs text-gray-400">
                          {order.scan_source === 'manual' ? '手动' : order.strategy || order.scan_source}
                        </span>
                      </td>
                      <td className="px-4 py-3 text-center">
                        <span className="text-xs px-1.5 py-0.5 rounded bg-primary/10 text-primary">
                          {brokerLabels[order.broker_type] || order.broker_type}
                        </span>
                      </td>
                      <td className="px-4 py-3 text-center">
                        <span className={`text-xs px-1.5 py-0.5 rounded font-medium ${st.color}`}>
                          {st.text}
                        </span>
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        )}
      </div>
    </div>
  );
};

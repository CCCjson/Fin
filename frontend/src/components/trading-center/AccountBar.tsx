/**
 * 账户概览条 — 常驻顶部，显示 broker 连接状态 + 资产摘要
 */
import React, { useEffect } from 'react';
import { useAutomationStore } from '../../stores/automationStore';
import type { BrokerStatusItem } from '../../stores/automationStore';

const brokerLabels: Record<string, string> = {
  paper: 'Paper',
  qmt: 'QMT',
  easytrader: 'EasyTrader',
};

const BrokerBadge: React.FC<{ id: string; broker: BrokerStatusItem }> = ({ id, broker }) => (
  <div className="flex items-center gap-1.5 px-2.5 py-1 rounded-lg bg-dark/60 border border-border/50">
    <div className={`w-2 h-2 rounded-full flex-shrink-0 ${broker.online ? 'bg-green-500' : 'bg-gray-600'}`} />
    <span className="text-xs text-gray-300">{brokerLabels[id] || id}</span>
  </div>
);

const formatMoney = (val: number) => {
  if (Math.abs(val) >= 1e6) return `¥${(val / 1e6).toFixed(2)}M`;
  if (Math.abs(val) >= 1e4) return `¥${(val / 1e4).toFixed(1)}W`;
  return `¥${val.toFixed(0)}`;
};

export const AccountBar: React.FC = () => {
  const { brokerStatuses, brokerSummary, fetchBrokerStatus } = useAutomationStore();

  useEffect(() => {
    fetchBrokerStatus();
    const timer = setInterval(fetchBrokerStatus, 30000);
    return () => clearInterval(timer);
  }, []);

  const { total_value, total_cash, total_pnl } = brokerSummary;
  const pnlPct = total_value > 0 ? ((total_pnl / (total_value - total_pnl)) * 100) : 0;
  const pnlColor = total_pnl >= 0 ? 'text-red-400' : 'text-green-400';

  return (
    <div className="flex-shrink-0 bg-dark-card/50 border-b border-border/50">
      <div className="px-4 md:px-6 py-2 flex items-center justify-between gap-4 overflow-x-auto">
        {/* Broker 状态 */}
        <div className="flex items-center gap-2 flex-shrink-0">
          {Object.entries(brokerStatuses).map(([id, broker]) => (
            <BrokerBadge key={id} id={id} broker={broker} />
          ))}
          {Object.keys(brokerStatuses).length === 0 && (
            <span className="text-xs text-gray-600">加载中...</span>
          )}
        </div>

        {/* 资产摘要 */}
        <div className="flex items-center gap-4 md:gap-6 flex-shrink-0">
          <div className="text-center">
            <div className="text-[10px] text-gray-500 leading-none mb-0.5">总资产</div>
            <div className="text-sm font-semibold text-white">{formatMoney(total_value)}</div>
          </div>
          <div className="text-center">
            <div className="text-[10px] text-gray-500 leading-none mb-0.5">可用</div>
            <div className="text-sm font-semibold text-white">{formatMoney(total_cash)}</div>
          </div>
          <div className="text-center">
            <div className="text-[10px] text-gray-500 leading-none mb-0.5">盈亏</div>
            <div className={`text-sm font-semibold ${pnlColor}`}>
              {total_pnl >= 0 ? '+' : ''}{formatMoney(total_pnl)}
              <span className="text-[10px] ml-0.5">
                ({pnlPct >= 0 ? '+' : ''}{pnlPct.toFixed(2)}%)
              </span>
            </div>
          </div>
        </div>
      </div>
    </div>
  );
};

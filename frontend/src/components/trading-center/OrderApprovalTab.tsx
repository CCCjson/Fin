/**
 * Tab 1: 订单审批 — 提取自 Automation.tsx
 */
import React, { useState, useCallback } from 'react';
import { Card } from '../common/Card';
import { useAutomationStore } from '../../stores/automationStore';
import { PendingOrderCard } from '../automation/PendingOrderCard';

// ==================== 统计卡片行 ====================

const StatsRow: React.FC<{ stats: any; pendingCount: number }> = ({ stats, pendingCount }) => {
  const cards = [
    {
      label: '今日总单',
      value: stats.total || 0,
      color: 'text-white',
      icon: (
        <svg className="w-4 h-4 text-gray-500" fill="none" stroke="currentColor" viewBox="0 0 24 24">
          <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M9 5H7a2 2 0 00-2 2v12a2 2 0 002 2h10a2 2 0 002-2V7a2 2 0 00-2-2h-2M9 5a2 2 0 002 2h2a2 2 0 002-2M9 5a2 2 0 012-2h2a2 2 0 012 2" />
        </svg>
      ),
    },
    {
      label: '待确认',
      value: pendingCount,
      color: 'text-yellow-400',
      pulse: pendingCount > 0,
      icon: (
        <svg className="w-4 h-4 text-yellow-400/60" fill="none" stroke="currentColor" viewBox="0 0 24 24">
          <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M12 8v4l3 3m6-3a9 9 0 11-18 0 9 9 0 0118 0z" />
        </svg>
      ),
    },
    {
      label: '已成交',
      value: stats.filled || 0,
      color: 'text-green-400',
      icon: (
        <svg className="w-4 h-4 text-green-400/60" fill="none" stroke="currentColor" viewBox="0 0 24 24">
          <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M9 12l2 2 4-4m6 2a9 9 0 11-18 0 9 9 0 0118 0z" />
        </svg>
      ),
    },
    {
      label: '确认率',
      value: `${stats.confirm_rate || 0}%`,
      color: 'text-primary',
      icon: (
        <svg className="w-4 h-4 text-primary/60" fill="none" stroke="currentColor" viewBox="0 0 24 24">
          <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M13 7h8m0 0v8m0-8l-8 8-4-4-6 6" />
        </svg>
      ),
    },
    {
      label: '已拒绝/过期/失败',
      value: (stats.rejected || 0) + (stats.expired || 0) + (stats.failed || 0),
      color: 'text-gray-400',
      icon: (
        <svg className="w-4 h-4 text-gray-500" fill="none" stroke="currentColor" viewBox="0 0 24 24">
          <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M18.364 18.364A9 9 0 005.636 5.636m12.728 12.728A9 9 0 015.636 5.636m12.728 12.728L5.636 5.636" />
        </svg>
      ),
    },
  ];

  return (
    <div className="grid grid-cols-2 md:grid-cols-3 lg:grid-cols-5 gap-3 md:gap-4">
      {cards.map((c) => (
        <Card
          key={c.label}
          glow
          className="p-3 md:p-5 transition-all duration-300"
        >
          <div className="flex items-center gap-2 mb-2">
            {c.icon}
            <span className="text-xs text-gray-500">{c.label}</span>
          </div>
          <div className="flex items-center gap-2">
            <span className={`text-2xl font-bold ${c.color}`}>{c.value}</span>
            {c.pulse && (
              <span className="w-2 h-2 rounded-full bg-red-500 animate-pulse" />
            )}
          </div>
        </Card>
      ))}
    </div>
  );
};

// ==================== 主组件 ====================

export const OrderApprovalTab: React.FC = () => {
  const [selectedOrders, setSelectedOrders] = useState<Set<string>>(new Set());

  const store = useAutomationStore();

  // 只显示 PENDING 状态的订单，已处理的去"执行历史"Tab 查看
  const pendingOrders = store.pendingOrders.filter((o) => o.status === 'PENDING');
  const pendingCount = pendingOrders.length;
  const stats = store.statistics || {};

  const handleConfirm = useCallback(async (orderId: string) => {
    await store.confirmOrder(orderId);
  }, []);

  const handleReject = useCallback(async (orderId: string) => {
    await store.rejectOrder(orderId);
  }, []);

  const handleBatchConfirm = async () => {
    if (selectedOrders.size === 0) return;
    await store.batchConfirm(Array.from(selectedOrders));
    setSelectedOrders(new Set());
  };

  const handleBatchReject = async () => {
    if (selectedOrders.size === 0) return;
    await store.batchReject(Array.from(selectedOrders));
    setSelectedOrders(new Set());
  };

  const handleSelectOrder = (orderId: string, checked: boolean) => {
    setSelectedOrders((prev) => {
      const next = new Set(prev);
      if (checked) next.add(orderId);
      else next.delete(orderId);
      return next;
    });
  };

  return (
    <div className="space-y-4">
      <StatsRow stats={stats} pendingCount={pendingCount} />

      <Card className="p-4 md:p-5">
        {/* 标题栏 */}
        <div className="flex items-center justify-between mb-4 flex-wrap gap-2">
          <div className="flex items-center gap-3">
            <h2 className="text-white font-medium text-lg">待确认订单</h2>
            {pendingCount > 0 && (
              <span className="text-xs text-gray-500">{pendingCount} 条待处理</span>
            )}
          </div>

          {selectedOrders.size > 0 && (
            <div className="flex gap-2">
              <button
                onClick={handleBatchConfirm}
                className="px-3 py-1.5 bg-green-500/20 text-green-400 rounded-lg text-xs font-medium hover:bg-green-500/30"
              >
                批量确认 ({selectedOrders.size})
              </button>
              <button
                onClick={handleBatchReject}
                className="px-3 py-1.5 bg-red-500/20 text-red-400 rounded-lg text-xs font-medium hover:bg-red-500/30"
              >
                批量拒绝 ({selectedOrders.size})
              </button>
            </div>
          )}
        </div>

        {/* 订单列表 */}
        {pendingOrders.length === 0 ? (
          <div className="text-center py-16">
            <div className="text-4xl mb-3 opacity-40">📋</div>
            <p className="text-gray-500">暂无待确认订单</p>
            <p className="text-gray-600 text-xs mt-1">等待策略扫描产生信号...</p>
          </div>
        ) : (
          <div className="space-y-3">
            {pendingOrders.map((order) => (
              <PendingOrderCard
                key={order.order_id}
                order={order}
                onConfirm={handleConfirm}
                onReject={handleReject}
                selected={selectedOrders.has(order.order_id)}
                onSelect={handleSelectOrder}
              />
            ))}
          </div>
        )}
      </Card>
    </div>
  );
};

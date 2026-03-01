/**
 * 统一交易中心 — Tab-based 布局
 *
 * 5 个 Tab：订单审批 | 持仓总览 | 策略配置 | 执行历史 | 手动下单
 */
import React, { useState, useEffect } from 'react';
import { useNavigate } from 'react-router-dom';
import { useAutomationStore } from '../stores/automationStore';

import { AccountBar } from '../components/trading-center/AccountBar';
import { OrderApprovalTab } from '../components/trading-center/OrderApprovalTab';
import { PositionsTab } from '../components/trading-center/PositionsTab';
import { StrategyConfigTab } from '../components/trading-center/StrategyConfigTab';
import { ExecutionHistoryTab } from '../components/trading-center/ExecutionHistoryTab';
import { ManualOrderTab } from '../components/trading-center/ManualOrderTab';

// Tab 定义
const tabs = [
  { key: 'orders', label: '订单审批' },
  { key: 'positions', label: '持仓总览' },
  { key: 'strategy', label: '策略配置' },
  { key: 'history', label: '执行历史' },
  { key: 'manual', label: '手动下单' },
] as const;

type TabKey = typeof tabs[number]['key'];

export const Automation: React.FC = () => {
  const [activeTab, setActiveTab] = useState<TabKey>('orders');
  const store = useAutomationStore();
  const nav = useNavigate();

  // 初始化
  useEffect(() => {
    store.fetchPendingOrders();
    store.fetchConfigs();
    store.fetchSchedulerStatus();
    store.fetchLogs();
    store.fetchStatistics();
    store.connectWebSocket();

    return () => {
      store.disconnectWebSocket();
    };
  }, []);

  // 定期刷新待确认订单
  useEffect(() => {
    const timer = setInterval(() => {
      store.fetchPendingOrders();
    }, 15000);
    return () => clearInterval(timer);
  }, []);

  const pendingCount = store.pendingOrders.filter((o) => o.status === 'PENDING').length;

  return (
    <div className="h-screen flex flex-col bg-dark">
      {/* ===== 顶栏 ===== */}
      <div className="flex-shrink-0 bg-dark-card border-b border-border">
        <div className="px-4 md:px-6 py-3 flex items-center justify-between">
          <div className="flex items-center gap-3">
            <span
              onClick={() => nav('/')}
              className="text-xl cursor-pointer hover:scale-110 transition-transform"
              title="返回主页"
            >💰</span>
            <h1 className="text-xl font-bold text-white">交易中心</h1>
            {pendingCount > 0 && (
              <span className="bg-red-500 text-white text-xs px-2 py-0.5 rounded-full animate-pulse">
                {pendingCount} 待处理
              </span>
            )}
          </div>

          <div className="flex items-center gap-3">
            {/* 调度器状态 */}
            <div className="flex items-center gap-2">
              <div className={`w-2.5 h-2.5 rounded-full ${
                store.schedulerRunning ? 'bg-green-500 animate-pulse' : 'bg-gray-500'
              }`} />
              <span className="text-sm text-gray-400 hidden sm:inline">
                {store.schedulerRunning ? '调度运行中' : '调度已停止'}
              </span>
            </div>

            {/* 启停按钮 */}
            <button
              onClick={() => store.schedulerRunning ? store.stopScheduler() : store.startScheduler()}
              className={`px-3 py-1.5 rounded-lg text-sm font-medium transition-all ${
                store.schedulerRunning
                  ? 'bg-red-500/20 text-red-400 hover:bg-red-500/30'
                  : 'bg-green-500/20 text-green-400 hover:bg-green-500/30'
              }`}
            >
              {store.schedulerRunning ? '停止' : '启动'}
            </button>

            {/* WS 状态 */}
            {store.wsConnected && (
              <div className="flex items-center gap-1">
                <div className="w-1.5 h-1.5 rounded-full bg-green-500" />
                <span className="text-xs text-gray-500">WS</span>
              </div>
            )}
            {store.unreadCount > 0 && (
              <button
                onClick={() => store.markAllRead()}
                className="text-xs text-gray-500 hover:text-gray-300"
              >
                {store.unreadCount} 未读
              </button>
            )}
          </div>
        </div>
      </div>

      {/* ===== 账户概览条 ===== */}
      <AccountBar />

      {/* ===== Tab 栏 ===== */}
      <div className="flex-shrink-0 bg-dark-card/30 border-b border-border/50">
        <div className="px-4 md:px-6 flex gap-1 overflow-x-auto">
          {tabs.map((tab) => {
            const isActive = activeTab === tab.key;
            const hasBadge = tab.key === 'orders' && pendingCount > 0;
            return (
              <button
                key={tab.key}
                onClick={() => setActiveTab(tab.key)}
                className={`relative px-4 py-2.5 text-sm font-medium whitespace-nowrap transition-all border-b-2 ${
                  isActive
                    ? 'text-primary border-primary'
                    : 'text-gray-500 border-transparent hover:text-gray-300'
                }`}
              >
                {tab.label}
                {hasBadge && (
                  <span className="absolute -top-0.5 -right-0.5 w-2 h-2 rounded-full bg-red-500 animate-pulse" />
                )}
              </button>
            );
          })}
        </div>
      </div>

      {/* ===== Tab 内容区 ===== */}
      <div className="flex-1 overflow-auto p-3 md:p-6 pb-20 md:pb-6">
        {activeTab === 'orders' && <OrderApprovalTab />}
        {activeTab === 'positions' && <PositionsTab />}
        {activeTab === 'strategy' && <StrategyConfigTab />}
        {activeTab === 'history' && <ExecutionHistoryTab />}
        {activeTab === 'manual' && <ManualOrderTab />}
      </div>
    </div>
  );
};

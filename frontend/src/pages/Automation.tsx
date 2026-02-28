/**
 * 自动化交易 Dashboard — 单页面布局
 */
import React, { useState, useEffect, useCallback } from 'react';
import { useNavigate } from 'react-router-dom';
import { useAutomationStore } from '../stores/automationStore';
import { PendingOrderCard } from '../components/automation/PendingOrderCard';
import type { AutomationConfigItem } from '../stores/automationStore';

// ==================== 主页面 ====================

export const Automation: React.FC = () => {
  const [selectedOrders, setSelectedOrders] = useState<Set<string>>(new Set());
  const [statusFilter, setStatusFilter] = useState<string>('');
  const [showConfigModal, setShowConfigModal] = useState(false);
  const [editingConfig, setEditingConfig] = useState<AutomationConfigItem | null>(null);

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

  // 定期刷新
  useEffect(() => {
    const timer = setInterval(() => {
      store.fetchPendingOrders(statusFilter || undefined);
    }, 15000);
    return () => clearInterval(timer);
  }, [statusFilter]);

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

  const handleFilterChange = (status: string) => {
    setStatusFilter(status);
    store.fetchPendingOrders(status || undefined);
  };

  const filteredOrders = store.pendingOrders;
  const pendingCount = store.pendingOrders.filter((o) => o.status === 'PENDING').length;
  const stats = store.statistics || {};

  return (
    <div className="h-screen flex flex-col bg-dark">
      {/* ===== 顶栏 ===== */}
      <div className="flex-shrink-0 bg-dark-card border-b border-border">
        <div className="px-4 md:px-6 py-3 flex items-center justify-between">
          <div className="flex items-center gap-3">
            <button
              onClick={() => nav('/')}
              className="flex items-center gap-1.5 text-gray-400 hover:text-white transition-colors mr-2"
              title="返回主页"
            >
              <svg className="w-5 h-5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M15 19l-7-7 7-7" />
              </svg>
              <span className="text-sm hidden md:inline">主页</span>
            </button>
            <div className="w-px h-5 bg-border hidden md:block" />
            <span className="text-xl">💰</span>
            <h1 className="text-xl font-bold text-white">自动化交易</h1>
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

      {/* ===== 可滚动内容区 ===== */}
      <div className="flex-1 overflow-auto p-3 md:p-6 pb-20 md:pb-6">
        {/* 统计卡片行 */}
        <StatsRow stats={stats} pendingCount={pendingCount} />

        {/* 主体区 — 左右分栏 */}
        <div className="mt-4 md:mt-6 flex flex-col lg:flex-row gap-4 md:gap-6">
          {/* 左侧：待确认订单 */}
          <div className="flex-[3] min-w-0">
            <OrdersSection
              orders={filteredOrders}
              statusFilter={statusFilter}
              onFilterChange={handleFilterChange}
              selectedOrders={selectedOrders}
              onSelectOrder={handleSelectOrder}
              onConfirm={handleConfirm}
              onReject={handleReject}
              onBatchConfirm={handleBatchConfirm}
              onBatchReject={handleBatchReject}
            />
          </div>

          {/* 右侧：策略配置 + 最近运行 */}
          <div className="flex-[2] min-w-0 flex flex-col gap-4 md:gap-6">
            <ConfigsSection
              configs={store.configs}
              onNew={() => { setEditingConfig(null); setShowConfigModal(true); }}
              onEdit={(c) => { setEditingConfig(c); setShowConfigModal(true); }}
              onToggle={(id) => store.toggleConfig(id)}
              onTrigger={(id) => store.triggerScan(id)}
            />
            <LogsSection logs={store.logs} />
          </div>
        </div>
      </div>

      {/* 配置弹窗 */}
      {showConfigModal && (
        <ConfigModal
          config={editingConfig}
          onClose={() => setShowConfigModal(false)}
          onSave={async (data) => {
            if (editingConfig) {
              await store.updateConfig(editingConfig.config_id, data);
            } else {
              await store.createConfig(data);
            }
            setShowConfigModal(false);
          }}
        />
      )}
    </div>
  );
};

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
        <div
          key={c.label}
          className="bg-gradient-card p-3 md:p-5 rounded-xl border border-border shadow-card hover:shadow-glow-blue transition-all duration-300"
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
        </div>
      ))}
    </div>
  );
};

// ==================== 待确认订单面板 ====================

const OrdersSection: React.FC<{
  orders: any[];
  statusFilter: string;
  onFilterChange: (s: string) => void;
  selectedOrders: Set<string>;
  onSelectOrder: (id: string, checked: boolean) => void;
  onConfirm: (id: string) => void;
  onReject: (id: string) => void;
  onBatchConfirm: () => void;
  onBatchReject: () => void;
}> = ({ orders, statusFilter, onFilterChange, selectedOrders, onSelectOrder, onConfirm, onReject, onBatchConfirm, onBatchReject }) => {
  const filters = [
    { label: '全部', value: '' },
    { label: '待确认', value: 'PENDING' },
    { label: '已成交', value: 'FILLED' },
    { label: '已处理', value: 'REJECTED' },
  ];

  return (
    <div className="bg-gradient-card rounded-xl border border-border shadow-card p-4 md:p-5">
      {/* 标题栏 + 筛选 */}
      <div className="flex items-center justify-between mb-4 flex-wrap gap-2">
        <div className="flex items-center gap-3">
          <h2 className="text-white font-medium text-lg">待确认订单</h2>
          <div className="flex gap-1">
            {filters.map((f) => (
              <button
                key={f.value}
                onClick={() => onFilterChange(f.value)}
                className={`px-3 py-1 rounded-lg text-xs font-medium transition-colors ${
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

        {/* 批量操作 */}
        {selectedOrders.size > 0 && (
          <div className="flex gap-2">
            <button
              onClick={onBatchConfirm}
              className="px-3 py-1.5 bg-green-500/20 text-green-400 rounded-lg text-xs font-medium hover:bg-green-500/30"
            >
              批量确认 ({selectedOrders.size})
            </button>
            <button
              onClick={onBatchReject}
              className="px-3 py-1.5 bg-red-500/20 text-red-400 rounded-lg text-xs font-medium hover:bg-red-500/30"
            >
              批量拒绝 ({selectedOrders.size})
            </button>
          </div>
        )}
      </div>

      {/* 订单列表 */}
      {orders.length === 0 ? (
        <div className="text-center py-16">
          <div className="text-4xl mb-3 opacity-40">📋</div>
          <p className="text-gray-500">暂无待确认订单</p>
          <p className="text-gray-600 text-xs mt-1">等待策略扫描产生信号...</p>
        </div>
      ) : (
        <div className="space-y-3">
          {orders.map((order) => (
            <PendingOrderCard
              key={order.order_id}
              order={order}
              onConfirm={onConfirm}
              onReject={onReject}
              selected={selectedOrders.has(order.order_id)}
              onSelect={onSelectOrder}
            />
          ))}
        </div>
      )}
    </div>
  );
};

// ==================== 策略配置面板 ====================

const ConfigsSection: React.FC<{
  configs: AutomationConfigItem[];
  onNew: () => void;
  onEdit: (c: AutomationConfigItem) => void;
  onToggle: (id: string) => void;
  onTrigger: (id: string) => void;
}> = ({ configs, onNew, onEdit, onToggle, onTrigger }) => {
  const [showAll, setShowAll] = useState(false);

  const scanTypeLabel: Record<string, string> = {
    daily: '日线',
    intraday: '盘中',
    position_check: '持仓',
  };

  const displayed = showAll ? configs : configs.slice(0, 4);

  return (
    <div className="bg-gradient-card rounded-xl border border-border shadow-card p-4 md:p-5">
      <div className="flex items-center justify-between mb-4">
        <h2 className="text-white font-medium">策略配置</h2>
        <button
          onClick={onNew}
          className="px-3 py-1.5 rounded-lg text-xs font-medium border border-primary/30 text-primary hover:bg-primary/10 transition-all"
        >
          + 新建
        </button>
      </div>

      {configs.length === 0 ? (
        <div className="text-center py-8">
          <p className="text-gray-500 text-sm">还没有配置，点击上方按钮创建第一个～</p>
        </div>
      ) : (
        <div className="space-y-2">
          {displayed.map((c) => (
            <div
              key={c.config_id}
              className="flex items-center justify-between bg-dark/50 rounded-lg px-3 py-2.5 group hover:bg-dark/80 transition-colors"
            >
              <div className="flex items-center gap-3 min-w-0">
                <span className="text-sm text-white truncate">{c.name}</span>
                <span className="text-xs px-1.5 py-0.5 rounded bg-primary/10 text-primary flex-shrink-0">
                  {scanTypeLabel[c.scan_type] || c.scan_type}
                </span>
              </div>
              <div className="flex items-center gap-2 flex-shrink-0">
                <button
                  onClick={() => onTrigger(c.config_id)}
                  className="text-xs px-2 py-1 text-primary/60 hover:text-primary hover:bg-primary/10 rounded transition-all opacity-0 group-hover:opacity-100"
                  title="手动触发"
                >
                  触发
                </button>
                <button
                  onClick={() => onEdit(c)}
                  className="text-xs px-2 py-1 text-gray-500 hover:text-white rounded transition-all opacity-0 group-hover:opacity-100"
                >
                  编辑
                </button>
                <button
                  onClick={() => onToggle(c.config_id)}
                  className={`relative w-9 h-5 rounded-full transition-colors flex-shrink-0 ${c.enabled ? 'bg-green-500' : 'bg-gray-600'}`}
                >
                  <div className={`absolute top-0.5 w-4 h-4 rounded-full bg-white transition-transform ${c.enabled ? 'left-4' : 'left-0.5'}`} />
                </button>
              </div>
            </div>
          ))}
          {configs.length > 4 && !showAll && (
            <button
              onClick={() => setShowAll(true)}
              className="w-full text-center text-xs text-primary/60 hover:text-primary py-2 transition-colors"
            >
              查看全部 ({configs.length})
            </button>
          )}
        </div>
      )}
    </div>
  );
};

// ==================== 最近运行面板 ====================

const LogsSection: React.FC<{ logs: any[] }> = ({ logs }) => {
  const recentLogs = logs.slice(0, 5);

  const runTypeLabel: Record<string, string> = {
    daily: '日线扫描',
    intraday: '盘中扫描',
    position_check: '持仓检查',
  };

  return (
    <div className="bg-gradient-card rounded-xl border border-border shadow-card p-4 md:p-5">
      <h2 className="text-white font-medium mb-4">最近运行</h2>

      {recentLogs.length === 0 ? (
        <div className="text-center py-6">
          <p className="text-gray-500 text-sm">暂无运行记录</p>
        </div>
      ) : (
        <div className="space-y-1">
          {recentLogs.map((log: any, idx: number) => (
            <div key={log.id || idx} className="flex items-center gap-3 py-2 border-b border-border/50 last:border-0">
              {/* 时间线节点 */}
              <div className="flex-shrink-0 relative">
                <div className={`w-2 h-2 rounded-full ${
                  log.status === 'completed' ? 'bg-green-500' : log.status === 'failed' ? 'bg-red-500' : 'bg-blue-500 animate-pulse'
                }`} />
                {idx < recentLogs.length - 1 && (
                  <div className="absolute top-3 left-[3px] w-px h-4 bg-border/50" />
                )}
              </div>

              {/* 内容 */}
              <div className="flex-1 min-w-0 flex items-center justify-between gap-2">
                <div className="flex items-center gap-2 min-w-0">
                  <span className="text-xs text-gray-500 flex-shrink-0">
                    {log.started_at && new Date(log.started_at).toLocaleTimeString('zh-CN', { hour: '2-digit', minute: '2-digit' })}
                  </span>
                  <span className="text-xs text-gray-300 truncate">
                    {runTypeLabel[log.run_type] || log.run_type}
                  </span>
                </div>
                <div className="flex items-center gap-2 flex-shrink-0">
                  {log.signals_found > 0 && (
                    <span className="text-xs text-yellow-400">{log.signals_found}信号</span>
                  )}
                  {log.orders_created > 0 && (
                    <span className="text-xs text-primary">{log.orders_created}单</span>
                  )}
                  <span className={`text-xs ${log.status === 'completed' ? 'text-green-500' : log.status === 'failed' ? 'text-red-500' : 'text-blue-400'}`}>
                    {log.status === 'completed' ? '✓' : log.status === 'failed' ? '✗' : '⏳'}
                  </span>
                </div>
              </div>
            </div>
          ))}
        </div>
      )}
    </div>
  );
};

// ==================== 配置弹窗 ====================

const ConfigModal: React.FC<{
  config: AutomationConfigItem | null;
  onClose: () => void;
  onSave: (data: any) => Promise<void>;
}> = ({ config, onClose, onSave }) => {
  const [form, setForm] = useState({
    name: config?.name || '',
    scan_type: config?.scan_type || 'daily',
    frequency_minutes: config?.frequency_minutes || 5,
    strategies: config?.strategies || ['MACD', 'KDJ', 'RSI', 'MA'],
    watchlist: config?.watchlist?.join(',') || '',
    min_strength: config?.min_strength || 0.6,
    broker_type: config?.broker_type || 'paper',
    position_size_pct: config?.position_size_pct || 0.10,
    order_expire_minutes: config?.order_expire_minutes || 30,
  });
  const [saving, setSaving] = useState(false);

  const handleSave = async () => {
    setSaving(true);
    await onSave({
      ...form,
      watchlist: form.watchlist ? form.watchlist.split(',').map((s) => s.trim()).filter(Boolean) : [],
    });
    setSaving(false);
  };

  const allStrategies = ['MACD', 'KDJ', 'RSI', 'MA'];

  return (
    <div className="fixed inset-0 bg-black/60 z-50 flex items-center justify-center p-4" onClick={onClose}>
      <div className="bg-dark-card border border-border rounded-2xl w-full max-w-lg max-h-[90vh] overflow-auto" onClick={(e) => e.stopPropagation()}>
        <div className="p-6">
          <h2 className="text-lg font-bold text-white mb-4">{config ? '编辑配置' : '新建配置'}</h2>

          <div className="space-y-4">
            {/* 名称 */}
            <div>
              <label className="block text-sm text-gray-400 mb-1">配置名称</label>
              <input
                className="w-full bg-dark border border-border rounded-lg px-3 py-2 text-white text-sm focus:border-primary outline-none"
                value={form.name}
                onChange={(e) => setForm({ ...form, name: e.target.value })}
                placeholder="例如：日线信号扫描"
              />
            </div>

            {/* 扫描类型 */}
            <div>
              <label className="block text-sm text-gray-400 mb-1">扫描类型</label>
              <select
                className="w-full bg-dark border border-border rounded-lg px-3 py-2 text-white text-sm focus:border-primary outline-none"
                value={form.scan_type}
                onChange={(e) => setForm({ ...form, scan_type: e.target.value })}
              >
                <option value="daily">日线扫描（每日 15:30）</option>
                <option value="intraday">盘中扫描（按间隔）</option>
                <option value="position_check">持仓检查（止损止盈）</option>
              </select>
            </div>

            {/* 频率 */}
            {form.scan_type !== 'daily' && (
              <div>
                <label className="block text-sm text-gray-400 mb-1">扫描间隔（分钟）</label>
                <input
                  type="number"
                  className="w-full bg-dark border border-border rounded-lg px-3 py-2 text-white text-sm focus:border-primary outline-none"
                  value={form.frequency_minutes}
                  onChange={(e) => setForm({ ...form, frequency_minutes: parseInt(e.target.value) || 5 })}
                  min={1}
                  max={60}
                />
              </div>
            )}

            {/* 策略 */}
            <div>
              <label className="block text-sm text-gray-400 mb-1">策略</label>
              <div className="flex flex-wrap gap-2">
                {allStrategies.map((s) => (
                  <button
                    key={s}
                    onClick={() => {
                      const next = form.strategies.includes(s)
                        ? form.strategies.filter((x) => x !== s)
                        : [...form.strategies, s];
                      setForm({ ...form, strategies: next });
                    }}
                    className={`px-3 py-1.5 rounded-lg text-xs font-medium transition-colors ${
                      form.strategies.includes(s)
                        ? 'bg-primary text-white'
                        : 'bg-dark text-gray-400 hover:text-white'
                    }`}
                  >
                    {s}
                  </button>
                ))}
              </div>
            </div>

            {/* 监控列表 */}
            <div>
              <label className="block text-sm text-gray-400 mb-1">监控股票（逗号分隔，留空则扫描全市场）</label>
              <textarea
                className="w-full bg-dark border border-border rounded-lg px-3 py-2 text-white text-sm focus:border-primary outline-none resize-none h-20"
                value={form.watchlist}
                onChange={(e) => setForm({ ...form, watchlist: e.target.value })}
                placeholder="600519, 000858, 002594"
              />
            </div>

            {/* 参数行 */}
            <div className="grid grid-cols-2 gap-3">
              <div>
                <label className="block text-sm text-gray-400 mb-1">最低强度</label>
                <input
                  type="number"
                  step="0.1"
                  min="0"
                  max="1"
                  className="w-full bg-dark border border-border rounded-lg px-3 py-2 text-white text-sm focus:border-primary outline-none"
                  value={form.min_strength}
                  onChange={(e) => setForm({ ...form, min_strength: parseFloat(e.target.value) || 0.6 })}
                />
              </div>
              <div>
                <label className="block text-sm text-gray-400 mb-1">仓位占比</label>
                <input
                  type="number"
                  step="0.05"
                  min="0.01"
                  max="0.5"
                  className="w-full bg-dark border border-border rounded-lg px-3 py-2 text-white text-sm focus:border-primary outline-none"
                  value={form.position_size_pct}
                  onChange={(e) => setForm({ ...form, position_size_pct: parseFloat(e.target.value) || 0.1 })}
                />
              </div>
            </div>

            <div className="grid grid-cols-2 gap-3">
              <div>
                <label className="block text-sm text-gray-400 mb-1">券商</label>
                <select
                  className="w-full bg-dark border border-border rounded-lg px-3 py-2 text-white text-sm focus:border-primary outline-none"
                  value={form.broker_type}
                  onChange={(e) => setForm({ ...form, broker_type: e.target.value })}
                >
                  <option value="paper">模拟交易</option>
                  <option value="easytrader">实盘 (EasyTrader)</option>
                </select>
              </div>
              <div>
                <label className="block text-sm text-gray-400 mb-1">过期时间（分钟）</label>
                <input
                  type="number"
                  className="w-full bg-dark border border-border rounded-lg px-3 py-2 text-white text-sm focus:border-primary outline-none"
                  value={form.order_expire_minutes}
                  onChange={(e) => setForm({ ...form, order_expire_minutes: parseInt(e.target.value) || 30 })}
                  min={5}
                  max={240}
                />
              </div>
            </div>
          </div>

          {/* 按钮 */}
          <div className="flex gap-3 mt-6">
            <button
              onClick={handleSave}
              disabled={saving || !form.name}
              className="flex-1 py-2.5 bg-primary hover:bg-primary-light text-white rounded-lg text-sm font-medium transition-colors disabled:opacity-50"
            >
              {saving ? '保存中...' : '保存'}
            </button>
            <button
              onClick={onClose}
              className="flex-1 py-2.5 bg-dark-light text-gray-300 rounded-lg text-sm font-medium hover:bg-gray-700 transition-colors"
            >
              取消
            </button>
          </div>
        </div>
      </div>
    </div>
  );
};

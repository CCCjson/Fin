/**
 * Tab 3: 策略配置 — 提取自 Automation.tsx
 */
import React, { useState } from 'react';
import { Card } from '../common/Card';
import { useAutomationStore } from '../../stores/automationStore';
import type { AutomationConfigItem } from '../../stores/automationStore';

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
    <Card className="p-4 md:p-5">
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
    </Card>
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
    <Card className="p-4 md:p-5">
      <h2 className="text-white font-medium mb-4">最近运行</h2>

      {recentLogs.length === 0 ? (
        <div className="text-center py-6">
          <p className="text-gray-500 text-sm">暂无运行记录</p>
        </div>
      ) : (
        <div className="space-y-1">
          {recentLogs.map((log: any, idx: number) => (
            <div key={log.id || idx} className="flex items-center gap-3 py-2 border-b border-border/50 last:border-0">
              <div className="flex-shrink-0 relative">
                <div className={`w-2 h-2 rounded-full ${
                  log.status === 'completed' ? 'bg-green-500' : log.status === 'failed' ? 'bg-red-500' : 'bg-accent-cyan animate-pulse'
                }`} />
                {idx < recentLogs.length - 1 && (
                  <div className="absolute top-3 left-[3px] w-px h-4 bg-border/50" />
                )}
              </div>
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
                  <span className={`text-xs ${log.status === 'completed' ? 'text-green-500' : log.status === 'failed' ? 'text-red-500' : 'text-accent-cyan'}`}>
                    {log.status === 'completed' ? '✓' : log.status === 'failed' ? '✗' : '⏳'}
                  </span>
                </div>
              </div>
            </div>
          ))}
        </div>
      )}
    </Card>
  );
};

// ==================== 配置弹窗 ====================

export const ConfigModal: React.FC<{
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
            <div>
              <label className="block text-sm text-gray-400 mb-1">配置名称</label>
              <input
                className="w-full bg-dark border border-border rounded-lg px-3 py-2 text-white text-sm focus:border-primary outline-none"
                value={form.name}
                onChange={(e) => setForm({ ...form, name: e.target.value })}
                placeholder="例如：日线信号扫描"
              />
            </div>

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

            <div>
              <label className="block text-sm text-gray-400 mb-1">策略</label>
              <div className="flex flex-wrap gap-2">
                {allStrategies.map((s) => (
                  <button
                    key={s}
                    type="button"
                    onClick={() => {
                      const next = form.strategies.includes(s)
                        ? form.strategies.filter((x) => x !== s)
                        : [...form.strategies, s];
                      setForm({ ...form, strategies: next });
                    }}
                    className={`px-3 py-1.5 rounded-lg text-xs font-medium transition-colors ${
                      form.strategies.includes(s)
                        ? 'bg-primary text-dark'
                        : 'bg-dark text-gray-400 hover:text-white'
                    }`}
                  >
                    {s}
                  </button>
                ))}
              </div>
            </div>

            <div>
              <label className="block text-sm text-gray-400 mb-1">监控股票（逗号分隔，留空则扫描全市场）</label>
              <textarea
                className="w-full bg-dark border border-border rounded-lg px-3 py-2 text-white text-sm focus:border-primary outline-none resize-none h-20"
                value={form.watchlist}
                onChange={(e) => setForm({ ...form, watchlist: e.target.value })}
                placeholder="600519, 000858, 002594"
              />
            </div>

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

          <div className="flex gap-3 mt-6">
            <button
              onClick={handleSave}
              disabled={saving || !form.name}
              className="flex-1 py-2.5 bg-primary hover:bg-primary-light text-dark rounded-lg text-sm font-medium transition-colors disabled:opacity-50"
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

// ==================== 主组件 ====================

export const StrategyConfigTab: React.FC = () => {
  const store = useAutomationStore();
  const [showConfigModal, setShowConfigModal] = useState(false);
  const [editingConfig, setEditingConfig] = useState<AutomationConfigItem | null>(null);

  return (
    <div className="flex flex-col lg:flex-row gap-4 md:gap-6">
      <div className="flex-1">
        <ConfigsSection
          configs={store.configs}
          onNew={() => { setEditingConfig(null); setShowConfigModal(true); }}
          onEdit={(c) => { setEditingConfig(c); setShowConfigModal(true); }}
          onToggle={(id) => store.toggleConfig(id)}
          onTrigger={(id) => store.triggerScan(id)}
        />
      </div>
      <div className="flex-1">
        <LogsSection logs={store.logs} />
      </div>

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

import React, { useState, useEffect, useCallback } from 'react';
import { StockSymbolInput } from '../components/common/StockSymbolInput';
import { watchlistService } from '../services/watchlistService';
import type { WatchlistItem, PriceAlert } from '../services/watchlistService';
import realtimeService from '../services/realtimeService';

const ALERT_TYPE_LABEL: Record<string, string> = {
  price_above: '价格突破',
  price_below: '价格跌破',
  pct_change: '涨跌幅达',
};

export const Watchlist: React.FC = () => {
  const [items, setItems] = useState<WatchlistItem[]>([]);
  const [groups, setGroups] = useState<string[]>([]);
  const [activeGroup, setActiveGroup] = useState<string>('全部');
  const [quotes, setQuotes] = useState<Record<string, { price: number | null; change_pct: number | null }>>({});
  const [quoteLoading, setQuoteLoading] = useState(false);

  // 添加
  const [newSymbol, setNewSymbol] = useState('');
  const [newName, setNewName] = useState('');
  const [newGroup, setNewGroup] = useState('默认分组');

  // 预警
  const [alerts, setAlerts] = useState<PriceAlert[]>([]);
  const [alertModal, setAlertModal] = useState<WatchlistItem | null>(null);
  const [alertType, setAlertType] = useState('price_above');
  const [alertThreshold, setAlertThreshold] = useState('');
  const [alertRepeat, setAlertRepeat] = useState(false);

  const loadAll = useCallback(async () => {
    const [its, grps, als] = await Promise.all([
      watchlistService.list(),
      watchlistService.groups(),
      watchlistService.listAlerts(),
    ]);
    setItems(its); setGroups(grps); setAlerts(als);
  }, []);

  useEffect(() => { loadAll(); }, [loadAll]);

  const refreshQuotes = async () => {
    setQuoteLoading(true);
    try {
      const res = await realtimeService.getQuotes({ save: false });
      const map: Record<string, { price: number | null; change_pct: number | null }> = {};
      for (const q of res.data) map[q.symbol] = { price: q.price, change_pct: q.change_pct };
      setQuotes(map);
    } catch (e) {
      console.error('刷新行情失败', e);
    } finally {
      setQuoteLoading(false);
    }
  };

  const addItem = async () => {
    if (!newSymbol.trim()) return;
    await watchlistService.add({ symbol: newSymbol.trim(), name: newName || undefined, group_name: newGroup || '默认分组' });
    setNewSymbol(''); setNewName('');
    await loadAll();
  };

  const removeItem = async (id: number) => {
    await watchlistService.remove(id);
    await loadAll();
  };

  const submitAlert = async () => {
    if (!alertModal || !alertThreshold) return;
    await watchlistService.createAlert({
      symbol: alertModal.symbol,
      name: alertModal.name || undefined,
      alert_type: alertType,
      threshold: parseFloat(alertThreshold),
      repeat: alertRepeat ? 1 : 0,
    });
    setAlertModal(null); setAlertThreshold(''); setAlertRepeat(false);
    await loadAll();
  };

  const toggleAlert = async (a: PriceAlert) => {
    await watchlistService.updateAlertStatus(a.alert_id, a.status === 'active' ? 'paused' : 'active');
    setAlerts(await watchlistService.listAlerts());
  };
  const delAlert = async (a: PriceAlert) => {
    await watchlistService.removeAlert(a.alert_id);
    setAlerts(await watchlistService.listAlerts());
  };

  const filtered = activeGroup === '全部' ? items : items.filter((i) => i.group_name === activeGroup);

  return (
    <div className="p-4 md:p-6 max-w-6xl mx-auto">
      <div className="flex items-center justify-between mb-4">
        <div>
          <h1 className="text-xl font-bold text-white">⭐ 自选股</h1>
          <p className="text-gray-500 text-sm">收藏分组 + 实时盯市 + 到价预警（预警后台常驻，盘中自动检查）</p>
        </div>
        <button
          onClick={refreshQuotes}
          disabled={quoteLoading}
          className="px-4 py-2 rounded-lg bg-primary text-dark text-sm hover:bg-primary/80 disabled:opacity-50"
        >
          {quoteLoading ? '刷新中…' : '🔄 刷新行情'}
        </button>
      </div>

      {/* 添加区 */}
      <div className="bg-dark-card border border-border rounded-xl p-4 mb-4 flex flex-wrap gap-2 items-center">
        <div className="w-56"><StockSymbolInput value={newSymbol} onChange={(s, n) => { setNewSymbol(s); if (n) setNewName(n); }} /></div>
        <input
          value={newGroup}
          onChange={(e) => setNewGroup(e.target.value)}
          placeholder="分组"
          className="px-3 py-2 rounded-lg bg-dark-light border border-border text-white text-sm w-32"
        />
        <button onClick={addItem} disabled={!newSymbol.trim()} className="px-4 py-2 rounded-lg bg-green-600 text-white text-sm hover:bg-green-500 disabled:opacity-50">+ 加入自选</button>
      </div>

      <div className="flex gap-4 flex-col md:flex-row">
        {/* 分组 */}
        <div className="md:w-40 flex md:flex-col gap-1 flex-wrap">
          {['全部', ...groups].map((g) => (
            <button
              key={g}
              onClick={() => setActiveGroup(g)}
              className={`text-left px-3 py-2 rounded-lg text-sm transition-colors ${activeGroup === g ? 'bg-primary text-dark' : 'text-gray-400 hover:bg-dark-light'}`}
            >{g}</button>
          ))}
        </div>

        {/* 列表 */}
        <div className="flex-1 bg-dark-card border border-border rounded-xl overflow-hidden">
          <table className="w-full text-sm">
            <thead className="bg-dark-light text-gray-400 text-xs">
              <tr>
                <th className="text-left px-4 py-2.5">代码 / 名称</th>
                <th className="text-right px-3 py-2.5">现价</th>
                <th className="text-right px-3 py-2.5">涨跌幅</th>
                <th className="text-left px-3 py-2.5">分组</th>
                <th className="text-right px-4 py-2.5">操作</th>
              </tr>
            </thead>
            <tbody>
              {filtered.length === 0 && (
                <tr><td colSpan={5} className="text-center text-gray-600 py-8">暂无自选股，先在上方添加</td></tr>
              )}
              {filtered.map((it) => {
                const q = quotes[it.symbol];
                const chg = q?.change_pct;
                return (
                  <tr key={it.id} className="border-t border-border/50 hover:bg-dark-light/50">
                    <td className="px-4 py-2.5">
                      <div className="text-white">{it.name || '—'}</div>
                      <div className="text-gray-500 text-xs">{it.symbol}</div>
                    </td>
                    <td className="px-3 py-2.5 text-right text-white">{q?.price ?? '—'}</td>
                    <td className={`px-3 py-2.5 text-right ${chg == null ? 'text-gray-500' : chg >= 0 ? 'text-red-400' : 'text-green-400'}`}>
                      {chg == null ? '—' : `${chg >= 0 ? '+' : ''}${chg.toFixed(2)}%`}
                    </td>
                    <td className="px-3 py-2.5 text-gray-400 text-xs">{it.group_name}</td>
                    <td className="px-4 py-2.5 text-right space-x-2 whitespace-nowrap">
                      <button onClick={() => { setAlertModal(it); setAlertThreshold(q?.price ? String(q.price) : ''); }} className="text-accent-cyan text-xs hover:underline">设预警</button>
                      <button onClick={() => removeItem(it.id)} className="text-red-400 text-xs hover:underline">删除</button>
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      </div>

      {/* 预警列表 */}
      <div className="mt-6 bg-dark-card border border-border rounded-xl p-4">
        <div className="text-sm font-semibold text-white mb-3">🔔 价格预警（{alerts.length}）</div>
        {alerts.length === 0 ? (
          <div className="text-gray-600 text-sm">暂无预警</div>
        ) : (
          <div className="space-y-2">
            {alerts.map((a) => (
              <div key={a.alert_id} className="flex items-center justify-between text-sm bg-dark-light rounded-lg px-3 py-2">
                <div>
                  <span className="text-white">{a.name || a.symbol}</span>
                  <span className="text-gray-400 ml-2 text-xs">{ALERT_TYPE_LABEL[a.alert_type]} {a.threshold}{a.alert_type === 'pct_change' ? '%' : ''}</span>
                  {a.repeat === 1 && <span className="ml-2 text-[10px] text-accent-cyan">反复</span>}
                  <span className={`ml-2 text-[10px] px-1.5 py-0.5 rounded ${a.status === 'active' ? 'bg-green-500/20 text-green-300' : a.status === 'triggered' ? 'bg-yellow-500/20 text-yellow-300' : 'bg-gray-500/20 text-gray-400'}`}>
                    {a.status === 'active' ? '监控中' : a.status === 'triggered' ? '已触发' : '已暂停'}
                  </span>
                </div>
                <div className="space-x-3">
                  {a.status !== 'triggered' && (
                    <button onClick={() => toggleAlert(a)} className="text-xs text-gray-400 hover:text-white">{a.status === 'active' ? '暂停' : '恢复'}</button>
                  )}
                  <button onClick={() => delAlert(a)} className="text-xs text-red-400 hover:underline">删除</button>
                </div>
              </div>
            ))}
          </div>
        )}
      </div>

      {/* 设预警弹窗 */}
      {alertModal && (
        <div className="fixed inset-0 bg-black/60 z-50 flex items-center justify-center p-4" onClick={() => setAlertModal(null)}>
          <div className="bg-dark-card border border-border rounded-xl p-5 w-full max-w-sm" onClick={(e) => e.stopPropagation()}>
            <div className="text-white font-semibold mb-3">为 {alertModal.name || alertModal.symbol} 设置预警</div>
            <label className="block text-xs text-gray-400 mb-1">类型</label>
            <select value={alertType} onChange={(e) => setAlertType(e.target.value)} className="w-full mb-3 px-3 py-2 rounded-lg bg-dark-light border border-border text-white text-sm">
              <option value="price_above">价格突破（≥）</option>
              <option value="price_below">价格跌破（≤）</option>
              <option value="pct_change">当日涨跌幅达到（%）</option>
            </select>
            <label className="block text-xs text-gray-400 mb-1">阈值{alertType === 'pct_change' ? '（%，正负皆可）' : '（价格）'}</label>
            <input type="number" value={alertThreshold} onChange={(e) => setAlertThreshold(e.target.value)} className="w-full mb-3 px-3 py-2 rounded-lg bg-dark-light border border-border text-white text-sm" />
            <label className="flex items-center gap-2 text-sm text-gray-300 mb-4">
              <input type="checkbox" checked={alertRepeat} onChange={(e) => setAlertRepeat(e.target.checked)} /> 反复触发（默认仅一次）
            </label>
            <div className="flex justify-end gap-2">
              <button onClick={() => setAlertModal(null)} className="px-3 py-1.5 rounded-lg text-gray-400 text-sm hover:bg-dark-light">取消</button>
              <button onClick={submitAlert} disabled={!alertThreshold} className="px-4 py-1.5 rounded-lg bg-primary text-dark text-sm disabled:opacity-50">创建</button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
};

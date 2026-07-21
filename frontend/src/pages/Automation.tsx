/**
 * 加密半自动交易中心 —— 需求3。
 *
 * 引擎自动产决策 + 自动排待确认单，每笔仍由 Jason 逐笔确认才成交（逐笔确认红线保留）。
 * 两个 Tab：半自动策略 | 待确认单。顶栏：引擎启停 + kill 总开关。
 * （原 A 股自动交易 UI 已随券商退役整块删除。）
 */
import React, { useState, useEffect } from 'react';
import { useNavigate } from 'react-router-dom';
import { useCryptoStrategyStore } from '../stores/cryptoStrategyStore';
import { CryptoStrategyTab } from '../components/trading-center/CryptoStrategyTab';
import { CryptoPendingTab } from '../components/trading-center/CryptoPendingTab';

const tabs = [
  { key: 'strategy', label: '半自动策略' },
  { key: 'pending', label: '待确认单' },
] as const;
type TabKey = typeof tabs[number]['key'];

export const Automation: React.FC = () => {
  const [activeTab, setActiveTab] = useState<TabKey>('strategy');
  const store = useCryptoStrategyStore();
  const nav = useNavigate();

  useEffect(() => {
    store.refreshAll();
    store.connectWebSocket();
    return () => store.disconnectWebSocket();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  useEffect(() => {
    const t = setInterval(() => { store.fetchPending(); store.fetchEngine(); }, 20000);
    return () => clearInterval(t);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const eng = store.engine;
  const running = !!eng?.is_running;
  const killed = !!eng?.killed;
  const pendingCount = store.pending.length;

  return (
    <div className="h-screen flex flex-col bg-dark">
      {/* ===== 顶栏 ===== */}
      <div className="flex-shrink-0 bg-dark-card border-b border-border">
        <div className="px-4 md:px-6 py-3 flex items-center justify-between flex-wrap gap-2">
          <div className="flex items-center gap-3">
            <span onClick={() => nav('/')} title="返回主页"
              className="text-xl cursor-pointer hover:scale-110 transition-transform">💰</span>
            <h1 className="text-xl font-bold text-white">加密自动化</h1>
            {pendingCount > 0 && (
              <span className="bg-red-500 text-white text-xs px-2 py-0.5 rounded-full animate-pulse">
                {pendingCount} 待确认
              </span>
            )}
          </div>

          <div className="flex items-center gap-3">
            {/* 引擎状态 */}
            <div className="flex items-center gap-2">
              <div className={`w-2.5 h-2.5 rounded-full ${
                killed ? 'bg-red-600' : running ? 'bg-green-500 animate-pulse' : 'bg-gray-500'
              }`} />
              <span className="text-sm text-gray-400 hidden sm:inline">
                {killed ? '已急停' : running ? `引擎运行中(每${eng?.tick_min}分)` : '引擎已停'}
              </span>
            </div>

            {/* 启停 */}
            <button
              onClick={() => running ? store.engineStop() : store.engineStart()}
              className={`px-3 py-1.5 rounded-lg text-sm font-medium transition-all ${
                running ? 'bg-red-500/20 text-red-400 hover:bg-red-500/30'
                        : 'bg-green-500/20 text-green-400 hover:bg-green-500/30'
              }`}>
              {running ? '停止引擎' : '启动引擎'}
            </button>

            {/* Kill 总开关 */}
            <button
              onClick={() => store.toggleKill()}
              title="总开关：急停后任何策略都不再产单"
              className={`px-3 py-1.5 rounded-lg text-sm font-medium transition-all ${
                killed ? 'bg-yellow-500/20 text-yellow-400 hover:bg-yellow-500/30'
                       : 'bg-dark-light text-gray-400 hover:text-red-400 border border-border'
              }`}>
              {killed ? '解除急停' : '🛑 急停'}
            </button>

            {store.wsConnected && (
              <div className="flex items-center gap-1">
                <div className="w-1.5 h-1.5 rounded-full bg-green-500" />
                <span className="text-xs text-gray-500">WS</span>
              </div>
            )}
          </div>
        </div>
      </div>

      {/* ===== Tab 栏 ===== */}
      <div className="flex-shrink-0 bg-dark-card/30 border-b border-border/50">
        <div className="px-4 md:px-6 flex gap-1 overflow-x-auto">
          {tabs.map((tab) => {
            const isActive = activeTab === tab.key;
            const hasBadge = tab.key === 'pending' && pendingCount > 0;
            return (
              <button key={tab.key} onClick={() => setActiveTab(tab.key)}
                className={`relative px-4 py-2.5 text-sm font-medium whitespace-nowrap transition-all border-b-2 ${
                  isActive ? 'text-primary border-primary'
                           : 'text-gray-500 border-transparent hover:text-gray-300'
                }`}>
                {tab.label}
                {hasBadge && (
                  <span className="absolute -top-0.5 -right-0.5 w-2 h-2 rounded-full bg-red-500 animate-pulse" />
                )}
              </button>
            );
          })}
        </div>
      </div>

      {/* ===== 内容区 ===== */}
      <div className="flex-1 overflow-auto p-3 md:p-6 pb-20 md:pb-6">
        {activeTab === 'strategy' && <CryptoStrategyTab />}
        {activeTab === 'pending' && <CryptoPendingTab />}
      </div>
    </div>
  );
};

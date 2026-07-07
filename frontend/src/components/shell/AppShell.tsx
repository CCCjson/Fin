import React, { useEffect, useState } from 'react';
import { Sidebar } from './Sidebar';
import { MainStage } from './MainStage';
import { ToolsDrawer } from './ToolsDrawer';
import { TokenMonitor } from './TokenMonitor';
import { MonitorPanel } from './MonitorPanel';
import { FloatingChat } from '../moneybill/FloatingChat';
import { Toaster, toast } from '../common/Toast';
import { useChatStore } from '../../store/agentChatStore';
import wsService from '../../services/websocketService';

/* ================================================================
   统一 ChatGPT 外壳：左侧栏 + 主区（聊天/工具页 keep-alive）
   全局价格预警监听（登录门已移除，直接进入）
   ================================================================ */
export const AppShell: React.FC = () => {
  const [toolsOpen, setToolsOpen] = useState(false);

  useEffect(() => {
    useChatStore.getState().startFresh();
  }, []);

  // 全局价格预警：连 WS，收到 price_alert 弹 RiskToast（沿用原 Layout 逻辑）
  useEffect(() => {
    wsService.connect();
    const off = wsService.on('price_alert', (d: any) => {
      const base = d?.message || `${d?.name || d?.symbol} 触发预警`;
      const s = d?.suggested;
      const tip = s
        ? s.affordable
          ? `　💰按你的资金建议买 ${s.shares} 股(约¥${Number(s.amount).toLocaleString()})`
          : '　💰以你的资金买不起 1 手'
        : '';
      toast.show({
        kind: 'warning',
        title: d?.rule || '到价预警',
        message: base + tip,
      });
    });
    return off;
  }, []);

  return (
    <div className="relative flex h-screen w-screen overflow-hidden border-t border-border bg-dark text-gray-200">
      {/* 赛博霓虹网格底纹（纯装饰，不拦截事件） */}
      <div className="cyber-grid pointer-events-none absolute inset-0 z-0" />
      {/* 内容层置于网格之上 */}
      <div className="relative z-10 flex flex-1 min-w-0">
        <Sidebar onOpenTools={() => setToolsOpen(true)} />
        <MainStage />
      </div>
      <ToolsDrawer open={toolsOpen} onClose={() => setToolsOpen(false)} />
      <TokenMonitor />
      <MonitorPanel />
      <FloatingChat />
      <Toaster />
    </div>
  );
};

export default AppShell;

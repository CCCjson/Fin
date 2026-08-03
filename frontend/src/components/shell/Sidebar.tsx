import React, { useState } from 'react';
import { useNavigate, useLocation } from 'react-router-dom';
import { useChatStore } from '../../store/agentChatStore';
import { Button } from '../common/Button';

export const Sidebar: React.FC<{ onOpenTools: () => void }> = ({ onOpenTools }) => {
  const navigate = useNavigate();
  const location = useLocation();
  const sessions = useChatStore((s) => s.sessions);
  const activeId = useChatStore((s) => s.activeId);
  const [collapsed, setCollapsed] = useState(false);

  const onChat = location.pathname === '/' || location.pathname === '/app';
  const onTerminal = location.pathname === '/trading';

  const newChat = () => {
    useChatStore.getState().newSession();
    navigate('/');
  };
  const openSession = (id: string) => {
    useChatStore.getState().switchSession(id);
    navigate('/');
  };
  const del = (e: React.MouseEvent, id: string) => {
    e.stopPropagation();
    useChatStore.getState().deleteSession(id);
  };

  if (collapsed) {
    return (
      <div data-sidebar className="w-14 shrink-0 bg-dark-lighter border-r border-border flex flex-col items-center py-3 gap-3">
        <button onClick={() => setCollapsed(false)} title="展开" className="text-gray-400 hover:text-white text-xl">›</button>
        <button onClick={newChat} title="新对话" className="text-gray-300 hover:text-white text-xl">＋</button>
        {/* 交易终端：裁决 17 说它是「仅次于 MoneyBill 的第二块屏」，
            所以收起态也给它留位（其余 7 个页面仍在工具抽屉里）。 */}
        <button
          onClick={() => navigate('/trading')}
          title="交易终端"
          className={`text-xl mt-auto ${onTerminal ? 'text-primary' : 'text-gray-300 hover:text-white'}`}
        >
          🚦
        </button>
        <button onClick={onOpenTools} title="工具" className="text-gray-300 hover:text-white text-xl mb-1">🧰</button>
      </div>
    );
  }

  return (
    <div data-sidebar className="w-64 shrink-0 bg-dark-lighter border-r border-border flex flex-col">
      {/* 头部 */}
      <div className="flex items-center justify-between px-3 py-3">
        <div className="flex items-center gap-2">
          <span className="text-xl">💰</span>
          <span className="font-bold neon-text tracking-wide">MoneyBill</span>
        </div>
        <button onClick={() => setCollapsed(true)} title="收起" className="text-gray-500 hover:text-white text-lg">‹</button>
      </div>

      {/* 新对话 */}
      <div className="px-3">
        <Button onClick={newChat} size="lg" className="w-full">
          ＋ 新对话
        </Button>
      </div>

      {/* 会话历史 */}
      <div className="flex-1 overflow-y-auto px-2 py-3 space-y-1">
        <div className="px-2 text-xs text-gray-600 uppercase tracking-wider mb-1">对话历史</div>
        {sessions.length === 0 && <div className="px-2 text-xs text-gray-600">还没有对话</div>}
        {sessions.map((s) => (
          <div
            key={s.id}
            onClick={() => openSession(s.id)}
            className={`group flex items-center gap-2 rounded-lg px-2 py-2 text-sm cursor-pointer transition-colors ${
              onChat && s.id === activeId ? 'bg-primary/15 text-white' : 'text-gray-400 hover:bg-dark-light'
            }`}
          >
            <span className="text-xs">💬</span>
            <span className="flex-1 truncate">{s.title || '新对话'}</span>
            <button
              onClick={(e) => del(e, s.id)}
              className="opacity-0 group-hover:opacity-100 text-gray-600 hover:text-bear text-xs transition-opacity"
              title="删除"
            >
              ✕
            </button>
          </div>
        ))}
      </div>

      {/* 交易终端 + 工具入口 */}
      <div className="px-3 py-3 border-t border-border space-y-2">
        {/* ⭐ 交易终端提到一级（Jason 2026-08-03）：裁决 17 说它是
            「仅次于 MoneyBill 的第二块屏」，藏在工具抽屉里跟这个定位不符 ——
            他第一次找就没找到。⛔ 其余 7 个页面仍留在抽屉里，别破坏架构收敛。 */}
        <button
          onClick={() => navigate('/trading')}
          className={`w-full flex items-center gap-2 rounded-xl px-3 py-2.5 text-sm transition-colors border ${
            onTerminal
              ? 'bg-primary/15 border-primary/50 text-white'
              : 'bg-dark-light hover:bg-dark border-border hover:border-primary/50 text-gray-200'
          }`}
        >
          <span className="text-base">🚦</span> 交易终端
        </button>
        <button
          onClick={onOpenTools}
          className="w-full flex items-center gap-2 bg-dark-light hover:bg-dark border border-border hover:border-primary/50 text-gray-200 rounded-xl px-3 py-2.5 text-sm transition-colors"
        >
          <span className="text-base">🧰</span> 工具
        </button>
      </div>
    </div>
  );
};

export default Sidebar;

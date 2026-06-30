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
      <div className="w-14 shrink-0 bg-dark-lighter border-r border-border flex flex-col items-center py-3 gap-3">
        <button onClick={() => setCollapsed(false)} title="展开" className="text-gray-400 hover:text-white text-xl">›</button>
        <button onClick={newChat} title="新对话" className="text-gray-300 hover:text-white text-xl">＋</button>
        <button onClick={onOpenTools} title="工具" className="text-gray-300 hover:text-white text-xl mt-auto mb-1">🧰</button>
      </div>
    );
  }

  return (
    <div className="w-64 shrink-0 bg-dark-lighter border-r border-border flex flex-col">
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

      {/* 工具入口 */}
      <div className="px-3 py-3 border-t border-border">
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

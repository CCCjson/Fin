import React from 'react';
import { useNavigate } from 'react-router-dom';

interface ToolItem {
  path: string;
  label: string;
  icon: string;
}

const GROUPS: { title: string; items: ToolItem[] }[] = [
  {
    title: 'A 股',
    items: [
      { path: '/app/cockpit', label: '决策驾驶舱', icon: '🎛️' },
      { path: '/app/watchlist', label: '自选股', icon: '⭐' },
      { path: '/app/screener', label: '选股器', icon: '🔎' },
      { path: '/app/realtime', label: '实时行情', icon: '⚡' },
      { path: '/app/market', label: 'K线分析', icon: '📈' },
      { path: '/app/signals', label: '信号分析', icon: '🎯' },
      { path: '/app/tracking', label: '信号追踪', icon: '📋' },
      { path: '/app/pipeline', label: '数据管道', icon: '🚀' },
      { path: '/app/prediction', label: '股价预测', icon: '🔮' },
    ],
  },
  {
    title: '通用工具',
    items: [
      { path: '/app/dashboard', label: '账户概览', icon: '🏠' },
      { path: '/app/backtest', label: '策略回测', icon: '🔬' },
      { path: '/app/portfolio', label: '交易记录', icon: '📒' },
      { path: '/app/review', label: '每日复盘', icon: '📝' },
      { path: '/app/reports', label: 'AI 报告', icon: '🤖' },
      { path: '/app/advisor', label: 'AI 顾问', icon: '💬' },
      { path: '/app/news', label: '新闻分析', icon: '📰' },
      { path: '/app/orderbook', label: '订单簿', icon: '📊' },
      { path: '/app/knowledge', label: '知识库', icon: '📚' },
    ],
  },
  {
    title: '高级',
    items: [
      { path: '/trading', label: '自动化交易', icon: '🚦' },
      { path: '/alpha-lab', label: 'Alpha Lab', icon: '🧬' },
      { path: '/fine-tune', label: '模型微调', icon: '🧪' },
    ],
  },
];

export const ToolsDrawer: React.FC<{ open: boolean; onClose: () => void }> = ({ open, onClose }) => {
  const navigate = useNavigate();
  if (!open) return null;

  const pick = (path: string) => {
    navigate(path);
    onClose();
  };

  return (
    <div className="fixed inset-0 z-50 flex" onClick={onClose}>
      <div className="absolute inset-0 bg-black/60 backdrop-blur-sm" />
      <div
        className="relative m-auto w-full max-w-3xl max-h-[80vh] overflow-y-auto bg-dark-light border border-border rounded-2xl shadow-card p-6"
        onClick={(e) => e.stopPropagation()}
      >
        <div className="flex items-center justify-between mb-5">
          <h2 className="text-lg font-bold text-white flex items-center gap-2">🧰 工具</h2>
          <button onClick={onClose} className="text-gray-500 hover:text-white text-xl leading-none">✕</button>
        </div>

        {GROUPS.map((g) => (
          <div key={g.title} className="mb-6 last:mb-0">
            <div className="text-xs font-semibold text-gray-500 uppercase tracking-wider mb-3">{g.title}</div>
            <div className="grid grid-cols-2 sm:grid-cols-3 md:grid-cols-4 gap-3">
              {g.items.map((it) => (
                <button
                  key={it.path}
                  onClick={() => pick(it.path)}
                  className="flex flex-col items-center gap-2 bg-dark hover:bg-dark-lighter border border-border hover:border-primary/50 rounded-xl px-3 py-4 transition-colors group"
                >
                  <span className="text-2xl group-hover:scale-110 transition-transform">{it.icon}</span>
                  <span className="text-xs text-gray-300">{it.label}</span>
                </button>
              ))}
            </div>
          </div>
        ))}
      </div>
    </div>
  );
};

export default ToolsDrawer;

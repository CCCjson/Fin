import React, { useEffect } from 'react';
import { create } from 'zustand';

// ==================== Types ====================

export interface RiskToastItem {
  id: string;
  rule: string;
  message: string;
  severity: 'WARNING' | 'ERROR';
  timestamp: number;
}

interface RiskToastStore {
  toasts: RiskToastItem[];
  addToast: (toast: Omit<RiskToastItem, 'id' | 'timestamp'>) => void;
  removeToast: (id: string) => void;
  clearAll: () => void;
}

// ==================== Store ====================

let _toastId = 0;

export const useRiskToastStore = create<RiskToastStore>((set) => ({
  toasts: [],
  addToast: (toast) => {
    const id = `risk-toast-${++_toastId}`;
    set((s) => ({
      toasts: [...s.toasts, { ...toast, id, timestamp: Date.now() }],
    }));
    // 自动消失
    setTimeout(() => {
      set((s) => ({ toasts: s.toasts.filter((t) => t.id !== id) }));
    }, 6000);
  },
  removeToast: (id) => set((s) => ({ toasts: s.toasts.filter((t) => t.id !== id) })),
  clearAll: () => set({ toasts: [] }),
}));

// ==================== 单个 Toast ====================

const Toast: React.FC<{ item: RiskToastItem; onClose: () => void }> = ({ item, onClose }) => {
  const isError = item.severity === 'ERROR';

  return (
    <div
      className={`
        flex items-start gap-3 px-4 py-3 rounded-lg shadow-lg border backdrop-blur-sm
        animate-slide-in-right min-w-[300px] max-w-[420px]
        ${isError
          ? 'bg-red-900/80 border-red-600/50 text-red-100'
          : 'bg-yellow-900/80 border-yellow-600/50 text-yellow-100'
        }
      `}
    >
      {/* 图标 */}
      <span className="text-lg flex-shrink-0 mt-0.5">
        {isError ? '🚨' : '⚠️'}
      </span>

      {/* 内容 */}
      <div className="flex-1 min-w-0">
        <div className={`text-xs font-semibold mb-0.5 ${isError ? 'text-red-300' : 'text-yellow-300'}`}>
          {item.rule}
        </div>
        <div className="text-sm leading-snug break-words">
          {item.message}
        </div>
      </div>

      {/* 关闭按钮 */}
      <button
        onClick={onClose}
        className={`flex-shrink-0 mt-0.5 p-0.5 rounded hover:bg-white/10 transition-colors ${
          isError ? 'text-red-300' : 'text-yellow-300'
        }`}
      >
        <svg className="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
          <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M6 18L18 6M6 6l12 12" />
        </svg>
      </button>
    </div>
  );
};

// ==================== Container ====================

export const RiskToastContainer: React.FC = () => {
  const { toasts, removeToast } = useRiskToastStore();

  if (toasts.length === 0) return null;

  return (
    <div className="fixed top-4 right-4 z-[9999] flex flex-col gap-2 pointer-events-auto">
      {toasts.map((t) => (
        <Toast key={t.id} item={t} onClose={() => removeToast(t.id)} />
      ))}
    </div>
  );
};

import React from 'react';
import { create } from 'zustand';
import { AnimatePresence, motion, useReducedMotion } from 'framer-motion';
import { toastVariants, fadeOnly } from './motion';

/* ================================================================
   统一 Toast 系统 —— 全局唯一的轻提示
   - zustand store + <Toaster> 容器（挂在 AppShell 一次）
   - framer-motion 进出动画，右上角堆叠，自动消失
   - 替代散落的 alert() / RiskToast / NotificationBanner
   用法：toast.success('已保存')  toast.error('提交失败')
        toast.show({ kind:'warning', title:'到价预警', message:'...' })
   ================================================================ */

export type ToastKind = 'success' | 'error' | 'info' | 'warning';

export interface ToastItem {
  id: string;
  kind: ToastKind;
  title?: string;
  message: string;
  duration: number; // ms，0 = 不自动消失
}

interface ToastInput {
  kind?: ToastKind;
  title?: string;
  message: string;
  duration?: number;
}

interface ToastStore {
  toasts: ToastItem[];
  push: (t: ToastInput) => string;
  dismiss: (id: string) => void;
  clear: () => void;
}

let _id = 0;

export const useToastStore = create<ToastStore>((set) => ({
  toasts: [],
  push: (t) => {
    const id = `toast-${++_id}`;
    const duration = t.duration ?? (t.kind === 'error' ? 6000 : 4000);
    const item: ToastItem = {
      id,
      kind: t.kind ?? 'info',
      title: t.title,
      message: t.message,
      duration,
    };
    set((s) => ({ toasts: [...s.toasts, item].slice(-5) }));
    if (duration > 0) {
      setTimeout(() => {
        set((s) => ({ toasts: s.toasts.filter((x) => x.id !== id) }));
      }, duration);
    }
    return id;
  },
  dismiss: (id) => set((s) => ({ toasts: s.toasts.filter((x) => x.id !== id) })),
  clear: () => set({ toasts: [] }),
}));

/** 命令式 API —— 任何地方都能 import { toast } 调用 */
export const toast = {
  show: (t: ToastInput) => useToastStore.getState().push(t),
  success: (message: string, title?: string) =>
    useToastStore.getState().push({ kind: 'success', message, title }),
  error: (message: string, title?: string) =>
    useToastStore.getState().push({ kind: 'error', message, title }),
  info: (message: string, title?: string) =>
    useToastStore.getState().push({ kind: 'info', message, title }),
  warning: (message: string, title?: string) =>
    useToastStore.getState().push({ kind: 'warning', message, title }),
  dismiss: (id: string) => useToastStore.getState().dismiss(id),
};

// 统一落在黑底卡面上，靠霓虹色边框 + 强调色区分；与主题 token 对齐
const KIND_STYLE: Record<ToastKind, { icon: string; cls: string; accent: string }> = {
  success: { icon: '✅', cls: 'bg-dark-card/90 border-primary/40 text-gray-100', accent: 'text-primary' },
  error: { icon: '🚨', cls: 'bg-dark-card/90 border-bull/50 text-gray-100', accent: 'text-bull-light' },
  warning: { icon: '⚠️', cls: 'bg-dark-card/90 border-accent-orange/50 text-gray-100', accent: 'text-accent-orange' },
  info: { icon: 'ℹ️', cls: 'bg-dark-card/90 border-border-light text-gray-100', accent: 'text-primary-light' },
};

const ToastCard: React.FC<{ item: ToastItem; onClose: () => void; reduce: boolean | null }> = ({
  item, onClose, reduce,
}) => {
  const s = KIND_STYLE[item.kind];
  return (
    <motion.div
      layout
      variants={reduce ? fadeOnly : toastVariants}
      initial="hidden"
      animate="show"
      exit="exit"
      className={`pointer-events-auto flex items-start gap-3 px-4 py-3 rounded-xl shadow-lg border backdrop-blur-sm min-w-[280px] max-w-[420px] ${s.cls}`}
    >
      <span className="text-lg flex-shrink-0 leading-none mt-0.5">{s.icon}</span>
      <div className="flex-1 min-w-0">
        {item.title && <div className={`text-xs font-semibold mb-0.5 ${s.accent}`}>{item.title}</div>}
        <div className="text-sm leading-snug break-words">{item.message}</div>
      </div>
      <button
        onClick={onClose}
        className="flex-shrink-0 mt-0.5 p-0.5 rounded text-gray-300/70 hover:text-white hover:bg-white/10 transition-colors"
        aria-label="关闭"
      >
        <svg className="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
          <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M6 18L18 6M6 6l12 12" />
        </svg>
      </button>
    </motion.div>
  );
};

export const Toaster: React.FC = () => {
  const toasts = useToastStore((s) => s.toasts);
  const dismiss = useToastStore((s) => s.dismiss);
  const reduce = useReducedMotion();

  return (
    <div className="fixed top-4 right-4 z-[9999] flex flex-col gap-2 pointer-events-none">
      <AnimatePresence initial={false}>
        {toasts.map((t) => (
          <ToastCard key={t.id} item={t} reduce={reduce} onClose={() => dismiss(t.id)} />
        ))}
      </AnimatePresence>
    </div>
  );
};

export default Toaster;

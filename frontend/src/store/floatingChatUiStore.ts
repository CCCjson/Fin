import { create } from 'zustand';

/* ================================================================
   浮窗 MoneyBill 的 UI 状态（open / mode）提升到全局 store。
   目的：agent 驱动导航（在完整对话页 ChatThread 里发生）后，
   能远程弹开右下角浮窗并强制切到「共享会话」，让同一段对话在缩小的
   MoneyBill 里无缝续接。mode 仍持久化到 localStorage。
   ================================================================ */

type Mode = 'shared' | 'independent';
const MODE_KEY = 'moneybill-float-mode';

function loadMode(): Mode {
  try {
    return typeof localStorage !== 'undefined' && localStorage.getItem(MODE_KEY) === 'independent'
      ? 'independent'
      : 'shared';
  } catch {
    return 'shared';
  }
}

interface FloatingChatUiState {
  open: boolean;
  mode: Mode;
  /** agent 驱动导航后调用：弹开浮窗并强制共享会话（续接完整页的对话）。*/
  requestOpen: () => void;
  setOpen: (v: boolean) => void;
  toggleMode: () => void;
}

export const useFloatingChatUiStore = create<FloatingChatUiState>((set) => ({
  open: false,
  mode: loadMode(),
  requestOpen: () => set({ open: true, mode: 'shared' }),
  setOpen: (v) => set({ open: v }),
  toggleMode: () =>
    set((s) => {
      const next: Mode = s.mode === 'shared' ? 'independent' : 'shared';
      try { localStorage.setItem(MODE_KEY, next); } catch { /* ignore */ }
      return { mode: next };
    }),
}));

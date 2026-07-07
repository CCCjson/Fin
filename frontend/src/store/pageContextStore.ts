import { create } from 'zustand';

/* ================================================================
   页面上下文 store（非持久化）
   关键页面在选中股票时写入 entities（如 { symbol }），
   capturePageContext() 采集时带上，让 MoneyBill 理解「这只股票/当前」的指代。
   记录写入时的 path，路由切走后自动失效，避免跨页残留。
   ================================================================ */

interface PageContextState {
  path: string | null;
  entities: Record<string, any>;
  /** 记录当前页面的关键对象（会覆盖，并绑定到 pathname）。
   *  可选传 path：agent 驱动导航时目标页尚未生效，需显式绑定到目标路径。*/
  setEntities: (entities: Record<string, any>, path?: string) => void;
  clear: () => void;
}

export const usePageContextStore = create<PageContextState>((set) => ({
  path: null,
  entities: {},
  setEntities: (entities, path) =>
    set({ path: path ?? (typeof window !== 'undefined' ? window.location.pathname : null), entities }),
  clear: () => set({ path: null, entities: {} }),
}));

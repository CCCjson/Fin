import { create } from 'zustand';
import wsService from '../services/websocketService';
import { toast } from '../components/common/Toast';
import {
  cryptoStrategyService as svc,
  type CryptoStrategyItem,
  type CryptoPendingItem,
  type CryptoEngineStatus,
} from '../services/cryptoStrategyService';

/** crypto 半自动策略中心 store —— 策略/待确认/引擎 + WS 推送（复用 /ws/automation 业务事件桥）。 */

interface CryptoStrategyState {
  strategies: CryptoStrategyItem[];
  pending: CryptoPendingItem[];
  engine: CryptoEngineStatus | null;
  wsConnected: boolean;
  loading: boolean;
  _unsubs: Array<() => void>;

  fetchStrategies: () => Promise<void>;
  fetchPending: () => Promise<void>;
  fetchEngine: () => Promise<void>;
  refreshAll: () => Promise<void>;

  arm: (id: string) => Promise<void>;
  enablePaper: (id: string) => Promise<void>;
  pause: (id: string) => Promise<void>;
  retire: (id: string) => Promise<void>;
  backtest: (id: string) => Promise<void>;

  confirmPending: (ref: string) => Promise<void>;
  rejectPending: (ref: string) => Promise<void>;

  engineStart: () => Promise<void>;
  engineStop: () => Promise<void>;
  toggleKill: () => Promise<void>;

  connectWebSocket: () => void;
  disconnectWebSocket: () => void;
}

// 统一处理「动作 → 报错 toast → 刷新」的样板。
// okMsg 支持传函数：需要按**返回结果**决定文案时用（比如「真成交了」还是「提交了但没吃到量」）。
async function act(
  fn: () => Promise<any>,
  onDone: () => Promise<void>,
  okMsg?: string | ((result: any) => string),
) {
  try {
    const result = await fn();
    const msg = typeof okMsg === 'function' ? okMsg(result) : okMsg;
    if (msg) toast.success(msg);
    await onDone();
  } catch (e: any) {
    toast.error(e?.response?.data?.detail || e?.message || '操作失败');
    await onDone();
  }
}

export const useCryptoStrategyStore = create<CryptoStrategyState>((set, get) => ({
  strategies: [],
  pending: [],
  engine: null,
  wsConnected: false,
  loading: false,
  _unsubs: [],

  fetchStrategies: async () => {
    try {
      const r = await svc.listStrategies();
      set({ strategies: r.strategies || [] });
    } catch { /* 静默：顶部会有 WS/加载态 */ }
  },

  fetchPending: async () => {
    try {
      // 带上 STALE：成交与否未知的单必须让 Jason 看得见（否则只在后台挡重排，人不知情）
      const r = await svc.listPending('PENDING,STALE');
      set({ pending: r.pending || [] });
    } catch { /* 静默 */ }
  },

  fetchEngine: async () => {
    try {
      set({ engine: await svc.engineStatus() });
    } catch { /* 静默 */ }
  },

  refreshAll: async () => {
    set({ loading: true });
    await Promise.all([get().fetchStrategies(), get().fetchPending(), get().fetchEngine()]);
    set({ loading: false });
  },

  arm: (id) => act(() => svc.arm(id), () => get().fetchStrategies(), '已武装上实盘（引擎将排单等你确认）'),
  enablePaper: (id) => act(() => svc.enablePaper(id), () => get().fetchStrategies(), '已启用纸面干跑'),
  pause: (id) => act(() => svc.pause(id), () => get().fetchStrategies(), '已暂停'),
  retire: (id) => act(() => svc.retire(id), () => get().fetchStrategies(), '已退役'),
  backtest: (id) => act(() => svc.backtest(id), () => get().fetchStrategies(), '回测完成'),

  // ⛔ 别无条件说「已确认成交」：后端可能返回 UNFILLED（已受理但零成交）或部分成交。
  // 谎报成交会让 Jason 以为仓位已建好，而交易所侧一分钱没动。文案一律看 fill_quantity。
  confirmPending: (ref) => act(() => svc.confirmPending(ref),
    async () => { await get().fetchPending(); },
    (r: any) => {
      const qty = Number(r?.fill_quantity || 0);
      if (r?.status === 'UNFILLED' || qty <= 0) {
        return '订单已提交，但未成交（没吃到量）—— 未计入台账，请去币安核对';
      }
      const ordered = Number(r?.quantity || 0);
      const partial = ordered > 0 && qty < ordered;
      return partial
        ? `部分成交 ${qty} / 委托 ${ordered}，余量未成交`
        : `已成交 ${qty}`;
    }),
  rejectPending: (ref) => act(() => svc.rejectPending(ref),
    async () => { await get().fetchPending(); }),

  engineStart: () => act(() => svc.engineStart(), () => get().fetchEngine()),
  engineStop: () => act(() => svc.engineStop(), () => get().fetchEngine()),
  toggleKill: () => act(
    async () => { const e = get().engine; return e?.killed ? svc.engineUnkill() : svc.engineKill(); },
    () => get().fetchEngine()),

  connectWebSocket: () => {
    wsService.connect();
    const unsubs: Array<() => void> = [];
    unsubs.push(wsService.on('connection', () => set({ wsConnected: true })));
    unsubs.push(wsService.on('business_event', (ev: any) => {
      if (ev?.type === 'crypto_order_pending') {
        toast.info(ev.title || '新的待确认单');
        get().fetchPending();
      } else if (ev?.source === 'crypto_strategy' || ev?.source === 'crypto') {
        if (ev?.severity === 'alert') toast.error(ev.title || 'crypto 风险告警');
        get().fetchPending();
        get().fetchStrategies();
      }
    }));
    set({ _unsubs: unsubs, wsConnected: true });
  },

  disconnectWebSocket: () => {
    get()._unsubs.forEach((u) => u());
    set({ _unsubs: [], wsConnected: false });
  },
}));

export default useCryptoStrategyStore;

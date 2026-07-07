import { create } from 'zustand';

/* ================================================================
   MoneyBill 工具调用流程监控 store（全局单例、不持久化）
   ChatThread / FloatingChat 两个 useAgentChat 实例都往这里写，
   悬浮监控面板（MonitorPanel）订阅展示——不管流从哪个入口发起都能看到。
   ================================================================ */

export interface MonitorCall {
  id: string;
  name: string;
  running: boolean;
  ok?: boolean;
  /** 后端质量判定：ok | empty | error | duplicate | meta */
  verdict?: string;
  elapsedMs?: number;
}

export interface MonitorIntervention {
  action: string;
  round?: number;
  text: string;
}

export interface TurnSummary {
  rounds: number;
  calls: number;
  verdicts: Record<string, number>;
  elapsedMsTotal: number;
  tokens: { prompt: number; completion: number };
  interventions: any[];
}

interface MonitorState {
  streaming: boolean;
  rounds: number;
  calls: MonitorCall[];
  interventions: MonitorIntervention[];
  lastSummary: TurnSummary | null;

  beginTurn: () => void;
  setStreaming: (v: boolean) => void;
  bumpRound: () => void;
  callStart: (id: string, name: string) => void;
  callEnd: (id: string, patch: Partial<MonitorCall>) => void;
  pushIntervention: (iv: MonitorIntervention) => void;
  endTurn: (summary: TurnSummary) => void;
}

export const useMonitorStore = create<MonitorState>((set) => ({
  streaming: false,
  rounds: 0,
  calls: [],
  interventions: [],
  lastSummary: null,

  // 新用户消息 = 新 turn，清空实时轨迹（确认续跑不调这个，轨迹接着累计）
  beginTurn: () => set({ rounds: 0, calls: [], interventions: [] }),
  setStreaming: (v) => set({ streaming: v }),
  bumpRound: () => set((s) => ({ rounds: s.rounds + 1 })),
  callStart: (id, name) =>
    set((s) => ({ calls: [...s.calls, { id, name, running: true }] })),
  callEnd: (id, patch) =>
    set((s) => ({
      calls: s.calls.map((c) => (c.id === id && c.running ? { ...c, ...patch, running: false } : c)),
    })),
  pushIntervention: (iv) => set((s) => ({ interventions: [...s.interventions, iv] })),
  endTurn: (summary) => set({ lastSummary: summary }),
}));

/** 干预动作 → 面板展示文案 */
export function interventionText(action: string, detail?: any): string {
  switch (action) {
    case 'duplicate_block':
      return '拦截了一次重复调用';
    case 'bad_streak_nudge':
      return `连续 ${detail?.streak ?? '多'} 次无效调用，已提示 AI 纠偏`;
    case 'soft_budget':
      return `token 超软预算（${Math.round((detail?.turn_tokens ?? 0) / 1000)}k），已提示 AI 收敛`;
    case 'midturn_compact':
      return `已压缩 ${detail?.compacted_msgs ?? ''} 条早期工具结果省 token`;
    case 'hard_budget_stop':
      return 'token 超硬预算，已熔断强制收尾';
    default:
      return action;
  }
}

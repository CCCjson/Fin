import { create } from 'zustand';
import type { UsageSnapshot, UsageToday } from '../services/agentService';

interface UsageState {
  totalTokens: number;
  promptTokens: number;
  completionTokens: number;
  costUsd: number;
  calls: number;
  byModel: Record<string, any>;
  today: UsageToday | null; // 当天整体用量（后端按日期分桶、跨重启累计）
  liveTokens: number; // 流式中按字符实时估算的增量（到 usage 事件时清零校准）
  setSnapshot: (s: UsageSnapshot) => void;
  addLiveTokens: (n: number) => void;
}

export const useUsageStore = create<UsageState>((set) => ({
  totalTokens: 0,
  promptTokens: 0,
  completionTokens: 0,
  costUsd: 0,
  calls: 0,
  byModel: {},
  today: null,
  liveTokens: 0,
  setSnapshot: (s) =>
    set({
      totalTokens: s.total_tokens ?? 0,
      promptTokens: s.prompt_tokens ?? 0,
      completionTokens: s.completion_tokens ?? 0,
      costUsd: s.cost_usd ?? 0,
      calls: s.calls ?? 0,
      byModel: s.by_model ?? {},
      today: s.today ?? null,
      liveTokens: 0, // 服务端真实值到达，清掉本地估算
    }),
  addLiveTokens: (n) => set((st) => ({ liveTokens: st.liveTokens + n })),
}));

/** 粗略估算一段文本的 token 数（中文≈1/字，其它≈1/4 字符）。仅用于流式实时跳动，最终以服务端为准。 */
export function estimateTokens(s: string): number {
  let cjk = 0;
  let other = 0;
  for (const ch of s) {
    if (/[㐀-鿿豈-﫿　-〿＀-￯]/.test(ch)) cjk++;
    else other++;
  }
  return Math.ceil(cjk + other / 4);
}

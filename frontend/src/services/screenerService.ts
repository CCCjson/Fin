import api from './api';
import { authFetch } from '../utils/authFetch';

const API_BASE = import.meta.env.VITE_API_URL || '/api';

// ==================== 类型 ====================

export interface ScreenerField {
  field: string;
  label: string;
  source: 'financial' | 'valuation';
  unit: string;
}

export interface ScreenFilter {
  field: string;
  op: 'gt' | 'gte' | 'lt' | 'lte' | 'eq' | 'between';
  value: number | number[];
}

export interface ScreenRequest {
  filters: ScreenFilter[];
  pool_id?: string;
  sort_by?: string;
  sort_desc?: boolean;
  limit?: number;
  affordable_only?: boolean;
  exclude_st?: boolean;
  main_board_only?: boolean;
}

export interface ScreenRow {
  symbol: string;
  name: string | null;
  price?: number | null;
  suggested?: {
    shares: number;
    lots: number;
    amount: number;
    affordable: boolean;
    risk_passed: boolean;
  };
  [field: string]: any;
}

// ==================== API ====================

export const screenerService = {
  getFields: async (): Promise<{ fields: ScreenerField[]; ops: string[] }> => {
    const res = await api.get<{ success: boolean; data: ScreenerField[]; ops: string[] }>('/screener/fields');
    return { fields: res.data, ops: res.ops };
  },

  run: async (req: ScreenRequest): Promise<{ count: number; total_capital: number; max_position_pct: number; data: ScreenRow[] }> => {
    const res = await api.post<{ success: boolean; count: number; total_capital: number; max_position_pct: number; data: ScreenRow[] }>('/screener/run', req);
    return { count: res.count, total_capital: res.total_capital, max_position_pct: res.max_position_pct, data: res.data };
  },

  refreshValuation: async (
    onEvent: (ev: { event: string; message?: string; result?: any }) => void,
    signal?: AbortSignal,
  ): Promise<void> => {
    const response = await authFetch(`${API_BASE}/screener/refresh-valuation`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      signal,
    });
    if (!response.ok) throw new Error(`请求失败: ${response.status}`);
    const reader = response.body?.getReader();
    if (!reader) throw new Error('无法获取响应流');
    const decoder = new TextDecoder();
    let buffer = '';
    while (true) {
      const { done, value } = await reader.read();
      if (done) break;
      buffer += decoder.decode(value, { stream: true });
      const lines = buffer.split('\n');
      buffer = lines.pop() || '';
      for (const line of lines) {
        const t = line.trim();
        if (!t) continue;
        try { onEvent(JSON.parse(t)); } catch { /* ignore */ }
      }
    }
    if (buffer.trim()) { try { onEvent(JSON.parse(buffer.trim())); } catch { /* ignore */ } }
  },
};

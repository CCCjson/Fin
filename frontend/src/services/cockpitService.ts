import api from './api';

const API_BASE = import.meta.env.VITE_API_URL || '/api';

// ==================== 类型 ====================

export interface DimensionResult {
  score: number | null;
  detail: Record<string, any>;
}

/** 按真实资金算出的建议买入 */
export interface PositionSizing {
  affordable: boolean;
  shares: number;
  lots: number;
  amount: number;
  target_pct: number;
  total_capital: number;
  available_cash: number;
  max_single_amount: number;
  capped_by: string | null;
  risk_passed: boolean;
  warnings: string[];
}

export interface CockpitData {
  symbol: string;
  name: string;
  as_of: string;
  price: { latest: number | null; change_5d_pct: number | null; change_20d_pct: number | null };
  valuation: { pe?: number; pe_ttm?: number; pb?: number; total_mv?: number; circ_mv?: number } | null;
  dynamic_levels: Record<string, any> | null;
  dimensions: {
    technical: DimensionResult;
    fundamental: DimensionResult;
    sentiment: DimensionResult;
    ml: DimensionResult;
    position: DimensionResult;
  };
  composite: number | null;
  recommendation: 'BUY' | 'HOLD' | 'SELL' | 'N/A';
  suggested_position_pct: number;
  suggested_add_pct: number;
  current_position_pct: number;
  stop_loss: number | null;
  weights_used: Record<string, number>;
  available_dimensions: string[];
  // 资金量相关
  total_capital: number;
  available_cash: number;
  current_position: { shares: number; value: number; pct: number };
  suggested: PositionSizing;
}

export interface CockpitSummaryEvent {
  event: 'cockpit' | 'chunk' | 'done' | 'error';
  data?: CockpitData;
  content?: string;
  message?: string;
}

// ==================== API ====================

export const cockpitService = {
  /** 获取结构化聚合（五维分 + 综合建议） */
  getCockpit: async (symbol: string): Promise<CockpitData> => {
    const res = await api.get<{ success: boolean; data: CockpitData }>(`/cockpit/${symbol}`);
    return res.data;
  },

  /** 流式 LLM 文字总结（NDJSON） */
  streamSummary: async (
    symbol: string,
    onEvent: (event: CockpitSummaryEvent) => void,
    signal?: AbortSignal,
  ): Promise<void> => {
    const response = await fetch(`${API_BASE}/cockpit/${symbol}/summary`, {
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
        const trimmed = line.trim();
        if (!trimmed) continue;
        try {
          onEvent(JSON.parse(trimmed));
        } catch (e) {
          console.warn('解析 cockpit 事件失败:', trimmed, e);
        }
      }
    }
    if (buffer.trim()) {
      try { onEvent(JSON.parse(buffer.trim())); } catch { /* ignore */ }
    }
  },
};

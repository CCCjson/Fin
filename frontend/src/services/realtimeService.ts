import api from './api';

const API_BASE = import.meta.env.VITE_API_URL || '/api';

export interface RealtimeQuote {
  symbol: string;
  code: string;
  name: string;
  price: number | null;
  change_pct: number | null;
  change_amount: number | null;
  volume: number | null;
  amount: number | null;
  amplitude: number | null;
  turnover: number | null;
  pe_ratio: number | null;
  high: number | null;
  low: number | null;
  open: number | null;
  prev_close: number | null;
  market_id: number;
}

export interface MarketStatistics {
  total: number;
  up: number;
  down: number;
  flat: number;
  limit_up: number;
  limit_down: number;
}

export interface IndexData {
  code: string;
  name: string;
  price: number;
  change_pct: number;
  change_amount: number;
  amount: number;
}

export interface RealtimeResponse {
  success: boolean;
  count: number;
  saved_count: number;
  statistics: MarketStatistics;
  data: RealtimeQuote[];
  message?: string;
}

export interface IndicesResponse {
  success: boolean;
  data: IndexData[];
}

export interface RealtimeStreamEvent {
  event: 'progress' | 'saving' | 'done' | 'error' | 'heartbeat';
  // progress
  fetched?: number;
  total?: number;
  page?: number;
  total_pages?: number;
  percent?: number;
  // saving / error
  message?: string;
  // done
  count?: number;
  saved_count?: number;
  statistics?: MarketStatistics;
  data?: RealtimeQuote[];
}

const realtimeService = {
  getQuotes: (params?: {
    use_proxy?: boolean;
    sort_by?: string;
    ascending?: boolean;
    save?: boolean;
  }): Promise<RealtimeResponse> => {
    return api.get('/realtime/quotes', { params, timeout: 120000 });
  },

  /**
   * 流式获取行情（带进度回调）
   */
  streamQuotes: async (
    params: { sort_by?: string; ascending?: boolean; save?: boolean; use_proxy?: boolean },
    onEvent: (event: RealtimeStreamEvent) => void,
    signal?: AbortSignal,
  ): Promise<void> => {
    const query = new URLSearchParams();
    if (params.sort_by) query.set('sort_by', params.sort_by);
    if (params.ascending !== undefined) query.set('ascending', String(params.ascending));
    if (params.save !== undefined) query.set('save', String(params.save));
    if (params.use_proxy !== undefined) query.set('use_proxy', String(params.use_proxy));

    const response = await fetch(
      `${API_BASE}/realtime/quotes/stream?${query.toString()}`,
      { signal },
    );

    if (!response.ok) {
      throw new Error(`获取行情失败: ${response.status}`);
    }

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
          const event: RealtimeStreamEvent = JSON.parse(trimmed);
          onEvent(event);
        } catch (e) {
          console.warn('解析行情事件失败:', trimmed, e);
        }
      }
    }

    if (buffer.trim()) {
      try {
        const event: RealtimeStreamEvent = JSON.parse(buffer.trim());
        onEvent(event);
      } catch (e) {
        console.warn('解析最后的行情事件失败:', buffer, e);
      }
    }
  },

  getIndices: (params?: {
    use_proxy?: boolean;
  }): Promise<IndicesResponse> => {
    return api.get('/realtime/indices', { params });
  },
};

export default realtimeService;

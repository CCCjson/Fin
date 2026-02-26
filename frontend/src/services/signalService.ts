import api from './api';

const API_BASE = import.meta.env.VITE_API_URL || 'http://127.0.0.1:8000';

export interface ScanMarketParams {
  symbols?: string[];
  lookback_days?: number;
  save_to_db?: boolean;
  limit?: number;
  db_only?: boolean;
}

export interface DataFreshness {
  latest_date: string | null;
  is_stale: boolean;
}

export interface TodayStatus {
  has_today_signals: boolean;
  signal_count: number;
  signal_date: string;
}

export interface ScanMarketResult {
  total_signals: number;
  buy_signals: number;
  sell_signals: number;
  symbols_scanned: number;
  results: Record<string, { count: number; buy?: number; sell?: number; error?: string }>;
}

export interface ScanProgressEvent {
  event: 'start' | 'progress' | 'complete';
  // start
  total?: number;
  start_date?: string;
  end_date?: string;
  // progress
  current?: number;
  symbol?: string;
  symbol_signals?: number;
  symbol_buy?: number;
  symbol_sell?: number;
  error?: string | null;
  cumulative?: {
    total_signals: number;
    buy_signals: number;
    sell_signals: number;
    failed: number;
  };
  // complete
  total_signals?: number;
  buy_signals?: number;
  sell_signals?: number;
  symbols_scanned?: number;
  failed?: number;
}

export const signalService = {
  // 查询数据新鲜度
  getDataFreshness: async (): Promise<DataFreshness> => {
    return api.get('/signals/data-freshness');
  },

  // 查询今日信号是否已生成（防重复扫描）
  getTodayStatus: async (): Promise<TodayStatus> => {
    return api.get('/signals/today-status');
  },

  // 获取信号列表
  getSignals: async (params?: {
    symbol?: string;
    signal_type?: string;
    start_date?: string;
    end_date?: string;
    limit?: number;
    offset?: number;
  }) => {
    return api.get('/history/signals', { params });
  },

  // 获取信号统计
  getStatistics: async (params?: {
    symbol?: string;
    days?: number;
  }) => {
    return api.get('/history/signals/statistics', { params });
  },

  // 扫描市场生成信号（原始非流式）
  scanMarket: async (params?: ScanMarketParams): Promise<ScanMarketResult> => {
    return api.post('/signals/scan', params || {});
  },

  // 流式扫描市场（带进度回调）
  scanMarketStream: async (
    params: ScanMarketParams,
    onProgress: (event: ScanProgressEvent) => void,
    signal?: AbortSignal,
  ): Promise<void> => {
    const response = await fetch(`${API_BASE}/signals/scan/stream`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(params),
      signal,
    });

    if (!response.ok) {
      throw new Error(`扫描请求失败: ${response.status}`);
    }

    const reader = response.body?.getReader();
    if (!reader) throw new Error('无法获取响应流');

    const decoder = new TextDecoder();
    let buffer = '';

    while (true) {
      const { done, value } = await reader.read();
      if (done) break;

      buffer += decoder.decode(value, { stream: true });

      // 按换行符分割，解析完整的 JSON 行
      const lines = buffer.split('\n');
      // 最后一个元素可能不完整，保留在 buffer
      buffer = lines.pop() || '';

      for (const line of lines) {
        const trimmed = line.trim();
        if (!trimmed) continue;
        try {
          const event: ScanProgressEvent = JSON.parse(trimmed);
          onProgress(event);
        } catch (e) {
          console.warn('解析进度事件失败:', trimmed, e);
        }
      }
    }

    // 处理 buffer 中剩余的数据
    if (buffer.trim()) {
      try {
        const event: ScanProgressEvent = JSON.parse(buffer.trim());
        onProgress(event);
      } catch (e) {
        console.warn('解析最后的进度事件失败:', buffer, e);
      }
    }
  },
};

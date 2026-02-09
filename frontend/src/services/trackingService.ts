import api from './api';

export interface StrategyMetrics {
  total: number;
  tracked: number;
  completed: number;
  win: number;
  loss: number;
  neutral: number;
  win_rate: number;
  avg_return_5d: number | null;
  avg_return_10d: number | null;
  avg_max_gain: number | null;
  avg_max_loss: number | null;
  stop_loss_hit_rate: number;
  take_profit_hit_rate: number;
}

export interface TrackingStats {
  overall: StrategyMetrics;
  by_strategy: Record<string, StrategyMetrics>;
}

export interface TrackedSignal {
  id: number;
  signal_id: string;
  symbol: string;
  name: string;
  strategy: string;
  signal_type: string;
  signal_date: string;
  signal_price: number;
  stop_loss: number | null;
  take_profit: number | null;
  return_1d: number | null;
  return_3d: number | null;
  return_5d: number | null;
  return_10d: number | null;
  return_20d: number | null;
  max_gain: number | null;
  max_loss: number | null;
  max_gain_day: number | null;
  max_loss_day: number | null;
  hit_stop_loss: boolean;
  hit_take_profit: boolean;
  days_to_stop: number | null;
  days_to_target: number | null;
  tracking_status: string;
  outcome: string | null;
  tracked_days: number;
}

export interface UpdateResult {
  created: number;
  updated: number;
  completed: number;
}

export interface TrackedSignalsResponse {
  signals: TrackedSignal[];
  total: number;
}

const API_BASE = import.meta.env.VITE_API_URL || 'http://127.0.0.1:8000';

export interface DataUpdateEvent {
  event: 'start' | 'progress' | 'complete' | 'error';
  // start
  total?: number;
  skipped_fresh?: number;
  date?: string;
  // progress
  current?: number;
  symbol?: string;
  name?: string;
  success?: number;
  failed?: number;
  new_records?: number;
  // complete
  skipped?: number;
  proxy_switches?: number;
  duration_seconds?: number;
  // error
  message?: string;
}

export interface DataUpdateStatus {
  total_stocks: number;
  latest_date: string | null;
  stocks_at_latest: number;
  coverage_pct: number;
  last_update: {
    status: string;
    records: number;
    duration: number;
    completed_at: string;
  } | null;
}

export const trackingService = {
  updateTracking: async (): Promise<UpdateResult> => {
    return api.post('/tracking/update');
  },

  getStats: async (params?: {
    strategy?: string;
    days?: number;
  }): Promise<TrackingStats> => {
    return api.get('/tracking/stats', { params });
  },

  getSignals: async (params?: {
    strategy?: string;
    outcome?: string;
    signal_type?: string;
    start_date?: string;
    end_date?: string;
    limit?: number;
    offset?: number;
  }): Promise<TrackedSignalsResponse> => {
    return api.get('/tracking/signals', { params });
  },

  getSignalDetail: async (signalId: string): Promise<TrackedSignal> => {
    return api.get(`/tracking/signals/${signalId}`);
  },

  // ==================== 行情数据更新 ====================

  getUpdateStatus: async (): Promise<DataUpdateStatus> => {
    return api.get('/data/update-status');
  },

  updateDailyStream: async (
    onProgress: (event: DataUpdateEvent) => void,
    signal?: AbortSignal,
  ): Promise<void> => {
    const response = await fetch(`${API_BASE}/data/update-daily/stream`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      signal,
    });

    if (!response.ok) {
      throw new Error(`更新请求失败: ${response.status}`);
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
          const event: DataUpdateEvent = JSON.parse(trimmed);
          onProgress(event);
        } catch (e) {
          console.warn('解析更新事件失败:', trimmed, e);
        }
      }
    }

    if (buffer.trim()) {
      try {
        const event: DataUpdateEvent = JSON.parse(buffer.trim());
        onProgress(event);
      } catch (e) {
        console.warn('解析最后的更新事件失败:', buffer, e);
      }
    }
  },
};

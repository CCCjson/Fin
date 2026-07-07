import api from './api';

// ====== A股深历史回补 ======

export interface AShareDeepHistoryRecent {
  symbol: string;
  name: string;
  status: 'ok' | 'failed';
  rows: number;
  at: string;
}

export interface AShareDeepHistoryStatus {
  status: 'idle' | 'running' | 'stopping' | 'stopped' | 'done';
  total_all: number;
  done_total: number;
  processed_this_run: number;
  success: number;
  failed: number;
  new_records: number;
  current_symbol: string | null;
  rate_per_min: number;
  eta_seconds: number | null;
  elapsed_seconds: number;
  recent: AShareDeepHistoryRecent[];
  config: Record<string, any>;
}

// ====== 港股/美股深历史回补 ======

export interface OverseasDeepHistoryRecent {
  symbol: string;
  status: 'ok' | 'no_data';
  rows: number;
  at: string;
}

export interface OverseasDeepHistoryStatus {
  status: 'idle' | 'running' | 'stopping' | 'stopped' | 'done';
  market: 'hk_stock' | 'us_stock' | null;
  total_all: number;
  done_total: number;
  processed_this_run: number;
  success: number;
  no_data: number;
  failed_batches: number;
  new_records: number;
  excluded: Record<string, number>;
  current_batch: string[];
  rate_per_min: number;
  eta_seconds: number | null;
  elapsed_seconds: number;
  recent: OverseasDeepHistoryRecent[];
  config: Record<string, any>;
}

type StartResult<T> = { ok: boolean; message: string } & T;

export const deepHistoryService = {
  getAShareStatus: () => api.get<AShareDeepHistoryStatus>('/deep-history/a-share/status'),

  startAShare: (body: { workers?: number; symbols?: string[]; limit?: number } = {}) =>
    api.post<StartResult<AShareDeepHistoryStatus>>('/deep-history/a-share/start', body),

  stopAShare: () =>
    api.post<StartResult<AShareDeepHistoryStatus>>('/deep-history/a-share/stop', {}),

  getOverseasStatus: () => api.get<OverseasDeepHistoryStatus>('/deep-history/overseas/status'),

  startOverseas: (body: {
    market: 'hk_stock' | 'us_stock';
    symbols?: string[]; limit?: number;
    batch_size?: number; sleep_between_batches?: number; max_retry?: number;
  }) => api.post<StartResult<OverseasDeepHistoryStatus>>('/deep-history/overseas/start', body),

  stopOverseas: () =>
    api.post<StartResult<OverseasDeepHistoryStatus>>('/deep-history/overseas/stop', {}),
};

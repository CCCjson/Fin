import api from './api';
import { authFetch } from '../utils/authFetch';

const API_BASE = import.meta.env.VITE_API_URL || '/api';

// ==================== 类型 ====================

export interface CoverageInfo {
  total_stocks: number;
  latest_date: string | null;
  stocks_at_latest: number;
  coverage_pct: number;
  last_update: {
    status: string | null;
    records: number | null;
    duration: number | null;
    completed_at: string | null;
  } | null;
}

export interface FreshnessInfo {
  latest_date: string | null;
  today: string;
  is_stale: boolean;
  is_weekday: boolean;
}

export interface AssetsInfo {
  realtime: {
    latest_snapshot: string | null;
    count_at_latest: number;
    is_today: boolean;
  };
  financial: {
    total_stocks: number;
    symbols_with_data: number;
    coverage_pct: number;
    latest_report_date: string | null;
    symbols_recent_report: number;
    last_backfill: { status: string | null; completed_at: string | null } | null;
  };
  valuation: {
    latest_date: string | null;
    count_at_latest: number;
    days_old: number | null;
  };
  news: {
    total: number;
    last_7d: number;
    latest_published_at: string | null;
  };
  limit_up: {
    latest_date: string | null;
    count_today: number;
    break_count?: number;
    ladder_summary: string;
    break_rate: number | null;
    is_today?: boolean;
  };
}

export interface UpdateLogRow {
  id: number;
  market: string;
  update_type: string;
  symbols_count: number | null;
  records_count: number | null;
  status: string;
  error_message: string | null;
  started_at: string | null;
  completed_at: string | null;
  duration_seconds: number | null;
}

export interface SchedulerStatus {
  running: boolean;
  enabled: boolean;
  is_updating: boolean;
  cron: string;
  chain_signals: boolean;
  chain_tracking: boolean;
  next_run: string | null;
  jobs: { id: string; name: string; next_run: string | null }[];
  last_run: Record<string, any> | null;
}

export interface CatchUpInfo {
  needed: boolean;
  reason: string;
  scheduled_time: string;
  last_daily_update: string | null;
}

export interface MonitorOverview {
  coverage: CoverageInfo;
  freshness: FreshnessInfo;
  assets: AssetsInfo;
  recent_update_logs: UpdateLogRow[];
  scheduler: SchedulerStatus;
  catch_up?: CatchUpInfo;
  server_time: string;
}

export interface LimitUpBoardRow {
  symbol: string;
  name: string;
  consecutive_boards: number | null;
  seal_amount: number | null;
  industry: string | null;
  zt_stat: string | null;
}

export interface LimitUpZhabanRow {
  symbol: string;
  name: string;
  break_count: number | null;
  industry: string | null;
}

export interface LimitUpDetail {
  trade_date: string;
  provisional?: boolean;
  limit_up_count: number;
  break_count: number;
  break_rate: number | null;
  ladder_distribution: Record<string, number>;
  profit_effect?: {
    sample_count?: number;
    avg_change_pct?: number | null;
    up_ratio?: number | null;
    promotion_count?: number | null;
  };
  top_boards: LimitUpBoardRow[];
  top_zhaban?: LimitUpZhabanRow[];
}

export interface FinancialBackfillBody {
  mode?: 'incremental' | 'full';
  stale_days?: number;
  workers?: number;
  start_year?: string;
}

// 日线更新流式事件（复用 DailyUpdater.update_stream 的 NDJSON）
export interface BackfilledStock {
  symbol: string;
  name: string;
  days: number;
}

export interface UpdateStreamEvent {
  event: 'start' | 'progress' | 'complete' | 'error';
  total?: number;
  current?: number;
  symbol?: string;
  success?: number;
  skipped?: number;
  failed?: number;
  new_records?: number;
  backfilled_count?: number;
  backfilled_stocks?: BackfilledStock[];
  message?: string;
  /** 慢路径重入队重试累计次数（代理故障时会持续增长） */
  retries?: number;
  /** 代理池熔断状态：closed=正常 / open=降级直连试探 / dead=直连也不可用 */
  proxy_state?: 'closed' | 'open' | 'dead';
  /** 人读的当前状态说明，如「代理连接失败，重试中…」 */
  note?: string;
  /** 时间驱动的心跳帧标记（计数可能与上一帧相同，仅用于告诉前端后端还活着） */
  heartbeat?: boolean;
  /** complete 事件：本次任务是否因熔断/静默超时被中止 */
  aborted?: boolean;
  /** complete 事件：中止原因 */
  abort_reason?: 'proxy_pool_dead' | 'stalled' | 'workers_exited' | string;
  [k: string]: any;
}

// ==================== 服务 ====================

export const dataMonitorService = {
  getOverview: (): Promise<MonitorOverview> => api.get('/data-monitor/overview'),

  toggleScheduler: (enabled: boolean): Promise<SchedulerStatus> =>
    api.post('/data-monitor/scheduler/toggle', { enabled }),

  refreshLimitUp: (): Promise<{ ok: boolean; candidate_count?: number; error?: string }> =>
    api.post('/data-monitor/limit-up/refresh'),

  getLimitUpDetail: (): Promise<LimitUpDetail> => api.get('/data-monitor/limit-up/detail'),

  /** 手动触发全市场日线更新，NDJSON 流式解析（复用 agentService 的 reader 循环） */
  streamUpdate: async (
    onEvent: (event: UpdateStreamEvent) => void,
    signal?: AbortSignal,
  ): Promise<void> => {
    const response = await authFetch(`${API_BASE}/data/update-daily/stream`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      signal,
    });
    if (!response.ok || !response.body) {
      throw new Error(`更新请求失败: ${response.status}`);
    }
    const reader = response.body.getReader();
    const decoder = new TextDecoder();
    let buffer = '';
    while (true) {
      const { done, value } = await reader.read();
      if (done) break;
      buffer += decoder.decode(value, { stream: true });
      const lines = buffer.split('\n');
      buffer = lines.pop() || '';
      for (const line of lines) {
        if (!line.trim()) continue;
        try {
          onEvent(JSON.parse(line));
        } catch (e) {
          console.warn('解析更新事件失败:', line, e);
        }
      }
    }
  },

  /** 触发全市场财务数据回补，NDJSON 流式解析（协议同 streamUpdate） */
  streamFinancialBackfill: async (
    body: FinancialBackfillBody,
    onEvent: (event: UpdateStreamEvent) => void,
    signal?: AbortSignal,
  ): Promise<void> => {
    const response = await authFetch(`${API_BASE}/data/financial/backfill/stream`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
      signal,
    });
    if (!response.ok || !response.body) {
      throw new Error(`财务回补请求失败: ${response.status}`);
    }
    const reader = response.body.getReader();
    const decoder = new TextDecoder();
    let buffer = '';
    while (true) {
      const { done, value } = await reader.read();
      if (done) break;
      buffer += decoder.decode(value, { stream: true });
      const lines = buffer.split('\n');
      buffer = lines.pop() || '';
      for (const line of lines) {
        if (!line.trim()) continue;
        try {
          onEvent(JSON.parse(line));
        } catch (e) {
          console.warn('解析财务回补事件失败:', line, e);
        }
      }
    }
  },
};

export default dataMonitorService;

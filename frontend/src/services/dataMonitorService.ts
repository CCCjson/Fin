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
  /** 港美股是**独立 job**（独立 cron 16:30）。后端一直返回这些字段，
   *  但此前前端既没声明也没渲染 —— 于是那个 job 在界面上不可见不可关。 */
  overseas_enabled?: boolean;
  overseas_cron?: string;
  overseas_is_updating?: boolean;
  overseas_last_run?: Record<string, any> | null;
  /** 缺口自动补齐（管中间的洞；尾部落后归 catch_up 管） */
  gap_autofill_enabled?: boolean;
  gap_autofill_interval_hours?: number;
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

// ==================== 资产矩阵（doc16 新增）====================

export type AssetHealth = 'ok' | 'warn' | 'stale' | 'unknown';

export interface AssetCell {
  key: string;
  label: string;
  group: 'calendar' | 'quote' | 'fundamental' | 'sentiment' | 'derived';
  market: string | null;
  cadence: 'daily_after_close' | 'interval' | 'on_demand';
  enabled: boolean;
  in_update_all: boolean;
  supports_gap_fill: boolean;
  hint: string;
  latest_date: string | null;
  count_at_latest: number | null;
  detail: string;
  behind_trading_days: number | null;
  gaps_certain: number;
  gaps_suspected: number;
  health: AssetHealth;
  health_reason: string;
  last_run: {
    status: string;
    completed_at: string | null;
    records: number | null;
    duration_seconds: number | null;
  } | null;
}

export interface AssetMatrix {
  cells: AssetCell[];
  overall_health: AssetHealth;
  markets: string[];
  groups: string[];
  server_time: string;
}

// ==================== 运行进度（后台任务）====================

export interface AssetProgress {
  label: string;
  market: string | null;
  status: 'pending' | 'running' | 'done' | 'failed' | 'skipped' | 'partial';
  current: number;
  total: number | null;
  updated: number;
  failed: number;
  records: number;
  fresh_skipped: number;
  note: string;
}

export interface RunSnapshot {
  /** idle / running / stopping / stopped / done */
  status: string;
  total_all: number;
  done_total: number;
  processed_this_run: number;
  rate_per_min: number;
  eta_seconds: number | null;
  elapsed_seconds: number;
  recent: { symbol: string; name: string; status: string; rows: number; at: string }[];
  config: Record<string, any>;
  mode: string;
  planned: string[];
  current_asset: string | null;
  asset_progress: Record<string, AssetProgress>;
  results: Record<string, Record<string, any>>;
  /** start/stop 调用时附带 */
  ok?: boolean;
  message?: string;
}

// ==================== 缺口 ====================

export interface GapRow {
  id: number;
  asset: string;
  market: string;
  date: string | null;
  /** open=待补 / filling=补齐中 / filled=已补上 / permanent=实测认定为假期 / suspected=疑似（无日历） */
  status: 'open' | 'filling' | 'filled' | 'permanent' | 'suspected';
  /** certain=基准指数自证（可自动补）/ suspected=工作日启发式（只展示不补） */
  confidence: 'certain' | 'suspected';
  attempts: number;
  observed_count: number | null;
  expected_count: number | null;
  note: string | null;
}

export interface GapSummary {
  total: number;
  fillable: number;
  by_asset: {
    asset: string;
    market: string;
    certain: number;
    suspected: number;
    dates: string[];
  }[];
}

export interface GapsResponse {
  gaps: GapRow[];
  summary: GapSummary;
}

export interface GapJobSnapshot extends RunSnapshot {
  phase: 'idle' | 'scanning' | 'filling';
  current_asset: string | null;
  gaps_found: number;
  gaps_fillable: number;
  days_filled: number;
  days_permanent: number;
  scan_result: Record<string, any> | null;
  fills: Record<string, any>[];
}

// ==================== 调度器 ====================

export interface SchedulerRow {
  id: string;
  label: string;
  description?: string;
  enabled: boolean | null;
  running: boolean | null;
  is_updating: boolean | null;
  cron: string | null;
  next_run: string | null;
  last_run: Record<string, any> | null;
  toggleable: boolean;
  toggle_hint?: string;
  error?: string;
}

// ==================== 交易日历 ====================

export interface CalendarStatus {
  [market: string]: {
    benchmark: string | null;
    days: number | null;
    first_date: string | null;
    latest_date: string | null;
    note?: string;
  };
}

// ==================== 涨停池 ====================

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

// 日线更新流式事件（老端点用，保留）
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
  retries?: number;
  proxy_state?: 'closed' | 'open' | 'dead';
  note?: string;
  heartbeat?: boolean;
  aborted?: boolean;
  abort_reason?: 'proxy_pool_dead' | 'stalled' | 'workers_exited' | string;
  [k: string]: any;
}

export interface MarketFreshness {
  market: string;
  reference_date: string | null;
  coverage_ratio_of_baseline: number | null;
  is_stale: boolean;
  version?: string;
}

export interface FreshnessReport {
  latest_date: string | null;
  today: string;
  is_stale: boolean;
  is_weekday: boolean;
  by_market: Record<string, MarketFreshness>;
  version?: string;
}

export interface RefreshStreamEvent {
  event: 'plan' | 'start' | 'progress' | 'complete' | 'error' | 'skipped' | 'all_complete';
  market?: string;
  markets?: string[];
  freshness?: FreshnessReport;
  summary?: Record<string, any>;
  total?: number;
  current?: number;
  symbol?: string;
  name?: string;
  updated?: number;
  rows?: number;
  success?: number;
  skipped?: number;
  failed?: number;
  new_records?: number;
  message?: string;
  [k: string]: any;
}

// ==================== NDJSON 流工具 ====================

async function consumeNdjson(
  url: string,
  init: RequestInit,
  onEvent: (event: any) => void,
): Promise<void> {
  const response = await authFetch(url, init);
  if (!response.ok || !response.body) {
    throw new Error(`请求失败: ${response.status}`);
  }
  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let buffer = '';
  for (;;) {
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
        console.warn('解析流式事件失败:', line, e);
      }
    }
  }
}

// ==================== 服务 ====================

export const dataMonitorService = {
  getOverview: (): Promise<MonitorOverview> => api.get('/data-monitor/overview'),

  // ---- 资产矩阵 ----
  getAssets: (): Promise<AssetMatrix> => api.get('/data-monitor/assets'),

  // ---- 一键更新全部（后台任务，关页面不断）----
  startRun: (body: { scope?: string[]; mode?: 'incremental' | 'gap_fill' } = {}):
    Promise<RunSnapshot> => api.post('/data-monitor/runs', body),
  getRun: (): Promise<RunSnapshot> => api.get('/data-monitor/runs/current'),
  stopRun: (): Promise<RunSnapshot> => api.post('/data-monitor/runs/stop'),

  // ---- 缺口 ----
  getGaps: (params?: { status?: string; market?: string }): Promise<GapsResponse> =>
    api.get('/data-monitor/gaps', { params }),
  scanGaps: (body: { scan_only?: boolean; asset_key?: string; lookback_days?: number } = {}):
    Promise<GapJobSnapshot> => api.post('/data-monitor/gaps/scan', body),
  getGapJob: (): Promise<GapJobSnapshot> => api.get('/data-monitor/gaps/job'),
  stopGapJob: (): Promise<GapJobSnapshot> => api.post('/data-monitor/gaps/job/stop'),

  // ---- 调度器（4 个，逐个开关）----
  getSchedulers: (): Promise<{ schedulers: SchedulerRow[] }> =>
    api.get('/data-monitor/schedulers'),
  toggleScheduler2: (id: string, enabled: boolean): Promise<{ schedulers: SchedulerRow[] }> =>
    api.post(`/data-monitor/schedulers/${id}/toggle`, { enabled }),

  // ---- 交易日历 ----
  getCalendar: (): Promise<CalendarStatus> => api.get('/data-monitor/calendar'),

  // ---- 老端点（别处仍在用，保留）----
  toggleScheduler: (enabled: boolean): Promise<SchedulerStatus> =>
    api.post('/data-monitor/scheduler/toggle', { enabled }),

  refreshLimitUp: (): Promise<{ ok: boolean; candidate_count?: number; error?: string }> =>
    api.post('/data-monitor/limit-up/refresh'),

  getLimitUpDetail: (): Promise<LimitUpDetail> => api.get('/data-monitor/limit-up/detail'),

  getFreshness: (): Promise<FreshnessReport> => api.get('/data/freshness'),

  refreshMarkets: (
    scope: string[] | undefined,
    onEvent: (event: RefreshStreamEvent) => void,
    signal?: AbortSignal,
  ): Promise<void> =>
    consumeNdjson(
      `${API_BASE}/data/refresh/stream`,
      {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(scope && scope.length ? { scope } : {}),
        signal,
      },
      onEvent,
    ),

  streamUpdate: (
    onEvent: (event: UpdateStreamEvent) => void,
    signal?: AbortSignal,
  ): Promise<void> =>
    consumeNdjson(
      `${API_BASE}/data/update-daily/stream`,
      { method: 'POST', headers: { 'Content-Type': 'application/json' }, signal },
      onEvent,
    ),

  streamFinancialBackfill: (
    body: FinancialBackfillBody,
    onEvent: (event: UpdateStreamEvent) => void,
    signal?: AbortSignal,
  ): Promise<void> =>
    consumeNdjson(
      `${API_BASE}/data/financial/backfill/stream`,
      {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(body),
        signal,
      },
      onEvent,
    ),
};

export default dataMonitorService;

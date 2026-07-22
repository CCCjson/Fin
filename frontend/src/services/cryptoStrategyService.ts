import api from './api';

/** crypto 半自动策略 —— REST 封装 /crypto-strategy/*（需求3）。 */

export interface CryptoStrategyItem {
  strategy_id: string;
  name: string;
  mode: 'paper' | 'live';
  status: string;              // draft|backtested|armed|paused_by_guardrail|retired
  enabled: boolean;
  strategy_kind: 'swing' | 'arb' | 'long_hold';
  interval_minutes: number;
  backtest_passed: boolean;
  backtest_net_return: number | null;
  last_run_at: string | null;
  halted_reason: string | null;
}

// ⏱ 所有 *_at 字段都是**带 UTC offset 的 ISO 串**（后端 common/market_time.utc_iso）。
//    渲染一律走 utils/datetime.ts，禁止字符串切片——切片会原样显示 UTC 时刻。
export interface CryptoPendingItem {
  order_ref: string;
  strategy_id: string;
  symbol: string;
  side: 'BUY' | 'SELL';
  quantity: number;
  quote_amount: number | null;
  price: number | null;
  est_notional: number | null;
  // PENDING|EXECUTING|FILLED|UNFILLED|FAILED|STALE
  //   UNFILLED = 已受理但零成交（不是成交也不是失败）
  //   STALE    = 成交与否未知，等人工去币安对账；系统不会自行了结
  status: string;
  net_edge: number | null;
  reason: Record<string, unknown> | null;
  created_at: string | null;
  expires_at: string | null;
  executed_order_id: string | null;
  fill_price: number | null;
  fill_quantity: number | null;
  error_message: string | null;
}

export interface CryptoEngineStatus {
  is_running: boolean;
  ticking: boolean;
  enabled: boolean;
  killed: boolean;
  tick_min: number;
}

export interface CryptoRunItem {
  id: number;
  status: string;
  mode: string | null;
  symbols_evaluated: number;
  orders_placed: number;
  started_at: string | null;
  decision_detail: string | null;
  error: string | null;
}

export const cryptoStrategyService = {
  // 策略
  listStrategies: (params?: { mode?: string; enabled?: boolean }): Promise<{ strategies: CryptoStrategyItem[] }> =>
    api.get('/crypto-strategy/strategies', { params }),
  getStrategy: (id: string): Promise<any> =>
    api.get(`/crypto-strategy/strategies/${encodeURIComponent(id)}`),
  backtest: (id: string): Promise<any> =>
    api.post(`/crypto-strategy/strategies/${encodeURIComponent(id)}/backtest`),
  arm: (id: string): Promise<CryptoStrategyItem> =>
    api.post(`/crypto-strategy/strategies/${encodeURIComponent(id)}/arm`),
  enablePaper: (id: string): Promise<CryptoStrategyItem> =>
    api.post(`/crypto-strategy/strategies/${encodeURIComponent(id)}/enable-paper`),
  pause: (id: string): Promise<CryptoStrategyItem> =>
    api.post(`/crypto-strategy/strategies/${encodeURIComponent(id)}/pause`),
  retire: (id: string): Promise<CryptoStrategyItem> =>
    api.post(`/crypto-strategy/strategies/${encodeURIComponent(id)}/retire`),
  listRuns: (strategyId: string, limit = 50): Promise<{ runs: CryptoRunItem[] }> =>
    api.get('/crypto-strategy/runs', { params: { strategy_id: strategyId, limit } }),

  // 引擎
  engineStatus: (): Promise<CryptoEngineStatus> => api.get('/crypto-strategy/engine/status'),
  engineStart: (): Promise<CryptoEngineStatus> => api.post('/crypto-strategy/engine/start'),
  engineStop: (): Promise<CryptoEngineStatus> => api.post('/crypto-strategy/engine/stop'),
  engineKill: (): Promise<{ killed: boolean }> => api.post('/crypto-strategy/engine/kill'),
  engineUnkill: (): Promise<{ killed: boolean }> => api.post('/crypto-strategy/engine/unkill'),

  // 待确认单
  listPending: (status = 'PENDING', limit = 100): Promise<{ pending: CryptoPendingItem[] }> =>
    api.get('/crypto-strategy/pending', { params: { status, limit } }),
  confirmPending: (ref: string): Promise<CryptoPendingItem> =>
    api.post(`/crypto-strategy/pending/${encodeURIComponent(ref)}/confirm`),
  rejectPending: (ref: string): Promise<{ order_ref: string; status: string; deleted: boolean }> =>
    api.post(`/crypto-strategy/pending/${encodeURIComponent(ref)}/reject`),
};

export default cryptoStrategyService;

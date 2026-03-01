import api from './api';

// ==================== 类型定义 ====================

export interface ManualTrade {
  id: number;
  symbol: string;
  name: string | null;
  side: 'BUY' | 'SELL';
  price: number;
  quantity: number;
  amount: number;
  commission: number;
  trade_date: string;
  note: string | null;
  report_id: string | null;
  ai_recommended_price: number | null;
  ai_stop_loss: number | null;
  ai_take_profit: number | null;
  ai_composite_score: number | null;
  ai_strategy: string | null;
  created_at: string | null;
  updated_at: string | null;
}

export interface TradeCreateParams {
  symbol: string;
  name?: string;
  side: 'BUY' | 'SELL';
  price: number;
  quantity: number;
  amount?: number;
  commission?: number;
  trade_date: string;
  note?: string;
  report_id?: string;
  ai_recommended_price?: number;
  ai_stop_loss?: number;
  ai_take_profit?: number;
  ai_composite_score?: number;
  ai_strategy?: string;
}

export interface TradeUpdateParams {
  symbol?: string;
  name?: string;
  side?: 'BUY' | 'SELL';
  price?: number;
  quantity?: number;
  amount?: number;
  commission?: number;
  trade_date?: string;
  note?: string;
}

export interface PortfolioPosition {
  symbol: string;
  name: string;
  quantity: number;
  avg_cost: number;
  current_price: number | null;
  market_value: number | null;
  total_cost: number;
  unrealized_pnl: number | null;
  unrealized_pnl_pct: number | null;
  realized_pnl: number;
}

export interface PortfolioStats {
  total_invested: number;
  current_value: number;
  total_cost_holding: number;
  realized_pnl: number;
  unrealized_pnl: number;
  total_pnl: number;
  total_pnl_pct: number;
  win_count: number;
  loss_count: number;
  win_rate: number;
  avg_win: number;
  avg_loss: number;
  profit_loss_ratio: number;
  total_commission: number;
  total_trades: number;
  total_closed_trades: number;
}

export interface ReportRecommendation {
  symbol: string;
  name: string;
  price: number | null;
  stop_loss: number | null;
  take_profit: number | null;
  score: number | null;
  strategy: string;
}

export interface RiskWarning {
  rule: string;
  message: string;
  severity: 'WARNING' | 'ERROR';
}

export interface TradeCreateResponse extends ManualTrade {
  risk_warnings: RiskWarning[];
}

export interface RiskAlert {
  symbol: string;
  name: string;
  rule: string;
  message: string;
  severity: 'WARNING' | 'ERROR';
}

export interface RiskOverview {
  total_capital: number;
  market_value: number;
  cash: number;
  total_position_pct: number;
  max_total_position_pct: number;
  total_position_ok: boolean;
  consecutive_losses: number;
  max_consecutive_losses: number;
  consecutive_loss_ok: boolean;
  last_loss_date: string | null;
}

export interface PositionRisk {
  symbol: string;
  name: string;
  pnl_pct: number;
  position_pct: number;
  max_position_pct: number;
  position_overweight: boolean;
  stop_loss_pct: number;
  take_profit_pct: number;
  dist_stop_loss: number;
  dist_take_profit: number;
  level: 'danger' | 'warning' | 'take_profit' | 'near_tp' | 'safe';
}

export interface RiskMonitorResponse {
  overview: RiskOverview;
  position_risks: PositionRisk[];
  alerts: RiskAlert[];
  error?: string;
}

export interface TradeListParams {
  symbol?: string;
  side?: string;
  start_date?: string;
  end_date?: string;
  limit?: number;
  offset?: number;
}

// ==================== 已平仓交易类型 ====================

export interface ClosedTrade {
  id: number;
  symbol: string;
  name: string | null;
  buy_trade_id: number;
  buy_date: string;
  buy_price: number;
  buy_quantity: number;
  buy_signal_strategy: string | null;
  buy_signal_strength: number | null;
  ai_stop_loss: number | null;
  ai_take_profit: number | null;
  market_env: 'bullish' | 'neutral' | 'bearish' | 'unknown' | null;
  market_env_detail: Record<string, any> | null;
  sell_trade_id: number;
  sell_date: string;
  sell_price: number;
  sell_quantity: number;
  sell_reason: 'take_profit' | 'stop_loss' | 'manual_close' | null;
  holding_days: number | null;
  pnl: number | null;
  pnl_pct: number | null;
  total_commission: number | null;
  benchmark_return_pct: number | null;
  excess_return_pct: number | null;
  created_at: string | null;
}

export interface ClosedTradeSummary {
  count: number;
  win_count: number;
  loss_count: number;
  win_rate: number;
  total_pnl: number;
  avg_pnl_pct: number;
  avg_holding_days: number;
  avg_win_pct: number;
  avg_loss_pct: number;
  avg_benchmark_return_pct: number | null;
  avg_excess_return_pct: number | null;
}

export interface ClosedTradeListParams {
  symbol?: string;
  sell_reason?: string;
  market_env?: string;
  start_date?: string;
  end_date?: string;
  sort_by?: string;
  sort_order?: 'asc' | 'desc';
  limit?: number;
  offset?: number;
}

export interface ClosedTradeStats {
  overall: ClosedTradeSummary;
  by_strategy: Array<{ name: string } & ClosedTradeSummary>;
  by_sell_reason: Array<{ name: string } & ClosedTradeSummary>;
  by_market_env: Array<{ name: string } & ClosedTradeSummary>;
}

// ==================== API ====================

export const portfolioService = {
  /** 录入交易（返回包含风控警告） */
  createTrade: async (params: TradeCreateParams): Promise<TradeCreateResponse> => {
    return api.post('/portfolio/trades', params);
  },

  /** 交易列表 */
  getTrades: async (params?: TradeListParams): Promise<{ total: number; trades: ManualTrade[] }> => {
    return api.get('/portfolio/trades', { params });
  },

  /** 修改交易 */
  updateTrade: async (id: number, params: TradeUpdateParams): Promise<ManualTrade> => {
    return api.put(`/portfolio/trades/${id}`, params);
  },

  /** 删除交易 */
  deleteTrade: async (id: number): Promise<{ message: string }> => {
    return api.delete(`/portfolio/trades/${id}`);
  },

  /** 当前持仓 */
  getPositions: async (): Promise<{ positions: PortfolioPosition[] }> => {
    return api.get('/portfolio/positions');
  },

  /** 绩效统计 */
  getStats: async (): Promise<PortfolioStats> => {
    return api.get('/portfolio/stats');
  },

  /** 获取报告推荐列表 */
  getReportRecommendations: async (reportId: string): Promise<{
    report_id: string;
    title: string;
    recommendations: ReportRecommendation[];
  }> => {
    return api.get(`/portfolio/reports/${reportId}/recommendations`);
  },

  /** 风控监控 */
  getRiskMonitor: async (): Promise<RiskMonitorResponse> => {
    return api.get('/portfolio/risk-monitor');
  },

  /** 从报告批量导入 */
  importFromReport: async (reportId: string, trades: TradeCreateParams[]): Promise<{
    message: string;
    count: number;
    trades: ManualTrade[];
  }> => {
    return api.post('/portfolio/import-from-report', { report_id: reportId, trades });
  },

  /** 已平仓交易列表 */
  getClosedTrades: async (params?: ClosedTradeListParams): Promise<{
    total: number;
    trades: ClosedTrade[];
    summary: ClosedTradeSummary;
  }> => {
    return api.get('/portfolio/closed-trades', { params });
  },

  /** 重建已平仓记录 */
  rebuildClosedTrades: async (): Promise<{ message: string; count: number; errors: number }> => {
    return api.post('/portfolio/closed-trades/rebuild');
  },

  /** 已平仓交易统计 */
  getClosedTradeStats: async (): Promise<ClosedTradeStats> => {
    return api.get('/portfolio/closed-trades/stats');
  },

  /** 获取用户设置 */
  getSettings: async (): Promise<Record<string, { value: string; description: string; updated_at: string | null }>> => {
    return api.get('/data/settings');
  },

  /** 更新用户设置 */
  updateSetting: async (key: string, value: string): Promise<{ key: string; value: string; message: string }> => {
    return api.put(`/data/settings/${key}`, null, { params: { value } });
  },
};

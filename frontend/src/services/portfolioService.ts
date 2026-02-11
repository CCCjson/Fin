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

export interface TradeListParams {
  symbol?: string;
  side?: string;
  start_date?: string;
  end_date?: string;
  limit?: number;
  offset?: number;
}

// ==================== API ====================

export const portfolioService = {
  /** 录入交易 */
  createTrade: async (params: TradeCreateParams): Promise<ManualTrade> => {
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

  /** 从报告批量导入 */
  importFromReport: async (reportId: string, trades: TradeCreateParams[]): Promise<{
    message: string;
    count: number;
    trades: ManualTrade[];
  }> => {
    return api.post('/portfolio/import-from-report', { report_id: reportId, trades });
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

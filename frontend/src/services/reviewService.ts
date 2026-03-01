import api from './api';

// ==================== 类型定义 ====================

export interface IndexData {
  symbol: string;
  name: string;
  price: number | null;
  change_pct: number | null;
}

export interface PositionDaily {
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
  today_close: number | null;
  prev_close: number | null;
  daily_change_pct: number | null;
  daily_pnl: number | null;
  daily_pnl_pct: number | null;
}

export interface DayTrade {
  id: number;
  symbol: string;
  name: string | null;
  side: 'BUY' | 'SELL';
  price: number;
  quantity: number;
  amount: number;
  commission: number;
  note: string | null;
  ai_recommended_price: number | null;
  source_type: 'automation' | 'manual_broker' | 'manual_entry' | null;
  pending_order_id: string | null;
}

export interface Decision {
  order_id: string;
  symbol: string;
  name: string | null;
  signal_type: 'BUY' | 'SELL';
  strategy: string | null;
  strength: number;
  suggested_price: number | null;
  stop_loss: number | null;
  take_profit: number | null;
  reasons: Array<{ indicator?: string; detail?: string }>;
  status: 'FILLED' | 'REJECTED' | 'EXPIRED' | 'FAILED';
  actual_price: number | null;
  reject_reason: string | null;
  risk_check_detail: Array<{ rule?: string; passed?: boolean; message?: string }>;
  created_at: string | null;
  confirmed_at: string | null;
}

export interface DaySignal {
  id: number;
  symbol: string;
  name: string;
  signal_type: 'BUY' | 'SELL';
  strength: number;
  price: number;
  strategy: string | null;
  holding_status: 'held' | 'sold_today' | 'not_held';
}

export interface AiDimensionItem {
  score: number;
  comment: string;
}

export interface AiDimensionScores {
  dimensions: {
    discipline: AiDimensionItem;
    position: AiDimensionItem;
    timing: AiDimensionItem;
    signal_follow: AiDimensionItem;
    reflection: AiDimensionItem;
  };
  highlights: string[];
  improvements: string[];
}

export interface ReviewRecord {
  id: number;
  review_date: string;
  self_score: number | null;
  ai_score: number | null;
  ai_score_reason: string | null;
  ai_dimension_scores: AiDimensionScores | null;
  composite_score: number | null;
  note: string | null;
  template_used: string;
  updated_at: string | null;
}

export interface ReviewData {
  date: string;
  a_share_closed: boolean;
  hk_closed: boolean;
  us_closed: boolean;
  indices: IndexData[];
  daily_pnl: number;
  daily_pnl_pct: number | null;
  positions: PositionDaily[];
  positions_count: number;
  trades: DayTrade[];
  trades_count: number;
  signals: DaySignal[];
  signals_total: number;
  signals_count: number;
  decisions: Decision[];
  decisions_count: number;
  decisions_filled: number;
  decisions_rejected: number;
  decisions_expired: number;
  decisions_failed: number;
  review: ReviewRecord | null;
  template: string;
}

export interface AiScoreResult {
  ai_score: number;
  ai_score_reason: string;
  ai_dimension_scores: AiDimensionScores | null;
  composite_score: number;
  self_score: number | null;
}

export interface CalendarEntry {
  date: string;
  composite_score: number | null;
  self_score: number | null;
  ai_score: number | null;
  has_note: boolean;
}

export interface ReviewSummary {
  date: string;
  daily_pnl: number | null;
  composite_score: number | null;
  self_score: number | null;
  ai_score: number | null;
  trades_count: number | null;
  has_note: boolean;
}

// ==================== API ====================

export const reviewService = {
  /** 获取某天的完整复盘数据 */
  getReview: async (date: string, params?: {
    signals_limit?: number;
    signals_offset?: number;
  }): Promise<ReviewData> => {
    return api.get(`/review/${date}`, { params });
  },

  /** 保存复盘笔记和自评分 */
  saveNote: async (date: string, params: {
    note: string;
    self_score?: number;
    template_used?: string;
  }): Promise<ReviewRecord> => {
    return api.put(`/review/${date}/note`, params);
  },

  /** 请求 AI 评分 */
  requestAiScore: async (date: string): Promise<AiScoreResult> => {
    return api.post(`/review/${date}/ai-score`);
  },

  /** 获取月度复盘日历 */
  getCalendar: async (year: number, month: number): Promise<{
    year: number;
    month: number;
    reviews: CalendarEntry[];
  }> => {
    return api.get(`/review/calendar/${year}/${month}`);
  },

  /** 获取最近复盘摘要 */
  getSummary: async (limit?: number): Promise<{ summaries: ReviewSummary[] }> => {
    return api.get('/review/summary/recent', { params: { limit } });
  },
};

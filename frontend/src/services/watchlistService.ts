import api from './api';

// ==================== 类型 ====================

export interface WatchlistItem {
  id: number;
  symbol: string;
  name: string | null;
  group_name: string;
  note: string | null;
  sort_order: number;
}

export interface PriceAlert {
  alert_id: string;
  symbol: string;
  name: string | null;
  alert_type: 'price_above' | 'price_below' | 'pct_change';
  threshold: number;
  status: 'active' | 'triggered' | 'paused';
  repeat: number;
  triggered_at: string | null;
  triggered_price: number | null;
  message: string | null;
}

// ==================== API ====================

export const watchlistService = {
  list: async (): Promise<WatchlistItem[]> => {
    const res = await api.get<{ success: boolean; data: WatchlistItem[] }>('/watchlist');
    return res.data;
  },
  groups: async (): Promise<string[]> => {
    const res = await api.get<{ success: boolean; data: string[] }>('/watchlist/groups');
    return res.data;
  },
  add: (body: { symbol: string; name?: string; group_name?: string; note?: string }) =>
    api.post('/watchlist', body),
  update: (id: number, body: Partial<Pick<WatchlistItem, 'name' | 'group_name' | 'note' | 'sort_order'>>) =>
    api.put(`/watchlist/${id}`, body),
  remove: (id: number) => api.delete(`/watchlist/${id}`),

  // 预警
  listAlerts: async (status?: string): Promise<PriceAlert[]> => {
    const res = await api.get<{ success: boolean; data: PriceAlert[] }>('/watchlist/alerts', { params: status ? { status } : undefined });
    return res.data;
  },
  createAlert: (body: { symbol: string; alert_type: string; threshold: number; name?: string; repeat?: number; message?: string }) =>
    api.post('/watchlist/alerts', body),
  updateAlertStatus: (alertId: string, status: 'active' | 'paused') =>
    api.put(`/watchlist/alerts/${alertId}`, null, { params: { status } }),
  removeAlert: (alertId: string) => api.delete(`/watchlist/alerts/${alertId}`),
};

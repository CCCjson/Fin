import api from './api';

export const signalService = {
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
    start_date?: string;
    end_date?: string;
  }) => {
    return api.get('/history/signals/statistics', { params });
  },
};

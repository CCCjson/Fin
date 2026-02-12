import api from './api';

export const marketService = {
  // 获取日线数据
  getDailyData: async (symbol: string, startDate: string, endDate: string, dbOnly = false) => {
    return api.post('/data/daily', {
      symbol,
      start_date: startDate,
      end_date: endDate,
      db_only: dbOnly,
    });
  },

  // 获取股票列表
  getStockList: async (market: string = 'A') => {
    return api.get('/data/stocks', { params: { market } });
  },

  // 更新数据
  updateData: async (symbol: string, days: number = 30) => {
    return api.post('/data/update', null, {
      params: { symbol, days },
    });
  },
};

import api from './api';

export const tradingService = {
  // 初始化账户
  initAccount: async (initialCash: number = 1000000) => {
    return api.post('/trading/init', null, {
      params: { initial_cash: initialCash, commission_rate: 0.0003 },
    });
  },

  // 提交订单
  submitOrder: async (symbol: string, action: string, quantity: number, price?: number) => {
    return api.post('/trading/order', {
      symbol,
      action,
      quantity,
      price,
    });
  },

  // 更新价格
  updatePrice: async (symbol: string, price: number) => {
    return api.post('/trading/price/update', { symbol, price });
  },

  // 获取账户信息
  getAccount: async () => {
    return api.get('/trading/account');
  },

  // 获取持仓
  getPosition: async (symbol: string) => {
    return api.get(`/trading/position/${symbol}`);
  },

  // 获取订单列表
  getOrders: async (symbol?: string) => {
    return api.get('/trading/orders', { params: { symbol } });
  },
};

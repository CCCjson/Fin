import React, { useState, useEffect } from 'react';
import { OrderForm } from '../components/trading/OrderForm';
import { AccountInfo } from '../components/trading/AccountInfo';
import { tradingService } from '../services/tradingService';
import { marketService } from '../services/marketService';
import type { Account, Order } from '../types';

export const Trading: React.FC = () => {
  const [account, setAccount] = useState<Account | null>(null);
  const [orders, setOrders] = useState<Order[]>([]);
  const [loading, setLoading] = useState(false);
  const [message, setMessage] = useState<{ type: 'success' | 'error'; text: string } | null>(null);

  useEffect(() => {
    loadAccount();
    loadOrders();
  }, []);

  const loadAccount = async () => {
    try {
      const data = await tradingService.getAccount();
      setAccount(data);
    } catch (error) {
      console.error('Failed to load account:', error);
    }
  };

  const loadOrders = async () => {
    try {
      const data = await tradingService.getOrders();
      setOrders(data.orders || []);
    } catch (error) {
      console.error('Failed to load orders:', error);
    }
  };

  const handleSubmitOrder = async (
    symbol: string,
    action: string,
    quantity: number,
    price?: number
  ) => {
    try {
      setLoading(true);
      setMessage(null);

      // 获取最新价格并更新（使用最近30天的数据）
      try {
        const endDate = new Date().toISOString().split('T')[0];
        const startDate = new Date(Date.now() - 30 * 24 * 60 * 60 * 1000).toISOString().split('T')[0];
        const marketData = await marketService.getDailyData(symbol, startDate, endDate);
        if (marketData.data && marketData.data.length > 0) {
          const latestPrice = marketData.data[marketData.data.length - 1].close;
          await tradingService.updatePrice(symbol, latestPrice);
        }
      } catch (priceError) {
        console.warn('无法获取最新价格，使用默认价格', priceError);
        // 如果获取价格失败，继续执行下单，使用用户输入的价格
      }

      // 提交订单
      const order = await tradingService.submitOrder(symbol, action, quantity, price);

      setMessage({
        type: 'success',
        text: `订单提交成功！订单ID: ${order.order_id}`,
      });

      // 刷新账户和订单
      await loadAccount();
      await loadOrders();
    } catch (error: any) {
      setMessage({
        type: 'error',
        text: error.response?.data?.detail || '订单提交失败',
      });
    } finally {
      setLoading(false);
    }
  };

  return (
    <div className="min-h-screen bg-gradient-dark p-6">
      <div className="max-w-7xl mx-auto space-y-6">
        <h1 className="text-3xl font-bold text-white">交易</h1>

        {/* 消息提示 */}
        {message && (
          <div
            className={`p-4 rounded-lg ${
              message.type === 'success'
                ? 'bg-bull/20 text-bull border border-bull/30'
                : 'bg-bear/20 text-bear border border-bear/30'
            }`}
          >
            {message.text}
          </div>
        )}

        <div className="grid grid-cols-1 lg:grid-cols-3 gap-6">
          {/* 下单表单 */}
          <div>
            <OrderForm onSubmit={handleSubmitOrder} loading={loading} />
          </div>

          {/* 账户信息 */}
          <div className="lg:col-span-2">
            <AccountInfo account={account} />
          </div>
        </div>

        {/* 订单列表 */}
        <div className="bg-gradient-card border border-border shadow-card p-6 rounded-lg">
          <div className="flex justify-between items-center mb-4">
            <h2 className="text-xl font-semibold text-white">订单历史</h2>
            <button
              onClick={loadOrders}
              className="px-4 py-2 bg-dark-card text-white rounded-xl hover:bg-primary border border-border hover:shadow-glow-blue transition"
            >
              刷新
            </button>
          </div>

          <div className="overflow-x-auto">
            <table className="w-full text-sm">
              <thead className="bg-dark-light text-gray-300 border-b border-border">
                <tr>
                  <th className="px-4 py-2 text-left">时间</th>
                  <th className="px-4 py-2 text-left">股票</th>
                  <th className="px-4 py-2 text-left">方向</th>
                  <th className="px-4 py-2 text-right">数量</th>
                  <th className="px-4 py-2 text-right">价格</th>
                  <th className="px-4 py-2 text-right">手续费</th>
                  <th className="px-4 py-2 text-center">状态</th>
                </tr>
              </thead>
              <tbody className="text-gray-300">
                {orders.length === 0 ? (
                  <tr>
                    <td colSpan={7} className="px-4 py-8 text-center text-gray-500">
                      暂无订单
                    </td>
                  </tr>
                ) : (
                  orders.map((order) => (
                    <tr key={order.order_id} className="border-b border-border hover:bg-dark-light transition-colors">
                      <td className="px-4 py-2">
                        {new Date(order.submit_time).toLocaleString()}
                      </td>
                      <td className="px-4 py-2">{order.symbol}</td>
                      <td
                        className={`px-4 py-2 ${
                          order.action === 'BUY' ? 'text-bull' : 'text-bear'
                        }`}
                      >
                        {order.action === 'BUY' ? '买入' : '卖出'}
                      </td>
                      <td className="px-4 py-2 text-right">{order.quantity}</td>
                      <td className="px-4 py-2 text-right">
                        ¥{order.filled_price?.toFixed(2) || '-'}
                      </td>
                      <td className="px-4 py-2 text-right">¥{order.commission.toFixed(2)}</td>
                      <td className="px-4 py-2 text-center">
                        <span
                          className={`px-2 py-1 rounded text-xs ${
                            order.status === 'FILLED'
                              ? 'bg-bull/20 text-bull border border-bull/30'
                              : order.status === 'REJECTED'
                              ? 'bg-bear/20 text-bear border border-bear/30'
                              : 'bg-dark-light text-gray-300 border-b border-border'
                          }`}
                        >
                          {order.status}
                        </span>
                      </td>
                    </tr>
                  ))
                )}
              </tbody>
            </table>
          </div>
        </div>
      </div>
    </div>
  );
};

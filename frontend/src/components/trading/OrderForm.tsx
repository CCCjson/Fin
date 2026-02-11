import React, { useState } from 'react';
import { StockSymbolInput } from '../common/StockSymbolInput';

interface OrderFormProps {
  onSubmit: (symbol: string, action: string, quantity: number, price?: number) => void;
  loading?: boolean;
}

export const OrderForm: React.FC<OrderFormProps> = ({ onSubmit, loading }) => {
  const [symbol, setSymbol] = useState('688576.SH');
  const [action, setAction] = useState('BUY');
  const [quantity, setQuantity] = useState(100);
  const [price, setPrice] = useState('');
  const [orderType, setOrderType] = useState('MARKET');

  const handleSubmit = (e: React.FormEvent) => {
    e.preventDefault();
    const priceValue = orderType === 'LIMIT' && price ? parseFloat(price) : undefined;
    onSubmit(symbol, action, quantity, priceValue);
  };

  return (
    <form onSubmit={handleSubmit} className="space-y-4 bg-gradient-card p-6 rounded-xl border border-border shadow-card">
      <h3 className="text-xl font-semibold text-white mb-4">下单</h3>

      <div>
        <label className="block text-sm font-medium text-gray-300 mb-2">
          股票代码
        </label>
        <StockSymbolInput
          value={symbol}
          onChange={(s) => setSymbol(s)}
          placeholder="输入代码或名称搜索"
        />
      </div>

      <div>
        <label className="block text-sm font-medium text-gray-300 mb-2">
          买卖方向
        </label>
        <select
          value={action}
          onChange={(e) => setAction(e.target.value)}
          className="w-full px-3 py-2 bg-dark-light text-white rounded-lg border border-border focus:border-primary focus:ring-2 focus:ring-primary/20 outline-none transition-all"
        >
          <option value="BUY">买入</option>
          <option value="SELL">卖出</option>
        </select>
      </div>

      <div>
        <label className="block text-sm font-medium text-gray-300 mb-2">
          订单类型
        </label>
        <select
          value={orderType}
          onChange={(e) => setOrderType(e.target.value)}
          className="w-full px-3 py-2 bg-dark-light text-white rounded-lg border border-border focus:border-primary focus:ring-2 focus:ring-primary/20 outline-none transition-all"
        >
          <option value="MARKET">市价单</option>
          <option value="LIMIT">限价单</option>
        </select>
      </div>

      <div>
        <label className="block text-sm font-medium text-gray-300 mb-2">
          数量
        </label>
        <input
          type="number"
          value={quantity}
          onChange={(e) => setQuantity(parseInt(e.target.value))}
          min="1"
          step="1"
          className="w-full px-3 py-2 bg-dark-light text-white rounded-lg border border-border focus:border-primary focus:ring-2 focus:ring-primary/20 outline-none transition-all"
          required
        />
      </div>

      {orderType === 'LIMIT' && (
        <div>
          <label className="block text-sm font-medium text-gray-300 mb-2">
            价格
          </label>
          <input
            type="number"
            value={price}
            onChange={(e) => setPrice(e.target.value)}
            step="0.01"
            className="w-full px-3 py-2 bg-dark-light text-white rounded-lg border border-border focus:border-primary focus:ring-2 focus:ring-primary/20 outline-none transition-all"
            required
          />
        </div>
      )}

      <button
        type="submit"
        disabled={loading}
        className={`w-full py-3 rounded-xl font-semibold transition-all duration-200 ${
          action === 'BUY'
            ? 'bg-bull hover:bg-bull-dark text-white shadow-glow-green'
            : 'bg-bear hover:bg-bear-dark text-white shadow-glow-red'
        } ${loading ? 'opacity-50 cursor-not-allowed' : ''}`}
      >
        {loading ? '提交中...' : action === 'BUY' ? '买入' : '卖出'}
      </button>
    </form>
  );
};

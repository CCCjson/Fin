/**
 * Tab 5: 手动下单 — 复用 OrderForm + broker 选择器
 */
import React, { useState } from 'react';
import { useAutomationStore } from '../../stores/automationStore';
import { StockSymbolInput } from '../common/StockSymbolInput';

export const ManualOrderTab: React.FC = () => {
  const { brokerStatuses, submitManualOrder } = useAutomationStore();

  const [brokerType, setBrokerType] = useState('paper');
  const [symbol, setSymbol] = useState('');
  const [action, setAction] = useState('BUY');
  const [quantity, setQuantity] = useState(100);
  const [price, setPrice] = useState('');
  const [orderType, setOrderType] = useState('MARKET');
  const [loading, setLoading] = useState(false);
  const [message, setMessage] = useState<{ type: 'success' | 'error'; text: string } | null>(null);

  const brokerOnline = brokerStatuses[brokerType]?.online ?? false;

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!symbol) {
      setMessage({ type: 'error', text: '请输入股票代码' });
      return;
    }
    if (!brokerOnline && brokerType !== 'paper') {
      setMessage({ type: 'error', text: `${brokerStatuses[brokerType]?.name || brokerType} 未连接` });
      return;
    }

    setLoading(true);
    setMessage(null);

    const priceValue = orderType === 'LIMIT' && price ? parseFloat(price) : undefined;
    const result = await submitManualOrder({
      broker_type: brokerType,
      symbol,
      action,
      quantity,
      price: priceValue,
    });

    setLoading(false);
    setMessage({
      type: result.success ? 'success' : 'error',
      text: result.message,
    });

    if (result.success) {
      // 清空表单
      setSymbol('');
      setPrice('');
      setQuantity(100);
    }
  };

  const brokerOptions = [
    { value: 'paper', label: '模拟交易 (Paper)' },
    { value: 'easytrader', label: 'EasyTrader' },
  ];

  return (
    <div className="max-w-xl mx-auto">
      <form onSubmit={handleSubmit} className="bg-gradient-card rounded-xl border border-border shadow-card p-6 space-y-5">
        <h3 className="text-lg font-semibold text-white">手动下单</h3>

        {/* Broker 选择器 */}
        <div>
          <label className="block text-sm font-medium text-gray-300 mb-2">交易通道</label>
          <div className="grid grid-cols-3 gap-2">
            {brokerOptions.map((opt) => {
              const online = brokerStatuses[opt.value]?.online ?? (opt.value === 'paper');
              return (
                <button
                  key={opt.value}
                  type="button"
                  onClick={() => setBrokerType(opt.value)}
                  className={`px-3 py-2.5 rounded-lg text-xs font-medium transition-all border ${
                    brokerType === opt.value
                      ? 'border-primary bg-primary/15 text-primary'
                      : 'border-border bg-dark text-gray-400 hover:text-white hover:border-gray-600'
                  }`}
                >
                  <div className="flex items-center justify-center gap-1.5">
                    <div className={`w-1.5 h-1.5 rounded-full ${online ? 'bg-green-500' : 'bg-gray-600'}`} />
                    <span>{opt.label}</span>
                  </div>
                </button>
              );
            })}
          </div>
          {!brokerOnline && brokerType !== 'paper' && (
            <p className="text-xs text-red-400 mt-1">该交易通道当前离线</p>
          )}
        </div>

        {/* 股票代码 */}
        <div>
          <label className="block text-sm font-medium text-gray-300 mb-2">股票代码</label>
          <StockSymbolInput
            value={symbol}
            onChange={(s) => setSymbol(s)}
            placeholder="输入代码或名称搜索"
          />
        </div>

        {/* 买卖方向 */}
        <div>
          <label className="block text-sm font-medium text-gray-300 mb-2">买卖方向</label>
          <div className="grid grid-cols-2 gap-2">
            <button
              type="button"
              onClick={() => setAction('BUY')}
              className={`py-2.5 rounded-lg text-sm font-medium transition-all ${
                action === 'BUY'
                  ? 'bg-red-500/20 text-red-400 border border-red-500/30'
                  : 'bg-dark border border-border text-gray-400 hover:text-white'
              }`}
            >
              买入
            </button>
            <button
              type="button"
              onClick={() => setAction('SELL')}
              className={`py-2.5 rounded-lg text-sm font-medium transition-all ${
                action === 'SELL'
                  ? 'bg-green-500/20 text-green-400 border border-green-500/30'
                  : 'bg-dark border border-border text-gray-400 hover:text-white'
              }`}
            >
              卖出
            </button>
          </div>
        </div>

        {/* 订单类型 + 数量 */}
        <div className="grid grid-cols-2 gap-3">
          <div>
            <label className="block text-sm font-medium text-gray-300 mb-2">订单类型</label>
            <select
              value={orderType}
              onChange={(e) => setOrderType(e.target.value)}
              className="w-full px-3 py-2 bg-dark border border-border text-white rounded-lg text-sm focus:border-primary outline-none"
            >
              <option value="MARKET">市价单</option>
              <option value="LIMIT">限价单</option>
            </select>
          </div>
          <div>
            <label className="block text-sm font-medium text-gray-300 mb-2">数量</label>
            <input
              type="number"
              value={quantity}
              onChange={(e) => setQuantity(parseInt(e.target.value) || 0)}
              min={1}
              step={100}
              className="w-full px-3 py-2 bg-dark border border-border text-white rounded-lg text-sm focus:border-primary outline-none"
              required
            />
          </div>
        </div>

        {/* 限价 */}
        {orderType === 'LIMIT' && (
          <div>
            <label className="block text-sm font-medium text-gray-300 mb-2">价格</label>
            <input
              type="number"
              value={price}
              onChange={(e) => setPrice(e.target.value)}
              step="0.01"
              className="w-full px-3 py-2 bg-dark border border-border text-white rounded-lg text-sm focus:border-primary outline-none"
              required
            />
          </div>
        )}

        {/* 提交按钮 */}
        <button
          type="submit"
          disabled={loading || !symbol}
          className={`w-full py-3 rounded-xl font-semibold transition-all duration-200 ${
            action === 'BUY'
              ? 'bg-red-500/80 hover:bg-red-500 text-white'
              : 'bg-green-500/80 hover:bg-green-500 text-white'
          } ${loading || !symbol ? 'opacity-50 cursor-not-allowed' : ''}`}
        >
          {loading ? '提交中...' : action === 'BUY' ? '买入' : '卖出'}
        </button>

        {/* 消息提示 */}
        {message && (
          <div className={`p-3 rounded-lg text-sm ${
            message.type === 'success' ? 'bg-green-500/10 text-green-400' : 'bg-red-500/10 text-red-400'
          }`}>
            {message.text}
          </div>
        )}
      </form>
    </div>
  );
};

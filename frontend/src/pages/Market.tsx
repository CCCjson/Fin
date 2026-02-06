import React, { useState, useEffect } from 'react';
import { SimpleChart } from '../components/charts/SimpleChart';
import { marketService } from '../services/marketService';
import type { StockData } from '../types';

export const Market: React.FC = () => {
  const [symbol, setSymbol] = useState('688576.SH');
  const [data, setData] = useState<StockData[]>([]);
  const [loading, setLoading] = useState(false);
  const [startDate, setStartDate] = useState('2025-01-01');
  const [endDate, setEndDate] = useState('2025-12-31');

  useEffect(() => {
    loadData();
  }, []);

  const loadData = async () => {
    try {
      setLoading(true);
      const response = await marketService.getDailyData(symbol, startDate, endDate);
      setData(response.data || []);
    } catch (error) {
      console.error('Failed to load data:', error);
    } finally {
      setLoading(false);
    }
  };

  const latestData = data[data.length - 1];

  return (
    <div className="min-h-screen bg-gradient-dark p-6">
      <div className="max-w-7xl mx-auto space-y-6">
        <h1 className="text-3xl font-bold text-white">行情数据</h1>

        {/* 查询表单 */}
        <div className="bg-gradient-card border border-border shadow-card p-6 rounded-lg">
          <div className="grid grid-cols-1 md:grid-cols-4 gap-4">
            <div>
              <label className="block text-sm font-medium text-gray-300 mb-2">
                股票代码
              </label>
              <input
                type="text"
                value={symbol}
                onChange={(e) => setSymbol(e.target.value)}
                className="w-full px-3 py-2 bg-dark-light text-white rounded-lg border border-border focus:border-primary focus:ring-2 focus:ring-primary/20 outline-none"
              />
            </div>
            <div>
              <label className="block text-sm font-medium text-gray-300 mb-2">
                开始日期
              </label>
              <input
                type="date"
                value={startDate}
                onChange={(e) => setStartDate(e.target.value)}
                className="w-full px-3 py-2 bg-dark-light text-white rounded-lg border border-border focus:border-primary focus:ring-2 focus:ring-primary/20 outline-none"
              />
            </div>
            <div>
              <label className="block text-sm font-medium text-gray-300 mb-2">
                结束日期
              </label>
              <input
                type="date"
                value={endDate}
                onChange={(e) => setEndDate(e.target.value)}
                className="w-full px-3 py-2 bg-dark-light text-white rounded-lg border border-border focus:border-primary focus:ring-2 focus:ring-primary/20 outline-none"
              />
            </div>
            <div className="flex items-end">
              <button
                onClick={loadData}
                disabled={loading}
                className="w-full px-4 py-2 bg-primary text-white rounded-xl hover:bg-primary-dark shadow-glow-blue transition disabled:opacity-50"
              >
                {loading ? '加载中...' : '查询'}
              </button>
            </div>
          </div>
        </div>

        {/* 最新数据 */}
        {latestData && (
          <div className="grid grid-cols-2 md:grid-cols-5 gap-4">
            <div className="bg-gradient-card border border-border shadow-card p-4 rounded-xl hover:shadow-glow-blue transition-all">
              <div className="text-gray-400 text-sm mb-1">最新价</div>
              <div className="text-2xl font-bold text-primary-light">¥{latestData.close.toFixed(2)}</div>
            </div>
            <div className="bg-gradient-card border border-border shadow-card p-4 rounded-xl hover:shadow-glow-blue transition-all">
              <div className="text-gray-400 text-sm mb-1">开盘价</div>
              <div className="text-xl font-semibold text-white">¥{latestData.open.toFixed(2)}</div>
            </div>
            <div className="bg-gradient-card border border-border shadow-card p-4 rounded-xl hover:shadow-glow-green transition-all">
              <div className="text-gray-400 text-sm mb-1">最高价</div>
              <div className="text-xl font-semibold text-bull">¥{latestData.high.toFixed(2)}</div>
            </div>
            <div className="bg-gradient-card border border-border shadow-card p-4 rounded-xl hover:shadow-glow-red transition-all">
              <div className="text-gray-400 text-sm mb-1">最低价</div>
              <div className="text-xl font-semibold text-bear">¥{latestData.low.toFixed(2)}</div>
            </div>
            <div className="bg-gradient-card border border-border shadow-card p-4 rounded-xl hover:shadow-glow-blue transition-all">
              <div className="text-gray-400 text-sm mb-1">成交量</div>
              <div className="text-xl font-semibold text-accent-cyan">{(latestData.volume / 10000).toFixed(2)}万</div>
            </div>
          </div>
        )}

        {/* K线图 */}
        <div className="bg-gradient-card border border-border shadow-card p-6 rounded-lg">
          <h2 className="text-xl font-semibold text-white mb-4">价格走势</h2>
          {data.length > 0 ? (
            <SimpleChart data={data} height={400} />
          ) : (
            <div className="h-96 flex items-center justify-center text-gray-400">
              {loading ? '加载中...' : '暂无数据'}
            </div>
          )}
        </div>

        {/* 数据表格 */}
        <div className="bg-gradient-card border border-border shadow-card p-6 rounded-lg">
          <h2 className="text-xl font-semibold text-white mb-4">历史数据</h2>
          <div className="overflow-x-auto">
            <table className="w-full text-sm">
              <thead className="bg-dark-light text-gray-300 border-b border-border">
                <tr>
                  <th className="px-4 py-2 text-left">日期</th>
                  <th className="px-4 py-2 text-right">开盘</th>
                  <th className="px-4 py-2 text-right">最高</th>
                  <th className="px-4 py-2 text-right">最低</th>
                  <th className="px-4 py-2 text-right">收盘</th>
                  <th className="px-4 py-2 text-right">成交量</th>
                </tr>
              </thead>
              <tbody className="text-gray-300">
                {data.slice(-20).reverse().map((item, idx) => (
                  <tr key={idx} className="border-b border-border hover:bg-dark-light transition-colors">
                    <td className="px-4 py-2">{item.date}</td>
                    <td className="px-4 py-2 text-right">¥{item.open.toFixed(2)}</td>
                    <td className="px-4 py-2 text-right text-bull font-medium">¥{item.high.toFixed(2)}</td>
                    <td className="px-4 py-2 text-right text-bear font-medium">¥{item.low.toFixed(2)}</td>
                    <td className="px-4 py-2 text-right font-semibold text-primary-light">¥{item.close.toFixed(2)}</td>
                    <td className="px-4 py-2 text-right text-accent-cyan">{(item.volume / 10000).toFixed(2)}万</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      </div>
    </div>
  );
};

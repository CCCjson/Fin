import React, { useEffect, useState } from 'react';
import { AccountInfo } from '../components/trading/AccountInfo';
import { tradingService } from '../services/tradingService';
import type { Account } from '../types';

export const Dashboard: React.FC = () => {
  const [account, setAccount] = useState<Account | null>(null);
  const [loading, setLoading] = useState(true);
  const [initialized, setInitialized] = useState(false);

  useEffect(() => {
    loadAccount();
  }, []);

  const loadAccount = async () => {
    try {
      setLoading(true);
      const data = await tradingService.getAccount();
      setAccount(data);
      setInitialized(true);
    } catch (error) {
      console.log('Account not initialized');
      setInitialized(false);
    } finally {
      setLoading(false);
    }
  };

  const initializeAccount = async () => {
    try {
      setLoading(true);
      await tradingService.initAccount(1000000);
      await loadAccount();
    } catch (error) {
      console.error('Failed to initialize account:', error);
    }
  };

  if (loading) {
    return (
      <div className="flex items-center justify-center h-screen">
        <div className="text-white text-xl">加载中...</div>
      </div>
    );
  }

  if (!initialized) {
    return (
      <div className="flex flex-col items-center justify-center h-screen space-y-6 bg-gradient-dark">
        <div className="text-center space-y-4">
          <h1 className="text-4xl font-bold text-white mb-2">欢迎使用量化交易系统</h1>
          <p className="text-gray-400 text-lg">请先初始化您的交易账户</p>
        </div>
        <button
          onClick={initializeAccount}
          className="px-8 py-4 bg-primary text-white rounded-xl hover:bg-primary-dark transition-all duration-200 shadow-glow-blue hover:shadow-glow-blue font-semibold text-lg"
        >
          初始化账户（初始资金：100万）
        </button>
      </div>
    );
  }

  return (
    <div className="min-h-screen bg-gradient-dark p-6">
      <div className="max-w-7xl mx-auto space-y-6">
        <div className="flex justify-between items-center">
          <h1 className="text-3xl font-bold text-white">量化交易仪表盘</h1>
          <button
            onClick={loadAccount}
            className="px-4 py-2 bg-dark-card text-white rounded-lg hover:bg-primary transition-all duration-200 border border-border hover:shadow-glow-blue"
          >
            刷新
          </button>
        </div>

        <AccountInfo account={account} />

        <div className="grid grid-cols-1 md:grid-cols-3 gap-4">
          <div className="bg-gradient-card p-6 rounded-xl border border-border shadow-card hover:shadow-glow-green transition-all duration-300">
            <div className="text-gray-400 text-sm mb-2">本日盈亏</div>
            <div className="text-3xl font-bold text-bull">+¥0.00</div>
          </div>
          <div className="bg-gradient-card p-6 rounded-xl border border-border shadow-card hover:shadow-glow-blue transition-all duration-300">
            <div className="text-gray-400 text-sm mb-2">持仓数量</div>
            <div className="text-3xl font-bold text-white">
              {account?.positions?.length || 0}
            </div>
          </div>
          <div className="bg-gradient-card p-6 rounded-xl border border-border shadow-card hover:shadow-glow-blue transition-all duration-300">
            <div className="text-gray-400 text-sm mb-2">总收益率</div>
            <div className="text-3xl font-bold text-primary-light">0.00%</div>
          </div>
        </div>
      </div>
    </div>
  );
};

import React from 'react';
import { CppBacktestPanel } from '../components/backtest/CppBacktestPanel';

// 回测页 —— 域5 阶段④退役 Python 引擎后，全站只剩 C++ 一套回测口径。
// 原「Python / C++」双 Tab 已收敛为直接渲染 C++ 面板（单次回测 + 批量回测）。

export const Backtest: React.FC = () => {
  return (
    <div className="min-h-screen bg-gradient-dark p-3 md:p-6 pb-20 md:pb-6">
      <div className="max-w-7xl mx-auto space-y-4 md:space-y-6">
        <h1 className="text-2xl md:text-3xl font-bold text-white">策略回测</h1>
        <CppBacktestPanel />
      </div>
    </div>
  );
};

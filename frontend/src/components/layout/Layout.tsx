import React from 'react';
import { Outlet, Link, useLocation } from 'react-router-dom';

export const Layout: React.FC = () => {
  const location = useLocation();

  const navItems = [
    { path: '/dashboard', label: '仪表盘', icon: '📊' },
    { path: '/market', label: '行情', icon: '📈' },
    { path: '/trading', label: '交易', icon: '💱' },
    { path: '/signals', label: '信号分析', icon: '🎯' },
    { path: '/backtest', label: '策略回测', icon: '🔬' },
  ];

  const isActive = (path: string) => location.pathname === path;

  return (
    <div className="flex h-screen bg-dark">
      {/* 侧边栏 */}
      <aside className="w-64 bg-dark-card border-r border-border">
        <div className="p-6 border-b border-border">
          <h1 className="text-2xl font-bold text-white mb-2 bg-gradient-to-r from-primary-light to-accent-cyan bg-clip-text text-transparent">
            量化交易系统
          </h1>
          <p className="text-gray-400 text-sm">Quantitative Trading</p>
        </div>

        <nav className="px-3 py-4 space-y-2">
          {navItems.map((item) => (
            <Link
              key={item.path}
              to={item.path}
              className={`flex items-center px-4 py-3 rounded-xl transition-all duration-200 ${
                isActive(item.path)
                  ? 'bg-primary text-white shadow-glow-blue'
                  : 'text-gray-400 hover:bg-dark-light hover:text-white'
              }`}
            >
              <span className="mr-3 text-xl">{item.icon}</span>
              <span className="font-medium">{item.label}</span>
            </Link>
          ))}
        </nav>

        <div className="absolute bottom-0 left-0 right-0 w-64 p-4 border-t border-border bg-dark-card">
          <div className="text-xs text-gray-500 text-center">
            Version 1.0.0
          </div>
        </div>
      </aside>

      {/* 主内容区 */}
      <main className="flex-1 overflow-auto bg-dark-lighter">
        <Outlet />
      </main>
    </div>
  );
};

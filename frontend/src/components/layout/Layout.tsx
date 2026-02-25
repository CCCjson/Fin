import React, { useState, useEffect, Suspense } from 'react';
import { Link, useLocation, Navigate } from 'react-router-dom';

// 路由懒加载 — 按页面拆分 chunk，首屏只加载当前页面的代码
const Dashboard = React.lazy(() => import('../../pages/Dashboard').then(m => ({ default: m.Dashboard })));
const Market = React.lazy(() => import('../../pages/Market').then(m => ({ default: m.Market })));
const Trading = React.lazy(() => import('../../pages/Trading').then(m => ({ default: m.Trading })));
const Signals = React.lazy(() => import('../../pages/Signals').then(m => ({ default: m.Signals })));
const Backtest = React.lazy(() => import('../../pages/Backtest').then(m => ({ default: m.Backtest })));
const Realtime = React.lazy(() => import('../../pages/Realtime').then(m => ({ default: m.Realtime })));
const Reports = React.lazy(() => import('../../pages/Reports').then(m => ({ default: m.Reports })));
const SignalTracking = React.lazy(() => import('../../pages/SignalTracking').then(m => ({ default: m.SignalTracking })));
const Portfolio = React.lazy(() => import('../../pages/Portfolio').then(m => ({ default: m.Portfolio })));
const Review = React.lazy(() => import('../../pages/Review').then(m => ({ default: m.Review })));
const Advisor = React.lazy(() => import('../../pages/Advisor').then(m => ({ default: m.Advisor })));
const OrderBook = React.lazy(() => import('../../pages/OrderBook').then(m => ({ default: m.OrderBook })));

const routeConfig: { path: string; Component: React.LazyExoticComponent<React.FC> }[] = [
  { path: '/dashboard', Component: Dashboard },
  { path: '/realtime', Component: Realtime },
  { path: '/market', Component: Market },
  { path: '/trading', Component: Trading },
  { path: '/signals', Component: Signals },
  { path: '/tracking', Component: SignalTracking },
  { path: '/backtest', Component: Backtest },
  { path: '/reports', Component: Reports },
  { path: '/portfolio', Component: Portfolio },
  { path: '/review', Component: Review },
  { path: '/advisor', Component: Advisor },
  { path: '/orderbook', Component: OrderBook },
];

const PageLoader: React.FC = () => (
  <div className="flex items-center justify-center h-full">
    <div className="text-gray-400 flex items-center gap-3">
      <svg className="animate-spin h-5 w-5 text-primary" xmlns="http://www.w3.org/2000/svg" fill="none" viewBox="0 0 24 24">
        <circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4"></circle>
        <path className="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4zm2 5.291A7.962 7.962 0 014 12H0c0 3.042 1.135 5.824 3 7.938l3-2.647z"></path>
      </svg>
      <span>Loading...</span>
    </div>
  </div>
);

export const Layout: React.FC = () => {
  const location = useLocation();
  const [mountedPaths, setMountedPaths] = useState<Set<string>>(new Set());

  const currentPath = location.pathname;

  useEffect(() => {
    setMountedPaths((prev) => {
      if (prev.has(currentPath)) return prev;
      const next = new Set(prev);
      next.add(currentPath);
      return next;
    });
  }, [currentPath]);

  const navItems = [
    { path: '/realtime', label: '实时行情', icon: '⚡' },
    { path: '/market', label: 'K线分析', icon: '📈' },
    { path: '/portfolio', label: '交易记录', icon: '📒' },
    { path: '/review', label: '每日复盘', icon: '📝' },
    { path: '/signals', label: '信号分析', icon: '🎯' },
    { path: '/tracking', label: '信号追踪', icon: '📋' },
    { path: '/backtest', label: '策略回测', icon: '🔬' },
    { path: '/reports', label: 'AI 报告', icon: '🤖' },
    { path: '/advisor', label: 'AI 顾问', icon: '💬' },
    { path: '/orderbook', label: '订单簿', icon: '📊' },
  ];

  const isActive = (path: string) => currentPath === path;

  // Redirect / to /realtime
  if (currentPath === '/') {
    return <Navigate to="/realtime" replace />;
  }

  return (
    <div className="flex h-screen bg-dark">
      {/* 侧边栏：flex 子元素，hover 时展开推挤内容 */}
      <aside className="group/sidebar flex-shrink-0 h-full z-30 flex flex-col bg-dark-card border-r border-border w-16 hover:w-64 transition-all duration-300 overflow-hidden">
        {/* Logo */}
        <div className="border-b border-border p-3 group-hover/sidebar:p-6 transition-all duration-300">
          <div className="group-hover/sidebar:hidden text-2xl text-center text-primary-light font-bold">Q</div>
          <div className="hidden group-hover/sidebar:block">
            <h1 className="text-2xl font-bold text-white mb-2 bg-gradient-to-r from-primary-light to-accent-cyan bg-clip-text text-transparent whitespace-nowrap">
              量化交易系统
            </h1>
            <p className="text-gray-400 text-sm whitespace-nowrap">Quantitative Trading</p>
          </div>
        </div>

        {/* 导航 */}
        <nav className="flex-1 py-4 space-y-2 overflow-y-auto px-1.5 group-hover/sidebar:px-3 transition-all duration-300">
          {navItems.map((item) => (
            <Link
              key={item.path}
              to={item.path}
              title={item.label}
              className={`flex items-center justify-center group-hover/sidebar:justify-start px-0 group-hover/sidebar:px-4 py-3 rounded-xl transition-all duration-200 ${
                isActive(item.path)
                  ? 'bg-primary text-white shadow-glow-blue'
                  : 'text-gray-400 hover:bg-dark-light hover:text-white'
              }`}
            >
              <span className="text-xl flex-shrink-0 group-hover/sidebar:mr-3 transition-all duration-200">{item.icon}</span>
              <span className="font-medium hidden group-hover/sidebar:inline whitespace-nowrap">{item.label}</span>
            </Link>
          ))}
        </nav>

        {/* 底部版本号 */}
        <div className="p-4 border-t border-border">
          <div className="text-xs text-gray-500 text-center hidden group-hover/sidebar:block whitespace-nowrap">
            Version 1.0.0
          </div>
        </div>
      </aside>

      {/* 主内容区 — 不设 overflow-auto，滚动交给各页面 wrapper */}
      <main className="flex-1 min-w-0 bg-dark-lighter relative">
        {routeConfig.map(({ path, Component }) => {
          if (!mountedPaths.has(path)) return null;
          const visible = path === currentPath;
          return (
            <div
              key={path}
              className={visible ? 'h-full overflow-auto' : 'hidden'}
            >
              <Suspense fallback={<PageLoader />}>
                <Component />
              </Suspense>
            </div>
          );
        })}
      </main>
    </div>
  );
};

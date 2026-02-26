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
const DataPipeline = React.lazy(() => import('../../pages/DataPipeline').then(m => ({ default: m.DataPipeline })));
const Prediction = React.lazy(() => import('../../pages/Prediction').then(m => ({ default: m.Prediction })));
const News = React.lazy(() => import('../../pages/News').then(m => ({ default: m.News })));

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
  { path: '/pipeline', Component: DataPipeline },
  { path: '/prediction', Component: Prediction },
  { path: '/news', Component: News },
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
  const [moreDrawerOpen, setMoreDrawerOpen] = useState(false);

  const currentPath = location.pathname;

  useEffect(() => {
    setMountedPaths((prev) => {
      if (prev.has(currentPath)) return prev;
      const next = new Set(prev);
      next.add(currentPath);
      return next;
    });
  }, [currentPath]);

  // 切换页面时关闭抽屉
  useEffect(() => {
    setMoreDrawerOpen(false);
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
    { path: '/pipeline', label: '数据管道', icon: '🚀' },
    { path: '/prediction', label: '股价预测', icon: '🔮' },
    { path: '/news', label: '新闻分析', icon: '📰' },
  ];

  // 底部 Tab 栏固定显示的 4 个 + 更多
  const mobileTabItems = [
    { path: '/realtime', label: '行情', icon: '⚡' },
    { path: '/market', label: 'K线', icon: '📈' },
    { path: '/signals', label: '信号', icon: '🎯' },
    { path: '/advisor', label: '顾问', icon: '💬' },
  ];

  // 「更多」抽屉里的其他页面
  const moreItems = navItems.filter(
    (item) => !mobileTabItems.some((tab) => tab.path === item.path)
  );

  const isActive = (path: string) => currentPath === path;
  const isInMore = moreItems.some((item) => item.path === currentPath);

  // Redirect / to /realtime
  if (currentPath === '/') {
    return <Navigate to="/realtime" replace />;
  }

  return (
    <div className="flex h-screen bg-dark">
      {/* ========== 桌面端侧边栏 ========== */}
      <aside className="hidden md:flex group/sidebar flex-shrink-0 h-full z-30 flex-col bg-dark-card border-r border-border w-16 hover:w-64 transition-all duration-300 overflow-hidden">
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
            Version 1.4.0
          </div>
        </div>
      </aside>

      {/* ========== 主内容区 ========== */}
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

      {/* ========== 手机端底部 Tab 栏 ========== */}
      <nav className="md:hidden fixed bottom-0 left-0 right-0 z-50 bg-dark-card border-t border-border">
        <div className="flex items-center justify-around h-14">
          {mobileTabItems.map((item) => (
            <Link
              key={item.path}
              to={item.path}
              className={`flex flex-col items-center justify-center flex-1 h-full transition-colors ${
                isActive(item.path)
                  ? 'text-primary'
                  : 'text-gray-500 active:text-gray-300'
              }`}
            >
              <span className="text-lg">{item.icon}</span>
              <span className="text-[10px] mt-0.5 font-medium">{item.label}</span>
            </Link>
          ))}

          {/* 更多按钮 */}
          <button
            onClick={() => setMoreDrawerOpen(!moreDrawerOpen)}
            className={`flex flex-col items-center justify-center flex-1 h-full transition-colors ${
              moreDrawerOpen || isInMore
                ? 'text-primary'
                : 'text-gray-500 active:text-gray-300'
            }`}
          >
            <svg className="w-5 h-5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M4 6h16M4 12h16M4 18h16" />
            </svg>
            <span className="text-[10px] mt-0.5 font-medium">更多</span>
          </button>
        </div>
        {/* iPhone 安全区 */}
        <div className="h-[env(safe-area-inset-bottom)] bg-dark-card" />
      </nav>

      {/* ========== 更多抽屉 ========== */}
      {moreDrawerOpen && (
        <>
          {/* 背景遮罩 */}
          <div
            className="md:hidden fixed inset-0 bg-black/60 z-40"
            onClick={() => setMoreDrawerOpen(false)}
          />
          {/* 抽屉面板 */}
          <div className="md:hidden fixed bottom-[calc(3.5rem+env(safe-area-inset-bottom))] left-0 right-0 z-40 bg-dark-card border-t border-border rounded-t-2xl overflow-hidden">
            <div className="flex justify-center pt-2 pb-1">
              <div className="w-10 h-1 bg-gray-600 rounded-full" />
            </div>
            <div className="grid grid-cols-4 gap-1 px-3 pb-4">
              {moreItems.map((item) => (
                <Link
                  key={item.path}
                  to={item.path}
                  onClick={() => setMoreDrawerOpen(false)}
                  className={`flex flex-col items-center justify-center py-3 rounded-xl transition-colors ${
                    isActive(item.path)
                      ? 'bg-primary/20 text-primary'
                      : 'text-gray-400 active:bg-dark-light'
                  }`}
                >
                  <span className="text-2xl mb-1">{item.icon}</span>
                  <span className="text-xs font-medium">{item.label}</span>
                </Link>
              ))}
            </div>
          </div>
        </>
      )}
    </div>
  );
};

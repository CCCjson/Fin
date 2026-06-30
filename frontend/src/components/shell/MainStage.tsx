import React, { Suspense, useEffect, useState } from 'react';
import { useLocation } from 'react-router-dom';
import { ChatThread } from '../moneybill/ChatThread';
import { SkeletonMetricGrid, SkeletonCard } from '../common/Skeleton';

/* 工具页懒加载（与原 Layout/App 一致的命名导出） */
const Dashboard = React.lazy(() => import('../../pages/Dashboard').then((m) => ({ default: m.Dashboard })));
const Market = React.lazy(() => import('../../pages/Market').then((m) => ({ default: m.Market })));
const Signals = React.lazy(() => import('../../pages/Signals').then((m) => ({ default: m.Signals })));
const Backtest = React.lazy(() => import('../../pages/Backtest').then((m) => ({ default: m.Backtest })));
const Realtime = React.lazy(() => import('../../pages/Realtime').then((m) => ({ default: m.Realtime })));
const Reports = React.lazy(() => import('../../pages/Reports').then((m) => ({ default: m.Reports })));
const SignalTracking = React.lazy(() => import('../../pages/SignalTracking').then((m) => ({ default: m.SignalTracking })));
const Portfolio = React.lazy(() => import('../../pages/Portfolio').then((m) => ({ default: m.Portfolio })));
const Review = React.lazy(() => import('../../pages/Review').then((m) => ({ default: m.Review })));
const Advisor = React.lazy(() => import('../../pages/Advisor').then((m) => ({ default: m.Advisor })));
const OrderBook = React.lazy(() => import('../../pages/OrderBook').then((m) => ({ default: m.OrderBook })));
const DataPipeline = React.lazy(() => import('../../pages/DataPipeline').then((m) => ({ default: m.DataPipeline })));
const Prediction = React.lazy(() => import('../../pages/Prediction').then((m) => ({ default: m.Prediction })));
const News = React.lazy(() => import('../../pages/News').then((m) => ({ default: m.News })));
const Cockpit = React.lazy(() => import('../../pages/Cockpit').then((m) => ({ default: m.Cockpit })));
const Watchlist = React.lazy(() => import('../../pages/Watchlist').then((m) => ({ default: m.Watchlist })));
const Screener = React.lazy(() => import('../../pages/Screener').then((m) => ({ default: m.Screener })));
const Automation = React.lazy(() => import('../../pages/Automation').then((m) => ({ default: m.Automation })));
const AlphaLab = React.lazy(() => import('../../pages/AlphaLab').then((m) => ({ default: m.AlphaLab })));
const FineTune = React.lazy(() => import('../../pages/FineTune').then((m) => ({ default: m.FineTune })));
const Knowledge = React.lazy(() => import('../../pages/Knowledge').then((m) => ({ default: m.Knowledge })));

const PAGES: Record<string, React.LazyExoticComponent<React.FC>> = {
  '/app/dashboard': Dashboard,
  '/app/realtime': Realtime,
  '/app/market': Market,
  '/app/signals': Signals,
  '/app/tracking': SignalTracking,
  '/app/backtest': Backtest,
  '/app/reports': Reports,
  '/app/portfolio': Portfolio,
  '/app/review': Review,
  '/app/advisor': Advisor,
  '/app/orderbook': OrderBook,
  '/app/pipeline': DataPipeline,
  '/app/prediction': Prediction,
  '/app/news': News,
  '/app/cockpit': Cockpit,
  '/app/watchlist': Watchlist,
  '/app/screener': Screener,
  '/app/knowledge': Knowledge,
  '/trading': Automation,
  '/alpha-lab': AlphaLab,
  '/fine-tune': FineTune,
};

const PageLoader: React.FC = () => (
  <div className="max-w-7xl mx-auto p-6 space-y-6">
    <SkeletonMetricGrid count={4} />
    <SkeletonCard />
    <SkeletonCard />
  </div>
);

export const MainStage: React.FC = () => {
  const { pathname } = useLocation();
  const isChat = pathname === '/' || pathname === '/app';
  const activeTool = PAGES[pathname] ? pathname : null;
  const showChat = isChat || !activeTool; // 未知路径兜底回聊天

  const [mounted, setMounted] = useState<Set<string>>(new Set());
  useEffect(() => {
    if (activeTool && !mounted.has(activeTool)) {
      setMounted((prev) => new Set(prev).add(activeTool));
    }
  }, [activeTool, mounted]);

  return (
    <div className="relative flex-1 h-full overflow-hidden">
      {/* 聊天层（始终挂载，保活） */}
      <div
        className={`absolute inset-0 transition-opacity duration-200 ${
          showChat ? 'opacity-100 z-10' : 'opacity-0 z-0 pointer-events-none'
        }`}
      >
        <ChatThread />
      </div>

      {/* 工具层（访问过即常驻，隐藏切换） */}
      {Array.from(mounted).map((path) => {
        const Comp = PAGES[path];
        const visible = path === activeTool && !isChat;
        return (
          <div
            key={path}
            className={`absolute inset-0 overflow-y-auto transition-opacity duration-200 ${
              visible ? 'opacity-100 z-10' : 'opacity-0 z-0 pointer-events-none'
            }`}
          >
            <Suspense fallback={<PageLoader />}>
              <Comp />
            </Suspense>
          </div>
        );
      })}
    </div>
  );
};

export default MainStage;

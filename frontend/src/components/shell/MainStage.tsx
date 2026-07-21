import React, { Suspense, useEffect, useState } from 'react';
import { useLocation } from 'react-router-dom';
import { ChatThread } from '../moneybill/ChatThread';
import { SkeletonMetricGrid, SkeletonCard } from '../common/Skeleton';

/*
 * 工具页懒加载 —— 架构收敛后只保留 8 个重可视化/实时交互工作台，
 * 其余能力（信号/筛股/自选/复盘/报告/新闻/知识库/设置/交易记录…）
 * 全部通过 MoneyBill 对话完成。
 */
const Market = React.lazy(() => import('../../pages/Market').then((m) => ({ default: m.Market })));
const Backtest = React.lazy(() => import('../../pages/Backtest').then((m) => ({ default: m.Backtest })));
const OrderBook = React.lazy(() => import('../../pages/OrderBook').then((m) => ({ default: m.OrderBook })));
const Prediction = React.lazy(() => import('../../pages/Prediction').then((m) => ({ default: m.Prediction })));
const DataMonitor = React.lazy(() => import('../../pages/DataMonitor').then((m) => ({ default: m.DataMonitor })));
const Automation = React.lazy(() => import('../../pages/Automation').then((m) => ({ default: m.Automation })));
const FineTune = React.lazy(() => import('../../pages/FineTune').then((m) => ({ default: m.FineTune })));
const Settings = React.lazy(() => import('../../pages/Settings').then((m) => ({ default: m.Settings })));

const PAGES: Record<string, React.LazyExoticComponent<React.FC>> = {
  '/app/market': Market,
  '/app/backtest': Backtest,
  '/app/orderbook': OrderBook,
  '/app/prediction': Prediction,
  '/app/data-monitor': DataMonitor,
  '/trading': Automation,
  '/fine-tune': FineTune,
  '/app/settings': Settings,
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
  const showChat = isChat || !activeTool; // 未知/已退役路径兜底回聊天

  const [mounted, setMounted] = useState<Set<string>>(new Set());
  useEffect(() => {
    if (activeTool && !mounted.has(activeTool)) {
      setMounted((prev) => new Set(prev).add(activeTool));
    }
  }, [activeTool, mounted]);

  return (
    <div id="main-stage" className="relative flex-1 h-full overflow-hidden">
      {/* 聊天层（始终挂载，保活） */}
      <div
        data-page-visible={showChat}
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
            data-page-visible={visible}
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

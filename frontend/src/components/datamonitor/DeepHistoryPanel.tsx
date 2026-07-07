import React, { useCallback, useEffect, useRef, useState } from 'react';
import { Card } from '../common/Card';
import { Button } from '../common/Button';
import { deepHistoryService } from '../../services/deepHistoryService';
import { AShareDeepHistoryPanel } from './AShareDeepHistoryPanel';
import { OverseasDeepHistoryPanel } from './OverseasDeepHistoryPanel';

/* ──────────────────────────────────────────────────────────────
   深历史日线回补合并面板（A股 + 港股 + 美股 一个卡片里）

   A股 和 港股/美股 是两条独立后端任务（不同数据源、不同限速域），可以真正
   同时跑；但港股/美股共用同一个 yfinance 限速配额，不能同时跑，所以"一键
   全部开始"的行为是：A股 立即开始 + 港股立即开始，美股在港股跑完后自动接上
   （轮询检测 overseas 任务 market=hk_stock 变成 done 就自动续跑 us_stock）。
   ────────────────────────────────────────────────────────────── */

const CHAIN_POLL_MS = 3000;

export const DeepHistoryPanel: React.FC = () => {
  const [busy, setBusy] = useState(false);
  const [pendingUSChain, setPendingUSChain] = useState(false);
  const chainIntervalRef = useRef<ReturnType<typeof setInterval> | undefined>(undefined);

  // 轻量单独轮询：只用来判断"港股跑完了没"，触发美股自动接上
  useEffect(() => {
    if (!pendingUSChain) return;
    const check = async () => {
      try {
        const s = await deepHistoryService.getOverseasStatus();
        if (s.market === 'hk_stock' && (s.status === 'done' || s.status === 'stopped')) {
          setPendingUSChain(false);
          await deepHistoryService.startOverseas({ market: 'us_stock', batch_size: 50 });
        }
      } catch {
        /* 静默失败，不打断轮询 */
      }
    };
    chainIntervalRef.current = setInterval(check, CHAIN_POLL_MS);
    return () => {
      if (chainIntervalRef.current) clearInterval(chainIntervalRef.current);
    };
  }, [pendingUSChain]);

  const handleStartAll = useCallback(async () => {
    setBusy(true);
    try {
      await Promise.all([
        deepHistoryService.startAShare({}),
        deepHistoryService.startOverseas({ market: 'hk_stock', batch_size: 50 }),
      ]);
      setPendingUSChain(true);
    } finally {
      setBusy(false);
    }
  }, []);

  return (
    <Card className="p-4">
      <div className="flex items-center justify-between mb-1">
        <span className="text-sm text-white font-medium">🗄️ 深历史日线回补</span>
        <Button variant="subtle" size="sm" loading={busy} onClick={handleStartAll}>
          一键全部开始
        </Button>
      </div>
      <div className="text-[11px] text-gray-500 mb-4">
        A股 和 港股会立即同时开始（互不干扰）；美股会在港股跑完后自动接上开始
        {pendingUSChain && <span className="text-primary">（等待港股跑完，自动续跑美股中…）</span>}
      </div>

      <div className="space-y-4">
        <AShareDeepHistoryPanel bare />
        <div className="border-t border-border/50" />
        <OverseasDeepHistoryPanel bare />
      </div>
    </Card>
  );
};

export default DeepHistoryPanel;

import React from 'react';
import type { StrategyPnl } from '../../services/arenaService';

/**
 * 盈亏数字 —— **永远带着它的 basis 一起显示**（S7）。
 *
 * 🔴 抽成独立组件是刻意的：项目里有一条反复出现的铁律 ——
 * 「`basis` 一定要跟着数字一起读」。paper 是**理想撮合**（挂单必成、零冲击、零排队），
 * 把 `+8%` 单独摆出来而不说它是模拟的，等于拿模拟成绩给真钱决策背书。
 * 让每块面板各自记得这件事是靠不住的，所以做成**唯一的渲染入口**。
 *
 * ⛔ 别在别处直接渲染 `pnl.realized_pnl`。
 *
 * ⚠️ `windowDays` 同理：这个数是**窗口内**的已实现盈亏，不是「开仓以来」。
 * 不写窗口的话，它会和旁边 90 天口径的门槛判定摆在同一行互相打架。
 */
export const PnlValue: React.FC<{
  pnl?: StrategyPnl | null;
  windowDays?: number;
}> = ({ pnl, windowDays }) => {
  const win = windowDays ? <span className="text-xs text-gray-500">近 {windowDays} 天</span> : null;

  if (!pnl || pnl.basis === 'none') {
    return (
      <span className="text-gray-500" title={pnl?.reason}>
        暂无{pnl?.reason ? ' ⓘ' : ''}
      </span>
    );
  }

  if (pnl.basis === 'mixed') {
    // 两块**不可相加也不可比大小**（后端 note 里也这么写），所以分开摆
    const live = pnl.live?.realized_pnl ?? 0;
    const paper = pnl.paper?.realized_pnl ?? 0;
    return (
      <span className="flex flex-col leading-tight">
        <Amount v={live} suffix=" USDT" real />
        <span className="text-xs text-gray-500">另有模拟账 {fmt(paper)}（不可相加）</span>
        {win}
      </span>
    );
  }

  const real = pnl.basis === 'live_fills';
  return (
    <span className="flex flex-col leading-tight">
      <Amount v={pnl.realized_pnl ?? 0} suffix={real ? ' USDT' : ''} real={real} />
      {!real && <span className="text-xs text-gray-500">模拟账（理想撮合）</span>}
      {win}
    </span>
  );
};

const Amount: React.FC<{ v: number; suffix?: string; real: boolean }> = ({ v, suffix, real }) => (
  <span className={
    // 红涨绿跌是 A 股惯例（见 index.css 的 --color-bull/--color-bear）。
    // 模拟账一律灰：它不该在视觉上和真钱平起平坐。
    !real ? 'text-gray-300' : v >= 0 ? 'text-bull' : 'text-bear'
  }>
    {fmt(v)}{suffix}
  </span>
);

const fmt = (v: number) => `${v >= 0 ? '+' : ''}${v.toFixed(2)}`;

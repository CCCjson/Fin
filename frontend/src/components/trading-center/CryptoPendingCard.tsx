import React, { useState } from 'react';
import { Button } from '../common/Button';
import type { CryptoPendingItem } from '../../services/cryptoStrategyService';

/** crypto 待确认单卡片 —— 半自动核心：引擎排单、Jason 逐笔确认。
 *  ⚠️ 点确认时后端按现价重新核算+重跑触发条件+超2%漂移自动拦，本卡里的价是**决策时**价。 */

function pct(v: number | null | undefined): string {
  return typeof v === 'number' ? `${(v * 100).toFixed(2)}%` : '—';
}

function countdown(expiresAt: string | null): string {
  if (!expiresAt) return '';
  const ms = new Date(expiresAt).getTime() - Date.now();
  if (ms <= 0) return '即将过期';
  const m = Math.floor(ms / 60000);
  return m >= 1 ? `${m} 分钟后过期` : '不到 1 分钟';
}

export const CryptoPendingCard: React.FC<{
  order: CryptoPendingItem;
  onConfirm: (ref: string) => void;
  onReject: (ref: string) => void;
  busy?: boolean;
}> = ({ order, onConfirm, onReject, busy }) => {
  const [expanded, setExpanded] = useState(false);
  const isBuy = order.side === 'BUY';
  const fired = (order.reason?.fired as string[] | undefined) || [];
  const composite = order.reason?.composite as number | undefined;

  return (
    <div className="rounded-xl border border-border bg-dark-light/40 p-4 space-y-3">
      {/* 头部 */}
      <div className="flex items-center justify-between flex-wrap gap-2">
        <div className="flex items-center gap-3">
          <span className={`px-2 py-0.5 rounded text-xs font-bold ${
            isBuy ? 'bg-green-500/20 text-green-400' : 'bg-red-500/20 text-red-400'
          }`}>{isBuy ? '买入' : '卖出'}</span>
          <span className="text-white font-semibold">{order.symbol}</span>
          {typeof composite === 'number' && (
            <span className="text-xs text-gray-500">综合分 {composite}</span>
          )}
        </div>
        <span className="text-xs text-yellow-400/80">{countdown(order.expires_at)}</span>
      </div>

      {/* 关键数据 */}
      <div className="grid grid-cols-2 md:grid-cols-4 gap-3 text-sm">
        <div>
          <div className="text-gray-500 text-xs">{isBuy ? '目标金额' : '卖出数量'}</div>
          <div className="text-white font-medium">
            {isBuy
              ? `$${(order.quote_amount ?? order.est_notional ?? 0).toLocaleString()}`
              : `${order.quantity} 币`}
          </div>
        </div>
        <div>
          <div className="text-gray-500 text-xs">决策时价</div>
          <div className="text-gray-300">${order.price?.toLocaleString() ?? '—'}</div>
        </div>
        <div>
          <div className="text-gray-500 text-xs">净边际</div>
          <div className={order.net_edge && order.net_edge > 0 ? 'text-green-400' : 'text-gray-300'}>
            {pct(order.net_edge)}
          </div>
        </div>
        <div>
          <div className="text-gray-500 text-xs">来自策略</div>
          <div className="text-gray-400 text-xs truncate" title={order.strategy_id}>{order.strategy_id}</div>
        </div>
      </div>

      {/* 现价重估提示 */}
      <div className="text-xs text-gray-500 bg-dark/50 rounded px-2 py-1.5 border border-border/50">
        ⚠️ 确认时后端会按<b className="text-gray-400">当前市价</b>重新核算数量、重跑触发条件，
        价格较决策时漂移超 2% 会自动拦下要你重新决策。
      </div>

      {/* 命中条件（可展开）*/}
      {fired.length > 0 && (
        <div>
          <button onClick={() => setExpanded((v) => !v)}
            className="text-xs text-primary/80 hover:text-primary">
            {expanded ? '收起' : `查看命中的 ${fired.length} 个条件`}
          </button>
          {expanded && (
            <ul className="mt-2 space-y-1 text-xs text-gray-400">
              {fired.map((f, i) => <li key={i} className="font-mono">{f}</li>)}
            </ul>
          )}
        </div>
      )}

      {/* 操作 */}
      <div className="flex gap-2 pt-1">
        <Button variant="primary" size="sm" loading={busy}
          onClick={() => onConfirm(order.order_ref)}>确认成交</Button>
        <Button variant="subtle" size="sm" disabled={busy}
          onClick={() => onReject(order.order_ref)}>拒绝</Button>
      </div>
    </div>
  );
};

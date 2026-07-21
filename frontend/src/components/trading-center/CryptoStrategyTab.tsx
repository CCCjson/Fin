import React, { useState } from 'react';
import { Card } from '../common/Card';
import { Button } from '../common/Button';
import { useCryptoStrategyStore } from '../../stores/cryptoStrategyStore';
import {
  cryptoStrategyService as svc,
  type CryptoStrategyItem,
  type CryptoRunItem,
} from '../../services/cryptoStrategyService';

const KIND_LABEL: Record<string, string> = { swing: '波段', arb: '套利', long_hold: '长持' };
const STATUS_STYLE: Record<string, string> = {
  armed: 'text-green-400', backtested: 'text-blue-400', draft: 'text-gray-400',
  paused_by_guardrail: 'text-red-400', retired: 'text-gray-600',
};

const StrategyCard: React.FC<{ s: CryptoStrategyItem }> = ({ s }) => {
  const { arm, enablePaper, pause, retire, backtest } = useCryptoStrategyStore();
  const [busy, setBusy] = useState(false);
  const [runs, setRuns] = useState<CryptoRunItem[] | null>(null);
  const [showRuns, setShowRuns] = useState(false);

  const run = async (fn: () => Promise<void>) => {
    setBusy(true);
    try { await fn(); } finally { setBusy(false); }
  };

  const toggleRuns = async () => {
    if (!showRuns && runs === null) {
      try { const r = await svc.listRuns(s.strategy_id, 20); setRuns(r.runs || []); }
      catch { setRuns([]); }
    }
    setShowRuns((v) => !v);
  };

  return (
    <div className="rounded-xl border border-border bg-dark-light/40 p-4 space-y-3">
      <div className="flex items-center justify-between flex-wrap gap-2">
        <div className="flex items-center gap-2">
          <span className="text-white font-semibold">{s.name}</span>
          <span className="text-xs px-1.5 py-0.5 rounded bg-dark text-gray-400">
            {KIND_LABEL[s.strategy_kind] || s.strategy_kind}
          </span>
          <span className={`text-xs px-1.5 py-0.5 rounded ${
            s.mode === 'live' ? 'bg-green-500/15 text-green-400' : 'bg-blue-500/15 text-blue-400'
          }`}>{s.mode === 'live' ? '实盘' : '纸面'}</span>
        </div>
        <span className={`text-xs ${STATUS_STYLE[s.status] || 'text-gray-400'}`}>
          {s.enabled ? '● 运行中' : '○ 未启用'} · {s.status}
        </span>
      </div>

      <div className="grid grid-cols-2 md:grid-cols-4 gap-3 text-sm">
        <div>
          <div className="text-gray-500 text-xs">参考回测</div>
          <div className="text-gray-300" title="双均线代理，不代表你的 DSL 规则，仅供参考">
            {typeof s.backtest_net_return === 'number'
              ? `${(s.backtest_net_return * 100).toFixed(2)}%` : '—'}
            <span className="text-gray-600 text-[10px] ml-1">代理</span>
          </div>
        </div>
        <div>
          <div className="text-gray-500 text-xs">节奏</div>
          <div className="text-gray-300">每 {s.interval_minutes} 分钟</div>
        </div>
        <div>
          <div className="text-gray-500 text-xs">上次运行</div>
          <div className="text-gray-400 text-xs">{s.last_run_at?.slice(5, 16) || '—'}</div>
        </div>
        <div>
          <div className="text-gray-500 text-xs">策略 ID</div>
          <div className="text-gray-500 text-xs truncate" title={s.strategy_id}>{s.strategy_id}</div>
        </div>
      </div>

      {s.halted_reason && (
        <div className="text-xs text-red-400/90 bg-red-500/10 rounded px-2 py-1.5">
          护栏熔断：{s.halted_reason}
        </div>
      )}

      <div className="flex flex-wrap gap-2 pt-1">
        <Button variant="ghost" size="sm" loading={busy} onClick={() => run(() => backtest(s.strategy_id))}>回测</Button>
        <Button variant="ghost" size="sm" disabled={busy} onClick={() => run(() => enablePaper(s.strategy_id))}>纸面干跑</Button>
        <Button variant="primary" size="sm" disabled={busy}
          title="按你的 DSL 规则排单，每笔仍由你逐笔确认才成交"
          onClick={() => run(() => arm(s.strategy_id))}>上实盘</Button>
        {s.enabled && (
          <Button variant="subtle" size="sm" disabled={busy} onClick={() => run(() => pause(s.strategy_id))}>暂停</Button>
        )}
        <Button variant="subtle" size="sm" disabled={busy} onClick={() => run(() => retire(s.strategy_id))}>退役</Button>
        <button onClick={toggleRuns} className="text-xs text-primary/80 hover:text-primary ml-auto">
          {showRuns ? '收起运行日志' : '运行日志'}
        </button>
      </div>

      {showRuns && (
        <div className="mt-2 space-y-1 text-xs">
          {runs === null ? <div className="text-gray-500">加载中…</div>
            : runs.length === 0 ? <div className="text-gray-600">暂无运行记录</div>
            : runs.map((r) => (
              <div key={r.id} className="flex items-center gap-2 text-gray-400 font-mono">
                <span className="text-gray-600">{r.started_at?.slice(5, 16)}</span>
                <span className="text-gray-300">{r.status}</span>
                <span>评估{r.symbols_evaluated} / 排单{r.orders_placed}</span>
                {r.error && <span className="text-red-400">{r.error}</span>}
              </div>
            ))}
        </div>
      )}
    </div>
  );
};

export const CryptoStrategyTab: React.FC = () => {
  const { strategies } = useCryptoStrategyStore();
  const visible = strategies.filter((s) => s.status !== 'retired');  // 退役的不再展示

  return (
    <Card className="p-4 md:p-5 space-y-4">
      <div className="flex items-center justify-between flex-wrap gap-2">
        <h2 className="text-white font-medium text-lg">半自动策略</h2>
        <span className="text-xs text-gray-500">
          💬 新建策略：对 MoneyBill 说「帮我做个自动策略」，它会把你的规则编译成策略
        </span>
      </div>

      {visible.length === 0 ? (
        <div className="text-center py-16">
          <div className="text-4xl mb-3 opacity-40">🤖</div>
          <p className="text-gray-500">还没有策略</p>
          <p className="text-gray-600 text-xs mt-1">
            去和 MoneyBill 聊「按XX规则半自动交易」，编译好的策略会出现在这里
          </p>
        </div>
      ) : (
        <div className="space-y-3">
          {visible.map((s) => <StrategyCard key={s.strategy_id} s={s} />)}
        </div>
      )}
    </Card>
  );
};

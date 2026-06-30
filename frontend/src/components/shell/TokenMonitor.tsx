import React, { useEffect, useState } from 'react';
import { useUsageStore } from '../../store/usageStore';
import { agentService } from '../../services/agentService';

/* 右上角 Token / 成本监视器 —— 累计 OpenAI 接口 usage，按模型单价折算美元 */
export const TokenMonitor: React.FC = () => {
  const baseTokens = useUsageStore((s) => s.totalTokens);
  const liveTokens = useUsageStore((s) => s.liveTokens);
  const costUsd = useUsageStore((s) => s.costUsd);
  const calls = useUsageStore((s) => s.calls);
  const byModel = useUsageStore((s) => s.byModel);
  const today = useUsageStore((s) => s.today);
  const [open, setOpen] = useState(false);

  const totalTokens = baseTokens + liveTokens;

  // 挂载时拉一次后端累计快照
  useEffect(() => {
    agentService.getUsage().then(useUsageStore.getState().setSnapshot).catch(() => {});
  }, []);

  const fmtTokens = (n: number) => (n >= 1000 ? `${(n / 1000).toFixed(1)}k` : `${n}`);

  return (
    <div className="fixed top-3 right-4 z-40">
      <button
        onClick={() => setOpen((v) => !v)}
        className="flex items-center gap-2 bg-dark-card/90 backdrop-blur border border-border hover:border-primary/50 rounded-full px-3 py-1.5 text-xs shadow-card transition-colors"
        title="本次运行累计 token 与成本（点开看明细）"
      >
        <span className="text-accent-cyan">🪙 {fmtTokens(totalTokens)}</span>
        <span className="text-bull font-semibold">${costUsd.toFixed(4)}</span>
      </button>

      {open && (
        <div className="absolute right-0 mt-2 w-64 bg-dark-card border border-border rounded-xl shadow-card p-3 text-xs">
          {/* 今日整体用量（跨重启累计） */}
          <div className="rounded-lg bg-primary/10 border border-primary/30 p-2.5 mb-3">
            <div className="flex items-center justify-between mb-1.5">
              <span className="text-primary-light font-semibold">📅 今日用量</span>
              <span className="text-[10px] text-gray-500">{today?.date ?? '—'}</span>
            </div>
            <div className="flex items-end justify-between">
              <span className="text-lg font-bold text-bull leading-none">
                ${(today?.cost_usd ?? 0).toFixed(4)}
              </span>
              <span className="text-gray-400">
                {fmtTokens(today?.total_tokens ?? 0)} tokens · {today?.calls ?? 0} 次
              </span>
            </div>
          </div>

          <div className="text-gray-500 mb-1.5 text-[11px]">本次运行累计</div>
          <div className="flex justify-between text-gray-300 mb-1">
            <span className="text-gray-500">累计成本</span>
            <span className="text-bull font-semibold">${costUsd.toFixed(6)}</span>
          </div>
          <div className="flex justify-between text-gray-300 mb-1">
            <span className="text-gray-500">总 tokens</span>
            <span>{totalTokens.toLocaleString()}</span>
          </div>
          <div className="flex justify-between text-gray-300 mb-2">
            <span className="text-gray-500">调用次数</span>
            <span>{calls}</span>
          </div>
          {Object.keys(byModel).length > 0 && (
            <div className="border-t border-border pt-2 space-y-1">
              <div className="text-gray-500 mb-1">按模型</div>
              {Object.entries(byModel).map(([m, v]: [string, any]) => (
                <div key={m} className="flex justify-between text-gray-400">
                  <span className="truncate mr-2">{m}</span>
                  <span>${Number(v.cost).toFixed(4)}</span>
                </div>
              ))}
            </div>
          )}
          <div className="text-[10px] text-gray-600 mt-2 leading-snug">
            单价默认为占位值，按实际计费用环境变量 AGENT_PRICE__&lt;model&gt; 设置后才准确。
          </div>
        </div>
      )}
    </div>
  );
};

export default TokenMonitor;

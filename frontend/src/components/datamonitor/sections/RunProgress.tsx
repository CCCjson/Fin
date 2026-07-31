import React from 'react';
import { Card } from '../../common/Card';
import { Button } from '../../common/Button';
import type { RunSnapshot } from '../../../services/dataMonitorService';
import { MARKET_META, fmtDur, fmtNum } from './shared';

/* ────────────────────────────────────────────────────────────────
   当前运行进度 —— **两层**进度。

       总进度：第 3 个资产 / 共 14 个
       子进度：A股日线 1203 / 5201

   数据来自后台任务的 snapshot（轮询），不是流式 —— 所以关掉页面再回来
   照样能看到进度，这正是从 HTTP 流改成后台任务的原因。
   ──────────────────────────────────────────────────────────────── */

const STATUS_STYLE: Record<string, string> = {
  pending: 'text-gray-500',
  running: 'text-primary',
  done: 'text-bear',
  partial: 'text-yellow-400',
  skipped: 'text-gray-400',
  failed: 'text-bull',
};

const STATUS_ICON: Record<string, string> = {
  pending: '⏸',
  running: '⏳',
  done: '✅',
  partial: '⚠️',
  skipped: '⏭',
  failed: '❌',
};

interface Props {
  run: RunSnapshot | null;
  onStop: () => void;
}

export const RunProgress: React.FC<Props> = ({ run, onStop }) => {
  if (!run || run.status === 'idle' || !run.planned?.length) return null;

  const active = run.status === 'running' || run.status === 'stopping';
  const pct = run.total_all ? Math.min(100, (run.done_total / run.total_all) * 100) : 0;

  return (
    <Card className="p-4" glow={active}>
      <div className="flex items-center justify-between flex-wrap gap-2 mb-3">
        <div className="text-sm text-white font-medium">
          {run.status === 'running'
            ? '⏳ 正在更新数据'
            : run.status === 'stopping'
            ? '⏹ 正在停止…'
            : run.status === 'stopped'
            ? '⏹ 已停止'
            : '✅ 更新完成'}
          <span className="text-xs text-gray-500 font-normal ml-2">
            {run.mode === 'gap_fill' ? '定点补洞' : '增量更新'}
          </span>
        </div>
        <div className="flex items-center gap-3 text-xs text-gray-400">
          <span className="tabular-nums">
            {run.done_total} / {run.total_all} 个资产
          </span>
          <span className="tabular-nums">已用 {fmtDur(run.elapsed_seconds)}</span>
          {active && run.eta_seconds ? (
            <span className="tabular-nums">预计还需 {fmtDur(run.eta_seconds)}</span>
          ) : null}
          {active && (
            <Button variant="danger" size="sm" onClick={onStop}>
              停止
            </Button>
          )}
        </div>
      </div>

      {/* 总进度条 */}
      <div className="w-full h-2 bg-dark-light rounded-full overflow-hidden mb-3">
        <div
          className="h-full bg-primary transition-all duration-500"
          style={{ width: `${pct}%` }}
        />
      </div>

      {/* 逐资产子进度 */}
      <div className="space-y-1.5 max-h-72 overflow-y-auto pr-1">
        {run.planned.map((key) => {
          const p = run.asset_progress?.[key];
          if (!p) return null;
          const sub = p.total ? Math.min(100, (p.current / p.total) * 100) : 0;
          const meta = p.market ? MARKET_META[p.market] : null;
          return (
            <div key={key} className="bg-dark-light/60 rounded-lg px-2.5 py-1.5">
              <div className="flex items-center justify-between gap-2 text-xs">
                <span className={`flex items-center gap-1.5 ${STATUS_STYLE[p.status]}`}>
                  <span>{STATUS_ICON[p.status]}</span>
                  {meta && <span>{meta.icon}</span>}
                  <span className="text-gray-200">{p.label}</span>
                </span>
                <span className="text-gray-500 tabular-nums shrink-0">
                  {p.status === 'pending'
                    ? '排队中'
                    : p.total
                    ? `${fmtNum(p.current)} / ${fmtNum(p.total)}`
                    : p.status === 'running'
                    ? '进行中'
                    : ''}
                  {p.fresh_skipped > 0 && (
                    <span className="text-gray-600 ml-2">
                      跳过已最新 {fmtNum(p.fresh_skipped)}
                    </span>
                  )}
                </span>
              </div>
              {p.status === 'running' && p.total ? (
                <div className="w-full h-1 bg-dark rounded-full overflow-hidden mt-1">
                  <div
                    className="h-full bg-primary/70 transition-all"
                    style={{ width: `${sub}%` }}
                  />
                </div>
              ) : null}
              {p.note && (
                <div className="text-[10px] text-gray-600 mt-0.5 truncate">{p.note}</div>
              )}
            </div>
          );
        })}
      </div>
    </Card>
  );
};

export default RunProgress;

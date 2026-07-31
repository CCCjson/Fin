import React from 'react';
import { Card } from '../../common/Card';
import { Button } from '../../common/Button';
import type { GapJobSnapshot, GapSummary } from '../../../services/dataMonitorService';
import { MARKET_META, fmtDateRanges, fmtDur } from './shared';

/* ────────────────────────────────────────────────────────────────
   数据缺口区 —— 「这几天本该有数据但没有」。

   ⚠️ **certain 和 suspected 必须视觉上分开**：
     certain   = 基准指数自证的交易日缺数据 → 红色，可一键补
     suspected = 没有日历、只靠「工作日」推断 → 黄色，**只展示不自动补**
                 （可能就是个节假日，误补一次港美股要白打 10-15 分钟 Yahoo）
   把两者混在一起显示，用户就会去点那个不该点的补齐。
   ──────────────────────────────────────────────────────────────── */

interface Props {
  summary: GapSummary | null;
  job: GapJobSnapshot | null;
  onFill: () => void;
  onStop: () => void;
}

export const GapPanel: React.FC<Props> = ({ summary, job, onFill, onStop }) => {
  const busy = job?.status === 'running' || job?.status === 'stopping';
  const hasGaps = (summary?.total ?? 0) > 0;

  // 没缺口且没在跑 → 整块不渲染，页面上不留空壳
  if (!hasGaps && !busy) return null;

  return (
    <Card className="p-4 border-bull/30">
      <div className="flex items-center justify-between flex-wrap gap-2 mb-3">
        <div className="text-sm text-white font-medium flex items-center gap-2">
          ⚠️ 数据缺口
          {summary && (
            <span className="text-xs text-gray-500 font-normal">
              共 {summary.total} 天
              {summary.fillable > 0 && ` · ${summary.fillable} 天可自动补`}
            </span>
          )}
        </div>
        <div className="flex items-center gap-2">
          {busy ? (
            <Button variant="danger" size="sm" onClick={onStop}>
              停止补齐
            </Button>
          ) : (
            summary && summary.fillable > 0 && (
              <Button variant="primary" size="sm" icon="🩹" onClick={onFill}>
                全部补齐（{summary.fillable} 天）
              </Button>
            )
          )}
        </div>
      </div>

      {/* 补齐任务进行中的进度 */}
      {busy && (
        <div className="mb-3 bg-dark-light/60 rounded-lg px-3 py-2">
          <div className="flex items-center justify-between text-xs">
            <span className="text-primary">
              {job?.phase === 'scanning' ? '🔍 正在扫描缺口…' : '🩹 正在补齐…'}
              {job?.current_asset && (
                <span className="text-gray-400 ml-2">{job.current_asset}</span>
              )}
            </span>
            <span className="text-gray-500 tabular-nums">
              {job?.done_total ?? 0} / {job?.total_all ?? 0} 天 · 已用{' '}
              {fmtDur(job?.elapsed_seconds)}
            </span>
          </div>
          {!!job?.total_all && (
            <div className="w-full h-1.5 bg-dark rounded-full overflow-hidden mt-1.5">
              <div
                className="h-full bg-primary transition-all"
                style={{
                  width: `${Math.min(100, (job.done_total / job.total_all) * 100)}%`,
                }}
              />
            </div>
          )}
        </div>
      )}

      {/* 上一轮补齐的结果 */}
      {!busy && job && (job.days_filled > 0 || job.days_permanent > 0) && (
        <div className="mb-3 text-[11px] text-gray-500">
          上轮补齐：补上 <span className="text-bear">{job.days_filled}</span> 天
          {job.days_permanent > 0 && (
            <>
              {' · '}
              <span className="text-gray-400">{job.days_permanent}</span> 天实测无数据，
              已认定为非交易日（不再重试）
            </>
          )}
        </div>
      )}

      {/* 按资产分组的缺口清单 */}
      <div className="space-y-1.5">
        {summary?.by_asset.map((a) => {
          const meta = a.market ? MARKET_META[a.market] : null;
          const isSuspected = a.certain === 0 && a.suspected > 0;
          return (
            <div
              key={a.asset}
              className={`rounded-lg px-3 py-2 flex items-center justify-between gap-3 flex-wrap border ${
                isSuspected
                  ? 'bg-yellow-400/5 border-yellow-400/30'
                  : 'bg-bull/5 border-bull/25'
              }`}
            >
              <div className="flex items-center gap-2 min-w-0">
                {meta && <span>{meta.icon}</span>}
                <span className="text-xs text-gray-200">{a.asset}</span>
                <span className="text-[11px] text-gray-500 truncate">
                  {fmtDateRanges(a.dates)}
                </span>
              </div>
              <div className="flex items-center gap-2 shrink-0">
                {a.certain > 0 && (
                  <span className="text-[10px] px-1.5 py-0.5 rounded-full bg-bull/15 text-bull">
                    {a.certain} 天 · 已确认
                  </span>
                )}
                {a.suspected > 0 && (
                  <span
                    className="text-[10px] px-1.5 py-0.5 rounded-full bg-yellow-400/15 text-yellow-400"
                    title="没有交易日历可依据，只是按工作日推断的。可能是节假日，所以不会自动补。"
                  >
                    {a.suspected} 天 · 疑似（不自动补）
                  </span>
                )}
              </div>
            </div>
          );
        })}
      </div>

      <div className="mt-3 text-[10px] text-gray-600">
        缺口判据 = 基准指数自证的交易日 − 实际有数据的日。补 3 次仍拉不到数据的会被
        认定为非交易日，不再重试。
      </div>
    </Card>
  );
};

export default GapPanel;

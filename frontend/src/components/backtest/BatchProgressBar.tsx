import React, { useRef, useEffect } from 'react';
import type { BatchProgressEvent } from '../../types';

interface Props {
  batchId: string;
  total: number;
  current: number;
  completed: number;
  failed: number;
  logs: BatchProgressEvent[];
  onCancel: () => void;
}

export const BatchProgressBar: React.FC<Props> = ({
  batchId, total, current, completed, failed, logs, onCancel,
}) => {
  const pct = total > 0 ? Math.round((current / total) * 100) : 0;
  const listRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (listRef.current) {
      listRef.current.scrollTop = listRef.current.scrollHeight;
    }
  }, [logs.length]);

  return (
    <div className="bg-gradient-card border border-border shadow-card p-4 rounded-xl">
      <div className="flex items-center justify-between mb-2">
        <h3 className="text-sm font-semibold text-white">批量回测进度</h3>
        <button
          onClick={onCancel}
          className="text-xs px-3 py-1 bg-red-500/20 text-red-400 border border-red-500/30 rounded-lg hover:bg-red-500/30 transition-all"
        >
          取消
        </button>
      </div>

      {/* 进度条 */}
      <div className="relative h-4 bg-dark-light rounded-full overflow-hidden mb-2">
        <div
          className="absolute inset-y-0 left-0 rounded-full transition-all duration-300"
          style={{
            width: `${pct}%`,
            background: 'linear-gradient(90deg, #8B5CF6, #06B6D4)',
          }}
        />
        <span className="absolute inset-0 flex items-center justify-center text-[10px] text-white font-semibold">
          {pct}%
        </span>
      </div>

      {/* 统计 */}
      <div className="flex gap-4 text-xs mb-3">
        <span className="text-gray-400">
          进度 <span className="text-white">{current}/{total}</span>
        </span>
        <span className="text-bear">
          完成 {completed}
        </span>
        {failed > 0 && (
          <span className="text-bull">
            失败 {failed}
          </span>
        )}
      </div>

      {/* 滚动日志 */}
      <div ref={listRef} className="max-h-40 overflow-y-auto space-y-1 scrollbar-thin">
        {logs.filter(l => l.event === 'progress').map((log, idx) => {
          const lt = log.latest;
          if (!lt) return null;
          const isOk = lt.status === 'completed';
          return (
            <div key={idx} className="flex items-center gap-2 text-xs py-0.5">
              <span className={isOk ? 'text-bear' : 'text-bull'}>
                {isOk ? '\u2713' : '\u2717'}
              </span>
              <span className="text-gray-300 truncate flex-1">
                {lt.label || `${lt.symbol} / ${lt.strategy}`}
              </span>
              {isOk && lt.sharpe_ratio !== undefined && (
                <span className="text-gray-500">SR {lt.sharpe_ratio.toFixed(2)}</span>
              )}
              {isOk && lt.total_return_pct !== undefined && (
                <span className={lt.total_return_pct >= 0 ? 'text-bull' : 'text-bear'}>
                  {lt.total_return_pct >= 0 ? '+' : ''}{lt.total_return_pct.toFixed(1)}%
                </span>
              )}
              {!isOk && lt.error && (
                <span className="text-red-400 truncate max-w-[120px]">{lt.error}</span>
              )}
            </div>
          );
        })}
      </div>
    </div>
  );
};

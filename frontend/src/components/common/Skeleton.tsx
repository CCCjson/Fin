import React from 'react';

/* ================================================================
   骨架屏 —— 内容加载占位（替代 spinner / "加载中…" 纯文字）
   用 shimmer 流光动画（见 index.css 的 skeleton-shimmer keyframe）。
   ================================================================ */

interface SkeletonProps {
  className?: string;
  /** 圆角，默认 rounded-lg */
  rounded?: string;
}

/** 基元：一块流光占位 */
export const Skeleton: React.FC<SkeletonProps> = ({ className = '', rounded = 'rounded-lg' }) => (
  <div className={`skeleton-shimmer ${rounded} ${className}`} />
);

/** 多行文本占位 */
export const SkeletonLines: React.FC<{ lines?: number; className?: string }> = ({
  lines = 3,
  className = '',
}) => (
  <div className={`space-y-2.5 ${className}`}>
    {Array.from({ length: lines }).map((_, i) => (
      <Skeleton
        key={i}
        className="h-3.5"
        rounded="rounded"
      />
    ))}
  </div>
);

/** 卡片占位（标题 + 数字 + 几行） */
export const SkeletonCard: React.FC<{ className?: string }> = ({ className = '' }) => (
  <div className={`bg-dark-card border border-border rounded-xl p-4 ${className}`}>
    <Skeleton className="h-3 w-1/3 mb-3" rounded="rounded" />
    <Skeleton className="h-7 w-1/2 mb-4" rounded="rounded" />
    <SkeletonLines lines={2} />
  </div>
);

/** 指标卡组占位 */
export const SkeletonMetricGrid: React.FC<{ count?: number; className?: string }> = ({
  count = 4,
  className = '',
}) => (
  <div className={`grid grid-cols-2 md:grid-cols-4 gap-3 ${className}`}>
    {Array.from({ length: count }).map((_, i) => (
      <div key={i} className="bg-dark-card border border-border rounded-xl p-4 min-h-[80px]">
        <Skeleton className="h-3 w-2/3 mb-2.5" rounded="rounded" />
        <Skeleton className="h-6 w-1/2" rounded="rounded" />
      </div>
    ))}
  </div>
);

/** 表格占位 */
export const SkeletonTable: React.FC<{ rows?: number; cols?: number; className?: string }> = ({
  rows = 5,
  cols = 5,
  className = '',
}) => (
  <div className={`rounded-xl border border-border overflow-hidden ${className}`}>
    <div className="bg-dark-light px-3 py-2.5 flex gap-3">
      {Array.from({ length: cols }).map((_, i) => (
        <Skeleton key={i} className="h-3 flex-1" rounded="rounded" />
      ))}
    </div>
    <div className="divide-y divide-border">
      {Array.from({ length: rows }).map((_, r) => (
        <div key={r} className="px-3 py-3 flex gap-3">
          {Array.from({ length: cols }).map((_, c) => (
            <Skeleton key={c} className="h-3.5 flex-1" rounded="rounded" />
          ))}
        </div>
      ))}
    </div>
  </div>
);

export default Skeleton;

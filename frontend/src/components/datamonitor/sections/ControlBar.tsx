import React from 'react';
import { Button } from '../../common/Button';
import type { AssetMatrix, GapSummary } from '../../../services/dataMonitorService';
import { HEALTH_DOT, HEALTH_STYLE, MARKET_META, MARKET_ORDER, fmtTime } from './shared';

/* ────────────────────────────────────────────────────────────────
   总控条 —— 一眼看全 + 唯一的主操作入口。

   替代原来「标题 + 新鲜度条带 + 顶部告警横幅 + 补跑横幅」四块。那四块的口径
   互相重叠（同一个「数据旧了」会同时点亮三处），信息密度反而稀。
   这里收成：四个市场胶囊（健康度）+ 缺口角标（具体缺什么）+ 两个主按钮。
   ──────────────────────────────────────────────────────────────── */

interface Props {
  matrix: AssetMatrix | null;
  gapSummary: GapSummary | null;
  serverTime: string | null;
  running: boolean;
  pollMs: number;
  onUpdateAll: () => void;
  onStop: () => void;
  onScanGaps: () => void;
  gapBusy: boolean;
}

/** 每个市场取「该市场行情资产」的健康度当代表 —— 用户先关心的是行情新不新 */
const marketHealth = (matrix: AssetMatrix | null, market: string) => {
  if (!matrix) return null;
  const quote = matrix.cells.find(
    (c) => c.market === market && c.group === 'quote' && c.key.startsWith('daily.'),
  ) || matrix.cells.find((c) => c.market === market && c.group === 'quote');
  return quote || null;
};

export const ControlBar: React.FC<Props> = ({
  matrix,
  gapSummary,
  serverTime,
  running,
  pollMs,
  onUpdateAll,
  onStop,
  onScanGaps,
  gapBusy,
}) => {
  const gapCount = gapSummary?.total ?? 0;

  return (
    <div className="sticky top-0 z-20 -mx-6 px-6 py-3 bg-dark/90 backdrop-blur-md border-b border-border">
      <div className="max-w-7xl mx-auto space-y-3">
        {/* 第一行：标题 + 主操作 */}
        <div className="flex items-center justify-between flex-wrap gap-3">
          <div className="flex items-center gap-3">
            <h1 className="text-xl font-bold text-white flex items-center gap-2">
              🛰️ 数据监控
            </h1>
            {matrix && (
              <span
                className={`text-[11px] px-2 py-0.5 rounded-full border ${HEALTH_STYLE[matrix.overall_health]}`}
              >
                {matrix.overall_health === 'ok'
                  ? '全部正常'
                  : matrix.overall_health === 'warn'
                  ? '有偏旧项'
                  : '有异常项'}
              </span>
            )}
          </div>
          <div className="flex items-center gap-2">
            <Button
              variant="subtle"
              size="sm"
              icon="🔍"
              loading={gapBusy}
              onClick={onScanGaps}
            >
              扫描缺口
            </Button>
            {running ? (
              <Button variant="danger" size="sm" icon="⏹" onClick={onStop}>
                停止更新
              </Button>
            ) : (
              <Button variant="primary" size="sm" icon="🔄" onClick={onUpdateAll}>
                更新全部
              </Button>
            )}
          </div>
        </div>

        {/* 第二行：四市场胶囊 + 缺口角标 */}
        <div className="flex items-center gap-2 flex-wrap">
          {MARKET_ORDER.map((m) => {
            const cell = marketHealth(matrix, m);
            const meta = MARKET_META[m];
            return (
              <div
                key={m}
                className="flex items-center gap-1.5 px-2.5 py-1 rounded-lg bg-dark-light border border-border"
                title={cell?.health_reason || ''}
              >
                <span className="text-sm">{meta.icon}</span>
                <span className="text-xs text-gray-300">{meta.label}</span>
                <span className="text-[11px] text-gray-500 tabular-nums">
                  {cell?.latest_date?.slice(5) || '—'}
                </span>
                <span
                  className={`w-1.5 h-1.5 rounded-full ${HEALTH_DOT[cell?.health || 'unknown']}`}
                />
              </div>
            );
          })}

          {gapCount > 0 && (
            <div className="flex items-center gap-1.5 px-2.5 py-1 rounded-lg bg-bull/10 border border-bull/40">
              <span className="text-sm">⚠️</span>
              <span className="text-xs text-bull">
                {gapCount} 天缺口
                {gapSummary && gapSummary.fillable < gapCount
                  ? `（${gapSummary.fillable} 天可自动补）`
                  : ''}
              </span>
            </div>
          )}

          <div className="ml-auto text-[11px] text-gray-500">
            {running ? (
              <span className="text-primary">更新中 · 快刷 {pollMs / 1000}s</span>
            ) : (
              <span>空闲 · 慢刷 {pollMs / 1000}s</span>
            )}
            {serverTime && <span> · 服务器 {fmtTime(serverTime)}</span>}
          </div>
        </div>
      </div>
    </div>
  );
};

export default ControlBar;

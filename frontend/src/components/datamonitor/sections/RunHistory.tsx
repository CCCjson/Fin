import React from 'react';
import { Card } from '../../common/Card';
import type { UpdateLogRow } from '../../../services/dataMonitorService';
import { fmtDur, fmtNum, fmtTime } from './shared';

/* ────────────────────────────────────────────────────────────────
   运行历史 —— `DataUpdateLog` 表。

   编排器现在给**每个资产**写一行（`update_type` = `DataAsset.key`），所以这里
   终于能看到港美股/crypto/新闻跑没跑 —— 此前只有部分 updater 会写，日志表是残缺的。
   老行的 `update_type` 是 `daily`/`financial` 这类旧枚举，两种都要能显示。
   ──────────────────────────────────────────────────────────────── */

const STATUS_STYLE: Record<string, string> = {
  success: 'text-bear',
  completed: 'text-bear',
  failed: 'text-bull',
  partial: 'text-yellow-400',
  running: 'text-yellow-400',
  skipped: 'text-gray-500',
  unknown: 'text-gray-500',
};

const STATUS_LABEL: Record<string, string> = {
  success: '成功',
  completed: '完成',
  failed: '失败',
  partial: '部分',
  running: '运行中',
  skipped: '跳过',
  unknown: '未知',
};

export const RunHistory: React.FC<{ logs: UpdateLogRow[] }> = ({ logs }) => (
  <Card className="p-4">
    <div className="text-sm text-white font-medium mb-3">📋 运行历史</div>
    {logs.length === 0 ? (
      <div className="text-xs text-gray-500 py-4 text-center">暂无更新记录</div>
    ) : (
      <div className="overflow-x-auto">
        <div className="min-w-[640px]">
          <div className="grid grid-cols-6 gap-2 text-xs text-gray-500 pb-2 border-b border-border">
            <div className="col-span-2">资产</div>
            <div>状态</div>
            <div className="text-right">记录数</div>
            <div className="text-right">耗时</div>
            <div className="text-right">完成时间</div>
          </div>
          {logs.map((lg) => (
            <div
              key={lg.id}
              className="grid grid-cols-6 gap-2 text-xs py-2 border-b border-border/50 last:border-0 text-gray-300"
            >
              <div className="col-span-2 truncate" title={lg.error_message || ''}>
                {lg.update_type}
              </div>
              <div className={STATUS_STYLE[lg.status?.toLowerCase()] || 'text-gray-400'}>
                {STATUS_LABEL[lg.status?.toLowerCase()] || lg.status}
              </div>
              <div className="text-right tabular-nums">{fmtNum(lg.records_count)}</div>
              <div className="text-right tabular-nums">{fmtDur(lg.duration_seconds)}</div>
              <div className="text-right text-gray-400 tabular-nums">
                {fmtTime(lg.completed_at)}
              </div>
            </div>
          ))}
        </div>
      </div>
    )}
  </Card>
);

export default RunHistory;

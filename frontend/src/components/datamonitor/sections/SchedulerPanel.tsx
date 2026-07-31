import React from 'react';
import { Card } from '../../common/Card';
import { Button } from '../../common/Button';
import type { SchedulerRow } from '../../../services/dataMonitorService';
import { fmtTime } from './shared';

/* ────────────────────────────────────────────────────────────────
   定时任务区 —— 四个调度器，各自开关。

   此前前端只有**一个**开关，且只接到 A 股主链。港美股 job 明明有独立 cron
   （16:30，因为港股 16:00 才收盘）、有独立 enabled 标志，后端 `get_status()`
   一直在返回 `overseas_*` 字段 —— 但界面上既看不到也关不掉。
   ──────────────────────────────────────────────────────────────── */

interface Props {
  schedulers: SchedulerRow[];
  gapAutofill: { enabled?: boolean; intervalHours?: number };
  busyId: string | null;
  onToggle: (id: string, enabled: boolean) => void;
}

export const SchedulerPanel: React.FC<Props> = ({
  schedulers,
  gapAutofill,
  busyId,
  onToggle,
}) => (
  <Card className="p-4">
    <div className="text-sm text-white font-medium mb-3">
      ⏰ 定时任务
      <span className="text-xs text-gray-500 font-normal ml-2">
        后端开着才会跑（关掉 App 那段时间的缺口由「自动补齐」捞回来）
      </span>
    </div>

    <div className="space-y-1.5">
      {schedulers.map((s) => (
        <div
          key={s.id}
          className="bg-dark-light/50 rounded-lg px-3 py-2 flex items-center justify-between gap-3 flex-wrap"
        >
          <div className="min-w-0">
            <div className="text-xs text-gray-200 flex items-center gap-2">
              {s.label}
              <span
                className={`text-[10px] px-1.5 py-0.5 rounded-full ${
                  s.enabled ? 'bg-bear/15 text-bear' : 'bg-gray-500/15 text-gray-400'
                }`}
              >
                {s.enabled ? '已开启' : '已关闭'}
              </span>
              {s.is_updating && (
                <span className="text-[10px] px-1.5 py-0.5 rounded-full bg-yellow-400/15 text-yellow-400">
                  运行中
                </span>
              )}
            </div>
            <div className="text-[11px] text-gray-500 mt-0.5">{s.description}</div>
            <div className="text-[10px] text-gray-600 mt-0.5">
              计划 {s.cron || '—'} · 下次 {fmtTime(s.next_run)}
            </div>
          </div>
          <div className="shrink-0">
            {s.toggleable ? (
              <Button
                variant={s.enabled ? 'subtle' : 'primary'}
                size="sm"
                loading={busyId === s.id}
                onClick={() => onToggle(s.id, !s.enabled)}
              >
                {s.enabled ? '关闭' : '开启'}
              </Button>
            ) : (
              <span className="text-[10px] text-gray-600" title={s.toggle_hint}>
                需改 .env
              </span>
            )}
          </div>
        </div>
      ))}

      {/* 缺口自动补齐不是 APScheduler 意义上的「调度器」，但对用户来说是同一类
          东西（「后台会不会自己跑」），所以放同一块里展示 */}
      <div className="bg-dark-light/50 rounded-lg px-3 py-2 flex items-center justify-between gap-3 flex-wrap">
        <div>
          <div className="text-xs text-gray-200 flex items-center gap-2">
            数据缺口自动补齐
            <span
              className={`text-[10px] px-1.5 py-0.5 rounded-full ${
                gapAutofill.enabled ? 'bg-bear/15 text-bear' : 'bg-gray-500/15 text-gray-400'
              }`}
            >
              {gapAutofill.enabled ? '已开启' : '已关闭'}
            </span>
          </div>
          <div className="text-[11px] text-gray-500 mt-0.5">
            扫出「中间空掉的交易日」并自动补回来。⛔ 只补基准指数确认过的日子，
            疑似假期的只报不补
          </div>
          <div className="text-[10px] text-gray-600 mt-0.5">
            启动 7 分钟后首扫 · 之后每 {gapAutofill.intervalHours ?? 6} 小时一次
          </div>
        </div>
        <span className="text-[10px] text-gray-600 shrink-0">GAP_AUTOFILL_ENABLED</span>
      </div>
    </div>
  </Card>
);

export default SchedulerPanel;

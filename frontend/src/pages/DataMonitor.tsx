import React, { useCallback, useEffect, useRef, useState } from 'react';
import { Card } from '../components/common/Card';
import { Button } from '../components/common/Button';
import { SkeletonCard, SkeletonMetricGrid } from '../components/common/Skeleton';
import { dataMonitorService } from '../services/dataMonitorService';
import type {
  AssetMatrix as Matrix,
  GapJobSnapshot,
  GapSummary,
  MonitorOverview,
  RunSnapshot,
  SchedulerRow,
} from '../services/dataMonitorService';
import { knowledgeService } from '../services/knowledgeService';
import type { KnowledgeStats } from '../services/knowledgeService';
import { ControlBar } from '../components/datamonitor/sections/ControlBar';
import { RunProgress } from '../components/datamonitor/sections/RunProgress';
import { GapPanel } from '../components/datamonitor/sections/GapPanel';
import { AssetMatrix } from '../components/datamonitor/sections/AssetMatrix';
import { SchedulerPanel } from '../components/datamonitor/sections/SchedulerPanel';
import { IngestDrawer } from '../components/datamonitor/sections/IngestDrawer';
import { RunHistory } from '../components/datamonitor/sections/RunHistory';

/* ════════════════════════════════════════════════════════════════
   数据监控页 —— 组装壳。

   ## 这个文件原来有 1028 行

   17 个区块竖着堆一列，信息层级完全平坦：7 个后台任务面板和 7 张资产卡视觉
   权重一样重，三套告警口径重叠打架（补跑横幅 + 顶部告警横幅 + 每卡角标），
   「看数据健康」和「跑摄入任务」两类事混在一页。

   现在拆成 7 个 section（`components/datamonitor/sections/`），本文件只负责
   **取数 + 轮询 + 把状态发下去**，不含任何布局细节。

   ## 轮询策略

   更新在跑时 3s 快刷（进度秒级在变），空闲 60s 慢刷（日频数据不常变）。
   进度来自后台任务的 snapshot 而不是 HTTP 流 —— 所以**关掉页面再回来照样
   能看到进度**，这正是从流式改成后台任务的原因。
   ════════════════════════════════════════════════════════════════ */

const IDLE_POLL_MS = 60_000;
const ACTIVE_POLL_MS = 3_000;

export const DataMonitor: React.FC = () => {
  const [overview, setOverview] = useState<MonitorOverview | null>(null);
  const [matrix, setMatrix] = useState<Matrix | null>(null);
  const [gapSummary, setGapSummary] = useState<GapSummary | null>(null);
  const [gapJob, setGapJob] = useState<GapJobSnapshot | null>(null);
  const [run, setRun] = useState<RunSnapshot | null>(null);
  const [schedulers, setSchedulers] = useState<SchedulerRow[]>([]);
  const [knowledgeStats, setKnowledgeStats] = useState<KnowledgeStats | null>(null);

  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [toggleBusy, setToggleBusy] = useState<string | null>(null);
  const [gapBusy, setGapBusy] = useState(false);

  const pollRef = useRef<ReturnType<typeof setInterval> | null>(null);

  const load = useCallback(async () => {
    // 六路并发，任一路挂掉不拖垮其余 —— 数据监控页最忌讳「一个查询 500 整页白屏」
    const [ov, mx, gaps, gj, rn, sch] = await Promise.allSettled([
      dataMonitorService.getOverview(),
      dataMonitorService.getAssets(),
      dataMonitorService.getGaps(),
      dataMonitorService.getGapJob(),
      dataMonitorService.getRun(),
      dataMonitorService.getSchedulers(),
    ]);
    if (ov.status === 'fulfilled') setOverview(ov.value);
    if (mx.status === 'fulfilled') setMatrix(mx.value);
    if (gaps.status === 'fulfilled') setGapSummary(gaps.value.summary);
    if (gj.status === 'fulfilled') setGapJob(gj.value);
    if (rn.status === 'fulfilled') setRun(rn.value);
    if (sch.status === 'fulfilled') setSchedulers(sch.value.schedulers);

    const allFailed = [ov, mx, gaps, gj, rn, sch].every((r) => r.status === 'rejected');
    setError(allFailed ? '后端未响应，请确认服务已启动' : null);
    setLoading(false);
  }, []);

  useEffect(() => {
    knowledgeService.getStats().then(setKnowledgeStats).catch(() => setKnowledgeStats(null));
  }, []);

  const runActive = run?.status === 'running' || run?.status === 'stopping';
  const gapActive = gapJob?.status === 'running' || gapJob?.status === 'stopping';
  const isActive = runActive || gapActive || (overview?.scheduler.is_updating ?? false);

  useEffect(() => {
    load();
    const interval = isActive ? ACTIVE_POLL_MS : IDLE_POLL_MS;
    pollRef.current = setInterval(load, interval);
    return () => {
      if (pollRef.current) clearInterval(pollRef.current);
    };
  }, [load, isActive]);

  const runningKeys = new Set<string>(
    Object.entries(run?.asset_progress || {})
      .filter(([, p]) => p.status === 'running')
      .map(([k]) => k),
  );

  const handleUpdateAll = async () => {
    try {
      setRun(await dataMonitorService.startRun({}));
    } catch (e: any) {
      setError(e?.message || '启动更新失败');
    }
  };

  const handleRunOne = async (key: string) => {
    try {
      setRun(await dataMonitorService.startRun({ scope: [key] }));
    } catch (e: any) {
      setError(e?.message || '启动更新失败');
    }
  };

  const handleStopRun = async () => {
    try {
      setRun(await dataMonitorService.stopRun());
    } catch (e: any) {
      setError(e?.message || '停止失败');
    }
  };

  /** 只扫不补 —— 想先看看缺哪些天再决定 */
  const handleScanGaps = async () => {
    setGapBusy(true);
    try {
      setGapJob(await dataMonitorService.scanGaps({ scan_only: true }));
    } catch (e: any) {
      setError(e?.message || '扫描失败');
    } finally {
      setGapBusy(false);
      load();
    }
  };

  /** 扫 + 补 —— ⛔ 只会补基准指数确认过的日子，疑似假期的不动 */
  const handleFillGaps = async () => {
    try {
      setGapJob(await dataMonitorService.scanGaps({}));
    } catch (e: any) {
      setError(e?.message || '补齐失败');
    }
  };

  const handleStopGapJob = async () => {
    try {
      setGapJob(await dataMonitorService.stopGapJob());
    } catch (e: any) {
      setError(e?.message || '停止失败');
    }
  };

  const handleToggleScheduler = async (id: string, enabled: boolean) => {
    setToggleBusy(id);
    try {
      const r = await dataMonitorService.toggleScheduler2(id, enabled);
      setSchedulers(r.schedulers);
    } catch (e: any) {
      setError(e?.response?.data?.detail || e?.message || '切换失败');
    } finally {
      setToggleBusy(null);
    }
  };

  if (loading) {
    return (
      <div className="max-w-7xl mx-auto p-6 space-y-6">
        <SkeletonMetricGrid count={8} />
        <SkeletonCard />
      </div>
    );
  }

  if (error && !matrix && !overview) {
    return (
      <div className="min-h-screen bg-gradient-dark p-6">
        <div className="max-w-3xl mx-auto">
          <Card className="p-6 text-center">
            <div className="text-bull text-lg mb-2">⚠️ 加载失败</div>
            <div className="text-gray-400 text-sm mb-4">{error}</div>
            <Button variant="ghost" onClick={load}>
              重试
            </Button>
          </Card>
        </div>
      </div>
    );
  }

  return (
    <div className="min-h-screen bg-gradient-dark p-6">
      <ControlBar
        matrix={matrix}
        gapSummary={gapSummary}
        serverTime={overview?.server_time ?? matrix?.server_time ?? null}
        running={!!runActive}
        pollMs={isActive ? ACTIVE_POLL_MS : IDLE_POLL_MS}
        onUpdateAll={handleUpdateAll}
        onStop={handleStopRun}
        onScanGaps={handleScanGaps}
        gapBusy={gapBusy}
      />

      <div className="max-w-7xl mx-auto space-y-4 mt-4">
        {error && (
          <div className="rounded-xl px-4 py-2 text-xs border border-bull/40 bg-bull/10 text-bull">
            {error}
          </div>
        )}

        {/* 补跑提示：今日该更却没更（后端判定），点了才花 IP */}
        {overview?.catch_up?.needed && !runActive && (
          <div className="rounded-xl px-4 py-3 border border-amber-500/50 bg-amber-500/10 flex items-center justify-between gap-3 flex-wrap">
            <div className="flex items-center gap-2 text-sm text-amber-300">
              <span>⏰</span>
              <span>
                今日自动更新未执行（{overview.catch_up.scheduled_time} 时后端可能不在线），
                数据还停在{' '}
                <span className="font-medium">
                  {overview.catch_up.last_daily_update || '—'}
                </span>
              </span>
            </div>
            <Button variant="primary" size="sm" icon="🔄" onClick={handleUpdateAll}>
              立即补跑
            </Button>
          </div>
        )}

        <RunProgress run={run} onStop={handleStopRun} />

        <GapPanel
          summary={gapSummary}
          job={gapJob}
          onFill={handleFillGaps}
          onStop={handleStopGapJob}
        />

        <AssetMatrix matrix={matrix} runningKeys={runningKeys} onRunOne={handleRunOne} />

        <SchedulerPanel
          schedulers={schedulers}
          gapAutofill={{
            enabled: overview?.scheduler.gap_autofill_enabled,
            intervalHours: overview?.scheduler.gap_autofill_interval_hours,
          }}
          busyId={toggleBusy}
          onToggle={handleToggleScheduler}
        />

        <IngestDrawer knowledgeStats={knowledgeStats} />

        <RunHistory logs={overview?.recent_update_logs || []} />
      </div>
    </div>
  );
};

export default DataMonitor;

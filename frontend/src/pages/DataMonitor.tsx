import React, { useCallback, useEffect, useRef, useState } from 'react';
import { Card } from '../components/common/Card';
import { Button } from '../components/common/Button';
import { SkeletonMetricGrid, SkeletonCard } from '../components/common/Skeleton';
import api from '../services/api';
import { dataMonitorService } from '../services/dataMonitorService';
import type {
  MonitorOverview,
  UpdateStreamEvent,
  LimitUpDetail,
  FreshnessReport,
  RefreshStreamEvent,
} from '../services/dataMonitorService';
import { screenerService } from '../services/screenerService';
import { newsService } from '../services/newsService';
import { knowledgeService } from '../services/knowledgeService';
import { ScrapeMonitorPanel } from '../components/datamonitor/ScrapeMonitorPanel';
import { KnowledgePanel } from '../components/datamonitor/KnowledgePanel';
import { CninfoIngestPanel } from '../components/datamonitor/CninfoIngestPanel';
import { ResearchReportIngestPanel } from '../components/datamonitor/ResearchReportIngestPanel';
import { ArxivIngestPanel } from '../components/datamonitor/ArxivIngestPanel';
import { NewsJobPanel } from '../components/datamonitor/NewsJobPanel';
import { DeepHistoryPanel } from '../components/datamonitor/DeepHistoryPanel';
import type { KnowledgeStats } from '../services/knowledgeService';

// 自适应轮询：空闲慢刷（日频数据不常变），更新进行中快刷（秒级在变）
const IDLE_POLL_MS = 60_000;
const ACTIVE_POLL_MS = 5_000;

/* ---------- 小工具 ---------- */

const fmtTime = (iso: string | null | undefined): string => {
  if (!iso) return '-';
  try {
    return new Date(iso).toLocaleString('zh-CN', {
      month: '2-digit',
      day: '2-digit',
      hour: '2-digit',
      minute: '2-digit',
    });
  } catch {
    return iso;
  }
};

const fmtNum = (v: number | null | undefined): string =>
  v === null || v === undefined ? '-' : v.toLocaleString('zh-CN');

const fmtDur = (v: number | null | undefined): string =>
  v === null || v === undefined ? '-' : `${v.toFixed(1)}s`;

const statusColor = (status: string | null): string => {
  if (!status) return 'text-gray-400';
  const s = status.toLowerCase();
  if (s === 'success' || s === 'completed') return 'text-bear';
  if (s === 'failed') return 'text-bull';
  if (s === 'partial' || s === 'running') return 'text-yellow-400';
  return 'text-gray-400';
};

const statusLabel = (status: string | null): string => {
  const map: Record<string, string> = {
    success: '成功',
    completed: '完成',
    failed: '失败',
    partial: '部分',
    running: '运行中',
  };
  return status ? map[status.toLowerCase()] || status : '-';
};

/** 距今天数（iso 无效时返回 null） */
const daysAgo = (iso: string | null | undefined): number | null => {
  if (!iso) return null;
  const t = new Date(iso).getTime();
  if (Number.isNaN(t)) return null;
  return Math.floor((Date.now() - t) / 86_400_000);
};

/* ---------- 资产卡片 ---------- */

type Staleness = 'ok' | 'warn' | 'stale';

// A 股配色：bear=绿(正常)，bull=红(告警)
const stalenessStyle: Record<Staleness, string> = {
  ok: 'bg-bear/15 text-bear',
  warn: 'bg-yellow-400/15 text-yellow-400',
  stale: 'bg-bull/15 text-bull',
};

const stalenessLabel: Record<Staleness, string> = {
  ok: '正常',
  warn: '偏旧',
  stale: '缺失',
};

const AssetCard: React.FC<{
  icon: string;
  title: string;
  value: React.ReactNode;
  sub?: React.ReactNode;
  latest?: string;
  staleness?: Staleness;
  action?: { label: string; onClick: () => void; busy?: boolean; disabled?: boolean };
}> = ({ icon, title, value, sub, latest, staleness, action }) => (
  <Card className="p-4 flex flex-col gap-1.5">
    <div className="flex items-center justify-between">
      <div className="text-xs text-gray-500">
        {icon} {title}
      </div>
      {staleness && (
        <span className={`text-[10px] px-1.5 py-0.5 rounded-full ${stalenessStyle[staleness]}`}>
          {stalenessLabel[staleness]}
        </span>
      )}
    </div>
    <div className="text-2xl font-bold text-white">{value}</div>
    {sub && <div className="text-xs text-gray-400">{sub}</div>}
    <div className="flex items-center justify-between mt-auto pt-1.5">
      <span className="text-[11px] text-gray-500">{latest || ''}</span>
      {action && (
        <Button
          variant="subtle"
          size="sm"
          loading={action.busy}
          disabled={action.disabled}
          onClick={action.onClick}
        >
          {action.label}
        </Button>
      )}
    </div>
  </Card>
);

/* ---------- 流式进度卡（日线/财报共用） ---------- */

const ABORT_REASON_LABEL: Record<string, string> = {
  proxy_pool_dead: '代理与直连均不可用，已中止',
  stalled: '任务长时间无响应，已中止',
  workers_exited: '后台任务异常退出，已中止',
};

const ProgressCard: React.FC<{
  progress: UpdateStreamEvent;
  runningTitle: string;
}> = ({ progress, runningTitle }) => (
  <Card className="p-4">
    <div className="flex items-center justify-between mb-2">
      <div className="flex items-center gap-2">
        <span className="text-sm text-white font-medium">
          {progress.event === 'complete'
            ? '✅ 更新完成'
            : progress.event === 'error'
            ? '❌ 更新出错'
            : runningTitle}
        </span>
        {progress.proxy_state === 'open' && (
          <span className={`text-[10px] px-1.5 py-0.5 rounded-full ${stalenessStyle.warn}`}>
            代理异常·直连降级
          </span>
        )}
        {progress.proxy_state === 'dead' && (
          <span className={`text-[10px] px-1.5 py-0.5 rounded-full ${stalenessStyle.stale}`}>
            数据源不可达
          </span>
        )}
      </div>
      {progress.total ? (
        <span className="text-xs text-gray-400">
          {progress.current || 0} / {progress.total}
        </span>
      ) : null}
    </div>
    {progress.total ? (
      <div className="w-full h-2 bg-dark-light rounded-full overflow-hidden">
        <div
          className="h-full bg-primary transition-all"
          style={{
            width: `${Math.min(100, ((progress.current || 0) / (progress.total || 1)) * 100)}%`,
          }}
        />
      </div>
    ) : null}
    <div className="text-xs text-gray-400 mt-2">
      {progress.event === 'complete'
        ? `${progress.aborted && progress.abort_reason ? `${ABORT_REASON_LABEL[progress.abort_reason] || '已中止'} · ` : ''}` +
          `成功 ${fmtNum(progress.success)} · 跳过 ${fmtNum(progress.skipped ?? progress.skipped_fresh)} · 失败 ${fmtNum(progress.failed)}`
        : progress.event === 'error'
        ? progress.message
        : progress.note
        ? progress.note
        : progress.symbol
        ? `当前: ${progress.symbol}`
        : '准备中…'}
      {progress.event === 'progress' && !!progress.retries && ` · 重试 ${progress.retries}`}
    </div>
    {progress.event === 'complete' && !!progress.backfilled_count && (
      <div className="mt-2 pt-2 border-t border-dark-light">
        <div className="text-xs text-gray-400 mb-1">
          补齐 {fmtNum(progress.backfilled_count)} 只股票（落后不止 1 天，已补齐历史数据）
          {progress.backfilled_count > (progress.backfilled_stocks?.length || 0) &&
            `，仅显示前 ${progress.backfilled_stocks?.length} 只`}
        </div>
        <div className="max-h-32 overflow-y-auto flex flex-wrap gap-1">
          {progress.backfilled_stocks?.map((s) => (
            <span
              key={s.symbol}
              className="text-[11px] px-1.5 py-0.5 rounded bg-dark-light text-gray-300"
              title={s.symbol}
            >
              {s.name || s.symbol} +{s.days}天
            </span>
          ))}
        </div>
      </div>
    )}
  </Card>
);

/* ---------- 三市场（+加密只读）新鲜度条带 ---------- */

const MARKET_META: Record<string, { label: string; icon: string }> = {
  a_share: { label: 'A股', icon: '🇨🇳' },
  hk_stock: { label: '港股', icon: '🇭🇰' },
  us_stock: { label: '美股', icon: '🇺🇸' },
  crypto: { label: '加密', icon: '🪙' },
};

const MARKET_ORDER = ['a_share', 'hk_stock', 'us_stock', 'crypto'];

/** 单市场新鲜度 chip：更新到哪天 + 是否落后徽章 */
const FreshnessChip: React.FC<{ market: string; info: import('../services/dataMonitorService').MarketFreshness }> = ({ market, info }) => {
  const meta = MARKET_META[market] || { label: market, icon: '•' };
  const st: Staleness = info.is_stale ? 'stale' : 'ok';
  return (
    <div className="flex-1 min-w-[130px] bg-dark-light rounded-lg px-3 py-2 flex items-center justify-between gap-2">
      <div className="flex items-center gap-1.5 min-w-0">
        <span>{meta.icon}</span>
        <div className="min-w-0">
          <div className="text-xs text-gray-300 font-medium flex items-center gap-1">
            {meta.label}
            {market === 'crypto' && <span className="text-[9px] text-gray-500">只读</span>}
          </div>
          <div className="text-[10px] text-gray-500 truncate">{info.reference_date || '无数据'}</div>
        </div>
      </div>
      <span className={`text-[10px] px-1.5 py-0.5 rounded-full shrink-0 ${stalenessStyle[st]}`}>
        {info.is_stale ? '落后' : '最新'}
      </span>
    </div>
  );
};

const MarketFreshnessStrip: React.FC<{ freshness: FreshnessReport | null }> = ({ freshness }) => {
  if (!freshness?.by_market) return null;
  const markets = MARKET_ORDER.filter((m) => freshness.by_market[m]);
  if (markets.length === 0) return null;
  return (
    <Card className="p-3">
      <div className="text-xs font-semibold text-gray-400 mb-2 px-1">📅 各市场行情新鲜度</div>
      <div className="flex flex-wrap gap-2">
        {markets.map((m) => (
          <FreshnessChip key={m} market={m} info={freshness.by_market[m]} />
        ))}
      </div>
    </Card>
  );
};

/** 统一刷新的分市场进度行 */
const RefreshProgressRow: React.FC<{ market: string; evt: RefreshStreamEvent }> = ({ market, evt }) => {
  const meta = MARKET_META[market] || { label: market, icon: '•' };
  const pct = evt.total ? Math.min(100, ((evt.current || 0) / (evt.total || 1)) * 100) : evt.event === 'complete' ? 100 : 0;
  const statusText =
    evt.event === 'complete'
      ? `✅ 完成 · 更新 ${fmtNum(evt.updated ?? evt.success)} · 失败 ${fmtNum(evt.failed)}`
      : evt.event === 'error'
      ? `❌ ${evt.message || '出错'}`
      : evt.event === 'skipped'
      ? `⏭ ${evt.message || '已跳过'}`
      : evt.symbol
      ? `当前: ${evt.name || evt.symbol}`
      : '准备中…';
  return (
    <div className="mb-2 last:mb-0">
      <div className="flex items-center justify-between text-xs mb-1">
        <span className="text-gray-300">{meta.icon} {meta.label}</span>
        {evt.total ? <span className="text-gray-500">{evt.current || 0} / {evt.total}</span> : null}
      </div>
      <div className="w-full h-2 bg-dark-light rounded-full overflow-hidden">
        <div
          className={`h-full transition-all ${evt.event === 'error' ? 'bg-bull' : evt.event === 'skipped' ? 'bg-gray-500' : 'bg-primary'}`}
          style={{ width: `${pct}%` }}
        />
      </div>
      <div className="text-[11px] text-gray-500 mt-1">{statusText}</div>
    </div>
  );
};

/* ================================================================ */

export const DataMonitor: React.FC = () => {
  const [data, setData] = useState<MonitorOverview | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [toggling, setToggling] = useState(false);
  const [knowledgeStats, setKnowledgeStats] = useState<KnowledgeStats | null>(null);

  // 日线手动更新流式状态
  const [updating, setUpdating] = useState(false);
  const [progress, setProgress] = useState<UpdateStreamEvent | null>(null);
  const abortRef = useRef<AbortController | null>(null);

  // 三市场统一刷新流式状态（分市场进度）+ 只读新鲜度
  const [mktFreshness, setMktFreshness] = useState<FreshnessReport | null>(null);
  const [refreshing, setRefreshing] = useState(false);
  const [refreshProgress, setRefreshProgress] = useState<Record<string, RefreshStreamEvent>>({});
  const refreshAbortRef = useRef<AbortController | null>(null);

  // 财报回补流式状态
  const [finUpdating, setFinUpdating] = useState(false);
  const [finProgress, setFinProgress] = useState<UpdateStreamEvent | null>(null);
  const finAbortRef = useRef<AbortController | null>(null);

  // 一次性触发（实时/估值/新闻）的忙碌标记
  const [busyAsset, setBusyAsset] = useState<string | null>(null);

  // 涨停池详情展开（懒加载：点开才拉完整股票清单，别每轮轮询都拉）
  const [limitUpDetailOpen, setLimitUpDetailOpen] = useState(false);
  const [limitUpDetail, setLimitUpDetail] = useState<LimitUpDetail | null>(null);
  const [limitUpDetailLoading, setLimitUpDetailLoading] = useState(false);
  const [limitUpTab, setLimitUpTab] = useState<'boards' | 'zhaban'>('boards');

  const pollRef = useRef<ReturnType<typeof setInterval> | null>(null);

  const load = useCallback(async () => {
    try {
      const [res, fresh] = await Promise.all([
        dataMonitorService.getOverview(),
        dataMonitorService.getFreshness().catch(() => null), // 新鲜度失败不拖垮总览
      ]);
      setData(res);
      if (fresh) setMktFreshness(fresh);
      setError(null);
    } catch (e: any) {
      setError(e?.message || '加载失败');
    } finally {
      setLoading(false);
    }
  }, []);

  // 知识库统计：单独的库 + 向量索引，较重，只在挂载时取一次
  useEffect(() => {
    knowledgeService
      .getStats()
      .then(setKnowledgeStats)
      .catch(() => setKnowledgeStats(null));
  }, []);

  // 更新是否正在进行：任一流式更新中 或 定时任务后台在跑
  const isActive =
    updating || refreshing || finUpdating || busyAsset !== null || (data?.scheduler.is_updating ?? false);

  // 自适应轮询：active 时 5s 快刷，空闲 60s 慢刷。isActive 翻转时立即重拉一次。
  useEffect(() => {
    load();
    const interval = isActive ? ACTIVE_POLL_MS : IDLE_POLL_MS;
    pollRef.current = setInterval(load, interval);
    return () => {
      if (pollRef.current) clearInterval(pollRef.current);
    };
  }, [load, isActive]);

  // 卸载时中断进行中的更新流
  useEffect(
    () => () => {
      abortRef.current?.abort();
      finAbortRef.current?.abort();
      refreshAbortRef.current?.abort();
    },
    [],
  );

  const handleToggle = async () => {
    if (!data) return;
    setToggling(true);
    try {
      const next = !data.scheduler.enabled;
      const sched = await dataMonitorService.toggleScheduler(next);
      setData((d) => (d ? { ...d, scheduler: sched } : d));
    } catch (e: any) {
      setError(e?.message || '切换失败');
    } finally {
      setToggling(false);
    }
  };

  const handleManualUpdate = async () => {
    if (updating) return;
    setUpdating(true);
    setProgress({ event: 'start' });
    const ctrl = new AbortController();
    abortRef.current = ctrl;
    try {
      await dataMonitorService.streamUpdate((evt) => setProgress(evt), ctrl.signal);
    } catch (e: any) {
      if (e?.name !== 'AbortError') {
        setProgress({ event: 'error', message: e?.message || '更新失败' });
      }
    } finally {
      setUpdating(false);
      abortRef.current = null;
      load(); // 更新完刷新总览
    }
  };

  /** 三市场统一刷新（scope 省略=A股+港股+美股；crypto 不走这里）。事件按 market 分栏 */
  const handleUnifiedRefresh = async () => {
    if (refreshing) return;
    setRefreshing(true);
    setRefreshProgress({});
    const ctrl = new AbortController();
    refreshAbortRef.current = ctrl;
    try {
      await dataMonitorService.refreshMarkets(
        undefined,
        (evt) => {
          if (evt.event === 'plan') {
            if (evt.freshness) setMktFreshness(evt.freshness);
            return;
          }
          if (evt.event === 'all_complete') return;
          if (evt.market) {
            setRefreshProgress((prev) => ({ ...prev, [evt.market as string]: evt }));
          }
        },
        ctrl.signal,
      );
    } catch (e: any) {
      if (e?.name !== 'AbortError') {
        setError(e?.message || '刷新失败');
      }
    } finally {
      setRefreshing(false);
      refreshAbortRef.current = null;
      load(); // 刷新完重拉总览+新鲜度
    }
  };

  const handleStopRefresh = () => {
    refreshAbortRef.current?.abort();
  };

  const handleFinancialBackfill = async () => {
    if (finUpdating) return;
    setFinUpdating(true);
    setFinProgress({ event: 'start' });
    const ctrl = new AbortController();
    finAbortRef.current = ctrl;
    try {
      await dataMonitorService.streamFinancialBackfill(
        { mode: 'incremental' },
        (evt) => setFinProgress(evt),
        ctrl.signal,
      );
    } catch (e: any) {
      if (e?.name !== 'AbortError') {
        setFinProgress({ event: 'error', message: e?.message || '财务回补失败' });
      }
    } finally {
      setFinUpdating(false);
      finAbortRef.current = null;
      load();
    }
  };

  const handleStopFinBackfill = () => {
    finAbortRef.current?.abort();
  };

  /** 实时/估值/新闻这类"点一下等结果"的触发，统一 busy 管理 */
  const runOneShot = async (key: string, fn: () => Promise<unknown>) => {
    if (busyAsset) return;
    setBusyAsset(key);
    try {
      await fn();
    } catch (e: any) {
      setError(e?.message || '操作失败');
    } finally {
      setBusyAsset(null);
      load();
    }
  };

  const toggleLimitUpDetail = async () => {
    const next = !limitUpDetailOpen;
    setLimitUpDetailOpen(next);
    if (next) {
      // 面板是条件渲染的，展开那一刻 DOM 还没挂载，下一帧再滚，避免用户以为"点了没反应"
      requestAnimationFrame(() => {
        setTimeout(() => {
          document.getElementById('limit-up-detail-panel')?.scrollIntoView({ behavior: 'smooth', block: 'start' });
        }, 0);
      });
    }
    if (next && !limitUpDetail) {
      setLimitUpDetailLoading(true);
      try {
        const detail = await dataMonitorService.getLimitUpDetail();
        setLimitUpDetail(detail);
      } catch (e: any) {
        setError(e?.message || '涨停池详情加载失败');
      } finally {
        setLimitUpDetailLoading(false);
      }
    }
  };

  if (loading) {
    return (
      <div className="max-w-7xl mx-auto p-6 space-y-6">
        <SkeletonMetricGrid count={7} />
        <SkeletonCard />
      </div>
    );
  }

  if (error && !data) {
    return (
      <div className="min-h-screen bg-gradient-dark p-6">
        <div className="max-w-3xl mx-auto">
          <Card className="p-6 text-center">
            <div className="text-bull text-lg mb-2">⚠️ 加载失败</div>
            <div className="text-gray-400 text-sm mb-4">{error}</div>
            <Button variant="ghost" onClick={load}>重试</Button>
          </Card>
        </div>
      </div>
    );
  }

  if (!data) return null;

  const { coverage, freshness, assets, scheduler } = data;

  // 顶部横幅判定：今日工作日但数据未到 / 上次更新失败 / 覆盖率偏低
  const lastFailed = coverage.last_update?.status === 'failed';
  const dataMissing = freshness.is_weekday && freshness.is_stale;
  const lowCoverage = coverage.coverage_pct > 0 && coverage.coverage_pct < 90;
  const hasAlert = lastFailed || dataMissing || lowCoverage;

  const bannerMsg = lastFailed
    ? '上次数据更新失败，建议手动补更新'
    : dataMissing
    ? '今日为交易日，但行情数据尚未更新到今天'
    : lowCoverage
    ? `数据覆盖率偏低（${coverage.coverage_pct}%），部分股票可能缺数据`
    : '数据已就绪，今日无异常';

  // 各资产新鲜度判定
  const dailyStaleness: Staleness = lastFailed || dataMissing ? 'stale' : lowCoverage ? 'warn' : 'ok';
  const realtimeStaleness: Staleness = !assets.realtime.latest_snapshot
    ? 'stale'
    : assets.realtime.is_today
    ? 'ok'
    : 'warn';
  const finRecentRatio =
    assets.financial.total_stocks > 0
      ? assets.financial.symbols_recent_report / assets.financial.total_stocks
      : 0;
  const financialStaleness: Staleness =
    assets.financial.symbols_with_data === 0 ? 'stale' : finRecentRatio < 0.8 ? 'warn' : 'ok';
  const valuationStaleness: Staleness =
    assets.valuation.days_old === null || assets.valuation.days_old > 7
      ? 'stale'
      : assets.valuation.days_old > 3
      ? 'warn'
      : 'ok';
  const newsAge = daysAgo(assets.news.latest_published_at);
  const newsStaleness: Staleness =
    newsAge === null || newsAge > 14 ? 'stale' : newsAge > 3 ? 'warn' : 'ok';
  const limitUpStaleness: Staleness = !assets.limit_up.latest_date
    ? 'stale'
    : assets.limit_up.is_today
    ? 'ok'
    : 'warn';

  return (
    <div className="min-h-screen bg-gradient-dark p-6">
      <div className="max-w-7xl mx-auto space-y-6">
        {/* 标题 */}
        <div className="flex items-center justify-between flex-wrap gap-3">
          <div>
            <h1 className="text-xl font-bold text-white flex items-center gap-2">
              🛰️ 数据监控
            </h1>
            <p className="text-xs text-gray-500 mt-1">
              基础数据资产健康状态 ·{' '}
              {isActive ? (
                <span className="text-primary">更新中 · 快刷 {ACTIVE_POLL_MS / 1000}s</span>
              ) : (
                <span>空闲 · 慢刷 {IDLE_POLL_MS / 1000}s</span>
              )}{' '}
              · 服务器时间 {fmtTime(data.server_time)}
            </p>
          </div>
          <div className="flex items-center gap-2">
            {refreshing ? (
              <Button variant="danger" size="sm" onClick={handleStopRefresh} icon="⏹">
                停止刷新
              </Button>
            ) : (
              <Button variant="primary" size="sm" onClick={handleUnifiedRefresh} icon="🔄">
                更新日线（三市场）
              </Button>
            )}
          </div>
        </div>

        {/* 三市场（+加密只读）新鲜度条带 */}
        <MarketFreshnessStrip freshness={mktFreshness} />

        {/* 三市场统一刷新进度（按 market 分栏） */}
        {(refreshing || Object.keys(refreshProgress).length > 0) && (
          <Card className="p-4">
            <div className="text-sm text-white font-medium mb-3">
              {refreshing ? '⏳ 正在统一刷新三市场日线…' : '三市场刷新结果'}
            </div>
            {MARKET_ORDER.filter((m) => refreshProgress[m]).map((m) => (
              <RefreshProgressRow key={m} market={m} evt={refreshProgress[m]} />
            ))}
            {refreshing && Object.keys(refreshProgress).length === 0 && (
              <div className="text-xs text-gray-500">准备中…</div>
            )}
          </Card>
        )}

        {/* 漏跑补跑横幅：今日该更却没更（后端判定），点了才花 IP */}
        {data.catch_up?.needed && !updating && (
          <div className="rounded-xl px-4 py-3 border border-amber-500/50 bg-amber-500/10 flex items-center justify-between gap-3 flex-wrap">
            <div className="flex items-center gap-2 text-sm text-amber-300">
              <span>⏰</span>
              <span>
                今日自动更新未执行（{data.catch_up.scheduled_time} 时后端可能不在线），
                数据还停在 <span className="font-medium">{data.catch_up.last_daily_update || '—'}</span>
              </span>
            </div>
            <Button variant="primary" size="sm" onClick={handleManualUpdate} icon="🔄">
              立即补跑
            </Button>
          </div>
        )}

        {/* 顶部告警横幅 */}
        <div
          className={`rounded-xl px-4 py-3 text-sm border flex items-center gap-2 ${
            hasAlert
              ? 'bg-bull/10 border-bull/40 text-bull'
              : 'bg-bear/10 border-bear/40 text-bear'
          }`}
        >
          <span>{hasAlert ? '⚠️' : '✅'}</span>
          <span>{bannerMsg}</span>
        </div>

        {/* 日线更新进度条 */}
        {progress && (updating || progress.event === 'complete' || progress.event === 'error') && (
          <ProgressCard progress={progress} runningTitle="⏳ 正在更新全市场日线…" />
        )}

        {/* 财报回补进度条 */}
        {finProgress &&
          (finUpdating || finProgress.event === 'complete' || finProgress.event === 'error') && (
            <ProgressCard progress={finProgress} runningTitle="⏳ 正在补齐财报数据…" />
          )}

        {/* 抓取任务独立监控区（带 sessionID，含聊天里触发的抓取） */}
        <ScrapeMonitorPanel />

        {/* 全市场 A股法定财报批量摄入（常驻后台任务，起停+轮询） */}
        <CninfoIngestPanel />

        {/* 全市场东财研报批量摄入（常驻后台任务，起停+轮询） */}
        <ResearchReportIngestPanel />

        {/* arXiv 论文摄入（一次性同步慢任务，官方API） */}
        <ArxivIngestPanel />

        {/* 深历史日线回补（A股+港股+美股 合并面板，一键全部开始） */}
        <DeepHistoryPanel />

        {/* Newnew 新闻定时任务（15分钟抓取+分析，常驻后台任务，起停+轮询） */}
        <NewsJobPanel />

        {/* 数据资产网格 */}
        <div className="grid grid-cols-2 md:grid-cols-3 gap-4">
          <AssetCard
            icon="📈"
            title="日线K线"
            staleness={dailyStaleness}
            value={<span className="text-primary">{coverage.coverage_pct}%</span>}
            sub={`${fmtNum(coverage.stocks_at_latest)} / ${fmtNum(coverage.total_stocks)} 只到最新`}
            latest={`最新 ${coverage.latest_date || '-'}`}
            action={{
              label: updating ? '更新中' : '更新',
              onClick: handleManualUpdate,
              busy: updating,
              disabled: updating,
            }}
          />
          <AssetCard
            icon="⚡"
            title="实时快照"
            staleness={realtimeStaleness}
            value={fmtNum(assets.realtime.count_at_latest)}
            sub={assets.realtime.is_today ? '今日已有快照' : '今日暂无快照'}
            latest={`最新 ${fmtTime(assets.realtime.latest_snapshot)}`}
            action={{
              label: '拉取',
              onClick: () =>
                runOneShot('realtime', () =>
                  api.get('/realtime/quotes', { params: { save: true, force: true } }),
                ),
              busy: busyAsset === 'realtime',
              disabled: busyAsset !== null,
            }}
          />
          <AssetCard
            icon="📊"
            title="财报"
            staleness={financialStaleness}
            value={<span className="text-primary">{assets.financial.coverage_pct}%</span>}
            sub={`${fmtNum(assets.financial.symbols_with_data)} / ${fmtNum(assets.financial.total_stocks)} 只有数据 · 近180天有报 ${fmtNum(assets.financial.symbols_recent_report)} 只`}
            latest={`最新报告期 ${assets.financial.latest_report_date || '-'}`}
            action={{
              label: finUpdating ? '停止' : '补齐',
              onClick: finUpdating ? handleStopFinBackfill : handleFinancialBackfill,
              busy: false,
            }}
          />
          <AssetCard
            icon="💹"
            title="估值"
            staleness={valuationStaleness}
            value={fmtNum(assets.valuation.count_at_latest)}
            sub={
              assets.valuation.days_old === null
                ? '暂无估值快照'
                : `距今 ${assets.valuation.days_old} 天`
            }
            latest={`最新 ${assets.valuation.latest_date || '-'}`}
            action={{
              label: '刷新',
              onClick: () =>
                runOneShot('valuation', () => screenerService.refreshValuation(() => {})),
              busy: busyAsset === 'valuation',
              disabled: busyAsset !== null,
            }}
          />
          <AssetCard
            icon="📰"
            title="新闻"
            staleness={newsStaleness}
            value={fmtNum(assets.news.last_7d)}
            sub={`近 7 天条数 · 库存共 ${fmtNum(assets.news.total)} 条`}
            latest={`最新 ${fmtTime(assets.news.latest_published_at)}`}
            action={{
              label: '抓取',
              onClick: () =>
                runOneShot('news', () => newsService.fetchNews({ market: 'a_share' }, () => {})),
              busy: busyAsset === 'news',
              disabled: busyAsset !== null,
            }}
          />
          <AssetCard
            icon="🔥"
            title="涨停池"
            staleness={limitUpStaleness}
            value={<span className="text-primary">{fmtNum(assets.limit_up.count_today)}</span>}
            sub={
              <div className="flex items-center justify-between gap-2">
                <span className="truncate">
                  连板梯队 {assets.limit_up.ladder_summary}
                  {assets.limit_up.break_rate !== null && assets.limit_up.break_rate !== undefined
                    ? ` · 炸板率 ${assets.limit_up.break_rate}%`
                    : ''}
                </span>
                <button
                  type="button"
                  onClick={toggleLimitUpDetail}
                  className="text-primary text-[11px] shrink-0 hover:underline"
                >
                  {limitUpDetailOpen ? '收起 ↑' : '看股票 →'}
                </button>
              </div>
            }
            latest={`最新 ${assets.limit_up.latest_date || '-'}`}
            action={{
              label: '刷新',
              onClick: () =>
                runOneShot('limit_up', async () => {
                  const r = await dataMonitorService.refreshLimitUp();
                  setLimitUpDetail(null); // 数据变了，作废缓存详情，下次展开重新拉
                  if (limitUpDetailOpen) {
                    dataMonitorService.getLimitUpDetail().then(setLimitUpDetail).catch(() => {});
                  }
                  return r;
                }),
              busy: busyAsset === 'limit_up',
              disabled: busyAsset !== null,
            }}
          />
          <AssetCard
            icon="📚"
            title="研报 / 知识库"
            value={knowledgeStats ? fmtNum(knowledgeStats.documents) : '-'}
            sub={
              knowledgeStats
                ? `${fmtNum(knowledgeStats.chunks)} 个知识块 · ${fmtNum(knowledgeStats.ideas)} 条想法`
                : '知识库未连接'
            }
            action={{
              label: '查看详情 ↓',
              onClick: () =>
                document.getElementById('knowledge-panel')?.scrollIntoView({ behavior: 'smooth', block: 'start' }),
            }}
          />
        </div>

        {/* 涨停池详情：完整股票清单，内部滚动，不占整页 */}
        {limitUpDetailOpen && (
          <Card id="limit-up-detail-panel" className="p-4">
            <div className="flex items-center justify-between flex-wrap gap-2 mb-3">
              <div className="text-sm text-white font-medium flex items-center gap-2">
                🔥 涨停池详情
                {limitUpDetail?.trade_date && (
                  <span className="text-xs text-gray-500 font-normal">
                    {limitUpDetail.trade_date}
                    {limitUpDetail.provisional && ' · 盘中实时'}
                  </span>
                )}
              </div>
              <div className="flex items-center gap-1">
                <button
                  type="button"
                  onClick={() => setLimitUpTab('boards')}
                  className={`text-xs px-2.5 py-1 rounded-lg ${
                    limitUpTab === 'boards' ? 'bg-primary/20 text-primary' : 'text-gray-500 hover:text-gray-300'
                  }`}
                >
                  连板榜 {limitUpDetail ? `(${limitUpDetail.top_boards?.length ?? 0})` : ''}
                </button>
                <button
                  type="button"
                  onClick={() => setLimitUpTab('zhaban')}
                  className={`text-xs px-2.5 py-1 rounded-lg ${
                    limitUpTab === 'zhaban' ? 'bg-primary/20 text-primary' : 'text-gray-500 hover:text-gray-300'
                  }`}
                >
                  炸板榜 {limitUpDetail ? `(${limitUpDetail.top_zhaban?.length ?? 0})` : ''}
                </button>
              </div>
            </div>

            {limitUpDetailLoading ? (
              <div className="text-xs text-gray-500 py-6 text-center">加载中…</div>
            ) : !limitUpDetail ? (
              <div className="text-xs text-gray-500 py-6 text-center">暂无数据</div>
            ) : (
              <>
                {limitUpDetail.profit_effect && typeof limitUpDetail.profit_effect.avg_change_pct === 'number' && (
                  <div className="text-[11px] text-gray-500 mb-2">
                    昨日涨停股今日平均{' '}
                    <span className={limitUpDetail.profit_effect.avg_change_pct >= 0 ? 'text-bull' : 'text-bear'}>
                      {limitUpDetail.profit_effect.avg_change_pct >= 0 ? '+' : ''}
                      {limitUpDetail.profit_effect.avg_change_pct.toFixed(1)}%
                    </span>
                    {typeof limitUpDetail.profit_effect.promotion_count === 'number' &&
                      ` · 晋级(再涨停) ${limitUpDetail.profit_effect.promotion_count} 家`}
                  </div>
                )}

                {/* 内部滚动区：股票清单不撑爆整页，固定高度 + 滚动条 */}
                <div className="max-h-96 overflow-y-auto pr-1 space-y-1.5">
                  {limitUpTab === 'boards' &&
                    (limitUpDetail.top_boards?.length ? (
                      limitUpDetail.top_boards.map((b) => (
                        <div
                          key={b.symbol}
                          className="bg-dark-light rounded-lg p-2.5 flex items-center justify-between gap-2 flex-wrap"
                        >
                          <div className="flex items-center gap-2 min-w-0">
                            <span className="text-white font-medium truncate">{b.name}</span>
                            <span className="text-gray-500 text-[10px]">{b.symbol}</span>
                            {b.industry && <span className="text-[10px] text-gray-500">{b.industry}</span>}
                          </div>
                          <div className="flex items-center gap-3 text-[11px] text-gray-400 shrink-0">
                            <span className="text-bull font-semibold">{b.consecutive_boards ?? '—'}板</span>
                            {b.zt_stat && <span>{b.zt_stat}</span>}
                            {typeof b.seal_amount === 'number' && (
                              <span>封 {(b.seal_amount / 1e8).toFixed(2)}亿</span>
                            )}
                          </div>
                        </div>
                      ))
                    ) : (
                      <div className="text-xs text-gray-500 py-4 text-center">今日暂无涨停股</div>
                    ))}

                  {limitUpTab === 'zhaban' &&
                    (limitUpDetail.top_zhaban?.length ? (
                      limitUpDetail.top_zhaban.map((z) => (
                        <div
                          key={z.symbol}
                          className="bg-dark-light rounded-lg p-2.5 flex items-center justify-between gap-2 flex-wrap"
                        >
                          <div className="flex items-center gap-2 min-w-0">
                            <span className="text-white font-medium truncate">{z.name}</span>
                            <span className="text-gray-500 text-[10px]">{z.symbol}</span>
                            {z.industry && <span className="text-[10px] text-gray-500">{z.industry}</span>}
                          </div>
                          <span className="text-[11px] text-bear">炸板 {z.break_count ?? '—'} 次</span>
                        </div>
                      ))
                    ) : (
                      <div className="text-xs text-gray-500 py-4 text-center">今日暂无炸板股</div>
                    ))}
                </div>

                <div className="mt-3 text-[10px] text-gray-500">
                  已封板涨停股买不进，仅作复盘参考；想看能买的候选去 MoneyBill 问「明天有涨停苗头吗」
                </div>
              </>
            )}
          </Card>
        )}

        {/* 定时任务状态 */}
        <Card className="p-4">
          <div className="flex items-center justify-between flex-wrap gap-3">
            <div>
              <div className="text-sm text-white font-medium flex items-center gap-2">
                ⏰ 每日自动更新
                <span
                  className={`text-xs px-2 py-0.5 rounded-full ${
                    scheduler.enabled
                      ? 'bg-bear/15 text-bear'
                      : 'bg-gray-500/15 text-gray-400'
                  }`}
                >
                  {scheduler.enabled ? '已开启' : '已关闭'}
                </span>
                {scheduler.is_updating && (
                  <span className="text-xs px-2 py-0.5 rounded-full bg-yellow-400/15 text-yellow-400">
                    运行中
                  </span>
                )}
              </div>
              <div className="text-xs text-gray-400 mt-1">
                计划: {scheduler.cron} · 下次运行: {fmtTime(scheduler.next_run)}
              </div>
              <div className="text-xs text-gray-500 mt-0.5">
                链条: 更新日线 {scheduler.chain_signals ? '→ 补信号' : ''} {scheduler.chain_tracking ? '→ 更新追踪' : ''}
              </div>
            </div>
            <Button
              variant={scheduler.enabled ? 'subtle' : 'primary'}
              size="sm"
              loading={toggling}
              onClick={handleToggle}
            >
              {scheduler.enabled ? '关闭自动更新' : '开启自动更新'}
            </Button>
          </div>
          {scheduler.last_run && (
            <div className="text-xs text-gray-500 mt-3 pt-3 border-t border-border">
              上次链条运行: {scheduler.last_run.ok === false ? '有步骤失败' : '正常'} ·{' '}
              {fmtTime(scheduler.last_run.completed_at || scheduler.last_run.started_at)}
            </div>
          )}
        </Card>

        {/* 最近更新日志 */}
        <Card className="p-4">
          <div className="text-sm text-white font-medium mb-3">📋 最近更新日志</div>
          {data.recent_update_logs.length === 0 ? (
            <div className="text-xs text-gray-500 py-4 text-center">暂无更新记录</div>
          ) : (
            <div className="overflow-x-auto">
              <div className="min-w-[640px]">
                <div className="grid grid-cols-6 gap-2 text-xs text-gray-500 pb-2 border-b border-border">
                  <div>类型</div>
                  <div>状态</div>
                  <div className="text-right">股票数</div>
                  <div className="text-right">记录数</div>
                  <div className="text-right">耗时</div>
                  <div className="text-right">完成时间</div>
                </div>
                {data.recent_update_logs.map((lg) => (
                  <div
                    key={lg.id}
                    className="grid grid-cols-6 gap-2 text-xs py-2 border-b border-border/50 last:border-0 text-gray-300"
                  >
                    <div>{lg.update_type}</div>
                    <div className={statusColor(lg.status)}>{statusLabel(lg.status)}</div>
                    <div className="text-right">{fmtNum(lg.symbols_count)}</div>
                    <div className="text-right">{fmtNum(lg.records_count)}</div>
                    <div className="text-right">{fmtDur(lg.duration_seconds)}</div>
                    <div className="text-right text-gray-400">{fmtTime(lg.completed_at)}</div>
                  </div>
                ))}
              </div>
            </div>
          )}
        </Card>
      </div>

      {/* 知识库（未曾有独立页面，收敛为本页常驻面板） */}
      <div className="mt-4">
        <KnowledgePanel stats={knowledgeStats} />
      </div>
    </div>
  );
};

export default DataMonitor;

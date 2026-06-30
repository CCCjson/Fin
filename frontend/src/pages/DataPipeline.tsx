import React, { useState, useCallback, useRef, useEffect } from 'react';

const API = '/api';

// ── 类型 ──

interface TaskProgress {
  task_id: string;
  state: string;
  total: number;
  done: number;
  failed: number;
  rows_written: number;
  elapsed_seconds: number;
  current_symbol: string;
  error_message: string;
  speed: number;
  proxy_switches?: number;
}

interface GlobalStats {
  total_requests: number;
  total_rows_written: number;
  total_errors: number;
  uptime_seconds: number;
  avg_speed: number;
}

// ── 主组件 ──

export const DataPipeline: React.FC = () => {
  const [symbols, setSymbols] = useState('');
  const [beginDate, setBeginDate] = useState('20250101');
  const [endDate, setEndDate] = useState('20260225');
  const [threadCount, setThreadCount] = useState(8);
  const [batchSize, setBatchSize] = useState(500);
  const [switchIpEvery] = useState(800);

  const [taskId, setTaskId] = useState('');
  const [progress, setProgress] = useState<TaskProgress | null>(null);
  const [globalStats, setGlobalStats] = useState<GlobalStats | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState('');

  const intervalRef = useRef<ReturnType<typeof setInterval> | undefined>(undefined);

  const pollProgress = useCallback(async (tid: string) => {
    try {
      const res = await fetch(`${API}/pipeline/status/${tid}`);
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      const data: TaskProgress = await res.json();
      setProgress(data);
      if (['COMPLETED', 'FAILED', 'STOPPED'].includes(data.state)) {
        if (intervalRef.current) { clearInterval(intervalRef.current); intervalRef.current = undefined; }
      }
    } catch (e: any) { setError(e.message); }
  }, []);

  const fetchStats = useCallback(async () => {
    try {
      const res = await fetch(`${API}/pipeline/stats`);
      if (res.ok) setGlobalStats(await res.json());
    } catch {}
  }, []);

  const submitTask = async (params: {
    symbols: string[]; begin_date: string; end_date: string;
    thread_count: number; batch_size: number;
    switch_ip_every: number;
  }) => {
    setError('');
    setLoading(true);
    try {
      const res = await fetch(`${API}/pipeline/fetch`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(params),
      });
      if (!res.ok) { const err = await res.json(); throw new Error(err.detail || `HTTP ${res.status}`); }
      const data = await res.json();
      setTaskId(data.task_id);
      setProgress(null);
      if (intervalRef.current) clearInterval(intervalRef.current);
      intervalRef.current = setInterval(() => pollProgress(data.task_id), 2000);
      pollProgress(data.task_id);
    } catch (e: any) { setError(e.message); } finally { setLoading(false); }
  };

  const handleSubmit = () => {
    const symList = symbols.trim() ? symbols.split(',').map(s => s.trim()).filter(Boolean) : [];
    submitTask({ symbols: symList, begin_date: beginDate, end_date: endDate, thread_count: threadCount, batch_size: batchSize, switch_ip_every: switchIpEvery });
  };

  const handleUpdateToday = () => {
    const today = new Date();
    const yyyymmdd = today.getFullYear().toString()
      + (today.getMonth() + 1).toString().padStart(2, '0')
      + today.getDate().toString().padStart(2, '0');
    submitTask({ symbols: [], begin_date: '20100101', end_date: yyyymmdd, thread_count: threadCount, batch_size: batchSize, switch_ip_every: switchIpEvery });
  };

  const handleStop = async () => {
    if (!taskId) return;
    try { await fetch(`${API}/pipeline/stop/${taskId}`, { method: 'POST' }); } catch (e: any) { setError(e.message); }
  };

  useEffect(() => { fetchStats(); const t = setInterval(fetchStats, 5000); return () => clearInterval(t); }, [fetchStats]);
  useEffect(() => () => { if (intervalRef.current) clearInterval(intervalRef.current); }, []);

  const progressPct = progress && progress.total > 0 ? Math.round((progress.done / progress.total) * 100) : 0;
  const isRunning = progress?.state === 'RUNNING';
  const successCount = progress ? progress.done - progress.failed : 0;

  return (
    <div className="p-3 md:p-4 h-full flex flex-col gap-3 md:gap-4 overflow-auto pb-20 md:pb-4">

      {/* ── 顶栏 ── */}
      <div className="flex flex-col gap-2 md:flex-row md:items-center md:justify-between">
        <div className="flex items-center gap-2 md:gap-4">
          <div>
            <h1 className="text-lg md:text-xl font-bold text-white">数据管道</h1>
            <p className="text-gray-500 text-xs mt-0.5">C++ 多线程 · EastMoney API · SQLite WAL</p>
          </div>
          <button
            onClick={handleUpdateToday}
            disabled={loading || isRunning}
            className="px-3 py-1.5 md:px-4 md:py-2 text-sm bg-green-600 hover:bg-green-500 text-white rounded-lg font-medium disabled:opacity-50 disabled:cursor-not-allowed transition-colors flex items-center gap-1.5"
          >
            <svg className="w-4 h-4" fill="none" viewBox="0 0 24 24" stroke="currentColor">
              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M4 4v5h.582m15.356 2A8.001 8.001 0 004.582 9m0 0H9m11 11v-5h-.581m0 0a8.003 8.003 0 01-15.357-2m15.357 2H15" />
            </svg>
            更新今日数据
          </button>
        </div>
        {globalStats && (
          <div className="flex items-center gap-3 md:gap-5 text-xs md:text-sm">
            <Stat label="累计请求" value={globalStats.total_requests.toLocaleString()} />
            <Stat label="累计写入" value={globalStats.total_rows_written.toLocaleString()} />
            <Stat label="运行时长" value={formatDuration(globalStats.uptime_seconds)} />
          </div>
        )}
      </div>

      {error && (
        <p className="text-red-400 text-sm bg-red-400/10 px-3 py-2 rounded">{error}</p>
      )}

      {/* ── 主体两列 ── */}
      <div className="flex-1 grid grid-cols-1 md:grid-cols-12 gap-3 md:gap-4 min-h-0">

        {/* 左列：配置面板 */}
        <div className="md:col-span-4 bg-dark-card rounded-xl border border-border p-3 md:p-4 flex flex-col">
          <h2 className="text-sm font-medium text-gray-400 mb-4">抓取配置</h2>

          <div className="space-y-3 flex-1">
            <Field label="股票代码" hint="逗号分隔，留空 = 全量 A 股">
              <input value={symbols} onChange={e => setSymbols(e.target.value)}
                placeholder="000001.SZ, 600000.SH"
                className="w-full p-2 bg-dark rounded border border-border text-white text-sm font-mono focus:border-primary outline-none" />
            </Field>

            <div className="grid grid-cols-2 gap-3">
              <Field label="开始日期">
                <input value={beginDate} onChange={e => setBeginDate(e.target.value)}
                  placeholder="YYYYMMDD"
                  className="w-full p-2 bg-dark rounded border border-border text-white text-sm font-mono focus:border-primary outline-none" />
              </Field>
              <Field label="结束日期">
                <input value={endDate} onChange={e => setEndDate(e.target.value)}
                  placeholder="YYYYMMDD"
                  className="w-full p-2 bg-dark rounded border border-border text-white text-sm font-mono focus:border-primary outline-none" />
              </Field>
            </div>

            <div className="grid grid-cols-2 gap-3">
              <Field label="并发线程">
                <input type="number" value={threadCount} onChange={e => setThreadCount(Number(e.target.value))}
                  min={1} max={16}
                  className="w-full p-2 bg-dark rounded border border-border text-white text-sm font-mono focus:border-primary outline-none" />
              </Field>
              <Field label="批量大小">
                <input type="number" value={batchSize} onChange={e => setBatchSize(Number(e.target.value))}
                  min={100} max={5000} step={100}
                  className="w-full p-2 bg-dark rounded border border-border text-white text-sm font-mono focus:border-primary outline-none" />
              </Field>
            </div>

          </div>

          {/* 按钮区 */}
          <div className="mt-4 flex gap-2">
            <button onClick={handleSubmit} disabled={loading || isRunning}
              className="flex-1 py-2.5 bg-primary hover:bg-primary/80 text-dark rounded-lg font-medium text-sm disabled:opacity-50 disabled:cursor-not-allowed transition-colors">
              {loading ? '提交中...' : isRunning ? '运行中...' : '开始抓取'}
            </button>
            {isRunning && (
              <button onClick={handleStop}
                className="px-4 py-2.5 bg-red-500/20 text-red-400 hover:bg-red-500/30 rounded-lg font-medium text-sm transition-colors border border-red-500/30">
                停止
              </button>
            )}
          </div>
        </div>

        {/* 右列：任务进度 */}
        <div className="md:col-span-8 flex flex-col gap-3 md:gap-4">

          {/* 进度卡片 */}
          <div className="bg-dark-card rounded-xl border border-border p-3 md:p-4 flex-1">
            {!progress ? (
              <div className="h-full flex flex-col items-center justify-center text-gray-600">
                <svg className="w-12 h-12 mb-3 opacity-30" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                  <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={1.5}
                    d="M20 7l-8-4-8 4m16 0l-8 4m8-4v10l-8 4m0-10L4 7m8 4v10M4 7v10l8 4" />
                </svg>
                <p className="text-sm">配置参数后点击「开始抓取」</p>
              </div>
            ) : (
              <>
                {/* 头部：状态 + 任务 ID */}
                <div className="flex items-center justify-between mb-4">
                  <div className="flex items-center gap-3">
                    <h2 className="text-sm font-medium text-gray-400">任务进度</h2>
                    <span className="text-xs text-gray-600 font-mono">{progress.task_id}</span>
                  </div>
                  <StatusBadge state={progress.state} />
                </div>

                {/* 进度条 */}
                <div className="mb-5">
                  <div className="flex items-baseline justify-between mb-2">
                    <span className="text-xl md:text-2xl font-bold text-white font-mono">{progressPct}%</span>
                    <span className="text-sm text-gray-500">
                      {progress.done} / {progress.total} 只股票
                    </span>
                  </div>
                  <div className="w-full bg-dark rounded-full h-2 overflow-hidden">
                    <div
                      className={`h-full rounded-full transition-all duration-700 ease-out ${
                        progress.state === 'COMPLETED' ? 'bg-green-500' :
                        progress.state === 'FAILED' ? 'bg-red-500' :
                        progress.state === 'STOPPED' ? 'bg-yellow-500' :
                        'bg-gradient-to-r from-primary to-accent-cyan'
                      }`}
                      style={{ width: `${progressPct}%` }}
                    />
                  </div>
                </div>

                {/* 指标网格 */}
                <div className={`grid grid-cols-2 md:grid-cols-3 ${progress.proxy_switches != null ? 'lg:grid-cols-7' : 'lg:grid-cols-6'} gap-2 md:gap-3`}>
                  <MetricCard
                    label="当前" icon=">"
                    value={progress.current_symbol || '-'}
                    mono
                  />
                  <MetricCard
                    label="成功" icon="+"
                    value={successCount.toString()}
                    valueColor="text-green-400"
                  />
                  <MetricCard
                    label="失败" icon="!"
                    value={progress.failed.toString()}
                    valueColor={progress.failed > 0 ? 'text-red-400' : 'text-gray-500'}
                  />
                  <MetricCard
                    label="写入行数" icon="#"
                    value={progress.rows_written.toLocaleString()}
                  />
                  <MetricCard
                    label="速度" icon="~"
                    value={`${progress.speed.toFixed(1)}/s`}
                    valueColor="text-primary-light"
                  />
                  <MetricCard
                    label="耗时" icon="T"
                    value={formatDuration(progress.elapsed_seconds)}
                  />
                  {progress.proxy_switches != null && (
                    <MetricCard
                      label="换IP" icon="@"
                      value={progress.proxy_switches.toString()}
                      valueColor="text-cyan-400"
                    />
                  )}
                </div>

                {/* 错误信息 */}
                {progress.error_message && (
                  <div className="mt-3 text-sm text-red-400 bg-red-500/10 border border-red-500/20 rounded px-3 py-2">
                    {progress.error_message}
                  </div>
                )}
              </>
            )}
          </div>

          {/* 底部统计条 */}
          {globalStats && (
            <div className="bg-dark-card rounded-xl border border-border px-3 md:px-5 py-3 flex flex-wrap items-center gap-3 md:gap-6 text-xs md:text-sm">
              <Stat label="平均速度" value={`${globalStats.avg_speed.toFixed(2)} 只/秒`} color="text-primary-light" />
              <Stat label="错误率" value={
                globalStats.total_requests > 0
                  ? `${((globalStats.total_errors / globalStats.total_requests) * 100).toFixed(1)}%`
                  : '0%'
              } color={globalStats.total_errors > 0 ? 'text-yellow-400' : 'text-green-400'} />
              <div className="flex-1" />
              <span className="text-gray-600 text-xs font-mono">C++ pipeline :8003</span>
            </div>
          )}
        </div>
      </div>
    </div>
  );
};

// ── 子组件 ──

const Field: React.FC<{ label: string; hint?: string; children: React.ReactNode }> = ({ label, hint, children }) => (
  <div>
    <label className="text-xs text-gray-500 mb-1 block">
      {label}
      {hint && <span className="text-gray-600 ml-1">({hint})</span>}
    </label>
    {children}
  </div>
);

const StatusBadge: React.FC<{ state: string }> = ({ state }) => {
  const styles: Record<string, string> = {
    RUNNING:   'bg-primary/15 text-primary-light border-primary/40',
    COMPLETED: 'bg-green-500/15 text-green-400 border-green-500/30',
    FAILED:    'bg-red-500/15 text-red-400 border-red-500/30',
    STOPPED:   'bg-yellow-500/15 text-yellow-400 border-yellow-500/30',
    PENDING:   'bg-gray-500/15 text-gray-400 border-gray-500/30',
  };
  return (
    <span className={`px-2.5 py-0.5 rounded-md text-xs font-medium border ${styles[state] || styles.PENDING}`}>
      {state === 'RUNNING' && <span className="inline-block w-1.5 h-1.5 bg-primary rounded-full mr-1.5 animate-pulse" />}
      {state}
    </span>
  );
};

const MetricCard: React.FC<{
  label: string; icon: string; value: string;
  valueColor?: string; mono?: boolean;
}> = ({ label, icon, value, valueColor = 'text-white', mono }) => (
  <div className="bg-dark/50 rounded-lg px-3 py-2.5">
    <div className="text-[10px] text-gray-600 uppercase tracking-wider mb-1">
      <span className="text-gray-700 mr-1">{icon}</span>{label}
    </div>
    <div className={`text-sm font-semibold truncate ${valueColor} ${mono ? 'font-mono' : ''}`}>
      {value}
    </div>
  </div>
);

const Stat: React.FC<{ label: string; value: string; color?: string }> = ({ label, value, color = 'text-white' }) => (
  <div>
    <span className="text-gray-500 mr-1.5">{label}</span>
    <span className={`font-mono ${color}`}>{value}</span>
  </div>
);

function formatDuration(seconds: number): string {
  if (seconds < 60) return `${seconds.toFixed(1)}s`;
  const m = Math.floor(seconds / 60);
  const s = Math.round(seconds % 60);
  if (m < 60) return `${m}m ${s}s`;
  const h = Math.floor(m / 60);
  return `${h}h ${m % 60}m`;
}

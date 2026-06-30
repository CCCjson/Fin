/**
 * Alpha Lab — AI 自动策略生成与迭代优化
 */
import React, { useState, useRef, useCallback, useEffect } from 'react';
import { useNavigate } from 'react-router-dom';
import {
  alphaLabService,
  type AlphaLabStreamEvent,
  type StartParams,
  type SessionSummary,
  type StrategySummary,
  type ProviderInfo,
} from '../services/alphaLabService';

// ==================== 类型 ====================

interface IterationRow {
  iteration: number;
  model: string;
  phase: string;
  trainSharpe: number | null;
  valSharpe: number | null;
  overfitScore: number | null;
  compositeScore: number | null;
  isBest: boolean;
  status: 'pending' | 'generating' | 'backtesting' | 'done' | 'failed';
  error?: string;
}

type RunStatus = 'idle' | 'running' | 'completed' | 'error';

// ==================== 组件 ====================

export const AlphaLab: React.FC = () => {
  const nav = useNavigate();

  // --- 表单状态 ---
  const [symbol, setSymbol] = useState('600519.SH');
  const [goal, setGoal] = useState('sharpe');
  const [dataStart, setDataStart] = useState('2010-01-01');
  const [dataEnd, setDataEnd] = useState('2025-12-31');
  const [maxIter, setMaxIter] = useState(10);

  // --- 运行状态 ---
  const [runStatus, setRunStatus] = useState<RunStatus>('idle');
  const runStatusRef = useRef<RunStatus>('idle');
  const updateRunStatus = useCallback((s: RunStatus) => {
    runStatusRef.current = s;
    setRunStatus(s);
  }, []);
  const [, setSessionId] = useState<string | null>(null);
  const [statusMessage, setStatusMessage] = useState('');
  const [iterations, setIterations] = useState<IterationRow[]>([]);
  const [, setBestIteration] = useState<number | null>(null);
  const [totalCost, setTotalCost] = useState(0);
  const [currentPhase, setCurrentPhase] = useState('explore');

  // --- 历史会话 ---
  const [sessions, setSessions] = useState<SessionSummary[]>([]);
  const [selectedSession, setSelectedSession] = useState<(SessionSummary & { strategies: StrategySummary[] }) | null>(null);

  // --- AI Provider ---
  const [providers, setProviders] = useState<ProviderInfo[]>([]);
  const [selectedProvider, setSelectedProvider] = useState<string>('');

  // --- 策略代码查看 ---
  const [viewingCode, setViewingCode] = useState<string | null>(null);
  const [viewingIteration, setViewingIteration] = useState<number | null>(null);

  const abortRef = useRef<AbortController | null>(null);
  const logRef = useRef<HTMLDivElement>(null);
  const scrollContainerRef = useRef<HTMLDivElement>(null);

  // 加载 providers 和历史会话
  useEffect(() => {
    alphaLabService.getProviders().then((res) => {
      setProviders(res.providers || []);
      // 默认选第一个可用的
      const first = (res.providers || []).find((p) => p.available);
      if (first) setSelectedProvider(first.id);
    }).catch(() => {});
    alphaLabService.getSessions().then((res) => {
      setSessions(res.sessions || []);
    }).catch(() => {});
  }, []);

  // 自动滚动日志
  useEffect(() => {
    if (logRef.current) {
      logRef.current.scrollTop = logRef.current.scrollHeight;
    }
  }, [iterations, statusMessage]);

  // --- 启动 ---
  const handleStart = useCallback(async () => {
    if (runStatus === 'running') return;

    updateRunStatus('running');
    setIterations([]);
    setBestIteration(null);
    setTotalCost(0);
    setSessionId(null);
    setStatusMessage('正在初始化...');
    setSelectedSession(null);
    setViewingCode(null);
    setCurrentPhase('explore');

    const controller = new AbortController();
    abortRef.current = controller;

    const params: StartParams = {
      target_symbols: [symbol.trim()],
      optimization_goal: goal,
      data_start: dataStart,
      data_end: dataEnd,
      max_iterations: maxIter,
      provider: selectedProvider || undefined,
    };

    try {
      await alphaLabService.start(params, (event: AlphaLabStreamEvent) => {
        switch (event.event) {
          case 'session_created':
            setSessionId(event.session_id || null);
            setStatusMessage('会话已创建');
            break;

          case 'preparing_data':
            setStatusMessage(event.message || '准备数据中...');
            break;

          case 'data_ready':
            setStatusMessage(event.message || '数据就绪');
            break;

          case 'iteration_start':
            setStatusMessage(`第 ${event.iteration} 轮 (${event.phase === 'explore' ? '探索期' : '精炼期'}) — ${event.model}`);
            setCurrentPhase(event.phase || 'explore');
            setIterations((prev) => [
              ...prev,
              {
                iteration: event.iteration!,
                model: event.model || '',
                phase: event.phase || 'explore',
                trainSharpe: null,
                valSharpe: null,
                overfitScore: null,
                compositeScore: null,
                isBest: false,
                status: 'pending',
              },
            ]);
            break;

          case 'generating_code':
            setStatusMessage(`第 ${event.iteration} 轮 — AI 正在生成策略...`);
            setIterations((prev) =>
              prev.map((r) =>
                r.iteration === event.iteration ? { ...r, status: 'generating' as const } : r
              )
            );
            break;

          case 'code_generated':
            setStatusMessage(`第 ${event.iteration} 轮 — 代码生成完成 (${event.tokens} tokens)`);
            break;

          case 'ast_check_passed':
            setStatusMessage(`第 ${event.iteration} 轮 — 安全检查通过`);
            break;

          case 'ast_rejected':
            setIterations((prev) =>
              prev.map((r) =>
                r.iteration === event.iteration
                  ? { ...r, status: 'failed' as const, error: `安全检查失败: ${event.violations?.join(', ')}` }
                  : r
              )
            );
            break;

          case 'backtest_running':
            setStatusMessage(`第 ${event.iteration} 轮 — 正在回测 ${event.symbol || ''}...`);
            setIterations((prev) =>
              prev.map((r) =>
                r.iteration === event.iteration ? { ...r, status: 'backtesting' as const } : r
              )
            );
            break;

          case 'backtest_failed':
            setIterations((prev) =>
              prev.map((r) =>
                r.iteration === event.iteration
                  ? { ...r, status: 'failed' as const, error: event.error }
                  : r
              )
            );
            break;

          case 'evaluation_done':
            break;

          case 'iteration_complete':
            setIterations((prev) =>
              prev.map((r) =>
                r.iteration === event.iteration
                  ? {
                      ...r,
                      trainSharpe: event.train_sharpe ?? null,
                      valSharpe: event.val_sharpe ?? null,
                      overfitScore: event.overfit_score ?? null,
                      compositeScore: event.composite_score ?? null,
                      isBest: event.is_best || false,
                      status: 'done' as const,
                    }
                  : r
              )
            );
            if (event.is_best) {
              setBestIteration(event.iteration || null);
            }
            break;

          case 'iteration_error':
            setIterations((prev) =>
              prev.map((r) =>
                r.iteration === event.iteration
                  ? { ...r, status: 'failed' as const, error: event.error }
                  : r
              )
            );
            break;

          case 'phase_change':
            setCurrentPhase(event.to || 'refine');
            setStatusMessage(`切换到${event.to === 'refine' ? '精炼期' : '探索期'}`);
            break;

          case 'early_stop':
            setStatusMessage(`早停: ${event.reason}`);
            break;

          case 'session_complete':
            updateRunStatus('completed');
            setBestIteration(event.best_iteration || null);
            setTotalCost(event.total_cost_usd || 0);
            setStatusMessage(
              `探索完成！最佳第 ${event.best_iteration} 轮，Sharpe ${event.best_sharpe?.toFixed(3) ?? 'N/A'}，成本 $${event.total_cost_usd?.toFixed(4) ?? '0'}`
            );
            alphaLabService.getSessions().then((res) => setSessions(res.sessions || [])).catch(() => {});
            break;

          case 'error':
            updateRunStatus('error');
            setStatusMessage(`错误: ${event.message}`);
            break;
        }
      }, controller.signal);

      if (runStatusRef.current !== 'error') {
        updateRunStatus('completed');
      }
    } catch (err: any) {
      if (err?.name !== 'AbortError') {
        updateRunStatus('error');
        setStatusMessage(`请求失败: ${err.message}`);
      }
    }
  }, [symbol, goal, dataStart, dataEnd, maxIter, runStatus, selectedProvider]);

  // --- 停止 ---
  const handleStop = useCallback(() => {
    abortRef.current?.abort();
    updateRunStatus('idle');
    setStatusMessage('已手动停止');
    // 将进行中的迭代标记为失败
    setIterations((prev) =>
      prev.map((r) =>
        r.status === 'pending' || r.status === 'generating' || r.status === 'backtesting'
          ? { ...r, status: 'failed' as const, error: '手动停止' }
          : r
      )
    );
  }, [updateRunStatus]);

  // --- 查看历史会话详情 ---
  const handleViewSession = useCallback(async (sid: string) => {
    try {
      const detail = await alphaLabService.getSessionDetail(sid);
      setSelectedSession(detail);
      setViewingCode(null);
    } catch {
      // ignore
    }
  }, []);

  // --- 删除会话 ---
  const handleDeleteSession = useCallback(async (e: React.MouseEvent, sid: string) => {
    e.stopPropagation();
    if (!confirm('确定删除这个会话吗？关联的策略和日志也会一起删除。')) return;
    try {
      await alphaLabService.deleteSession(sid);
      setSessions((prev) => prev.filter((s) => s.id !== sid));
      if (selectedSession?.id === sid) {
        setSelectedSession(null);
        setViewingCode(null);
      }
    } catch {
      // ignore
    }
  }, [selectedSession]);

  // --- 滚动控制 ---
  const scrollTo = useCallback((direction: 'top' | 'bottom') => {
    scrollContainerRef.current?.scrollTo({
      top: direction === 'top' ? 0 : scrollContainerRef.current.scrollHeight,
      behavior: 'smooth',
    });
  }, []);

  // ==================== 渲染 ====================

  const goalLabels: Record<string, string> = {
    sharpe: '夏普比率',
    return: '年化收益',
    win_rate: '胜率',
    drawdown: '最小回撤',
  };

  // Token 预估：探索期 ~3K/轮，精炼期 ~5K/轮
  const estimatedTokens = Math.round(maxIter * 4000);
  const estimatedCost = (5 * 0.00038 + (maxIter > 5 ? (maxIter - 5) * 0.00625 : 0)) * 4;

  return (
    <div className="h-screen bg-gradient-dark flex flex-col pb-20 md:pb-0 overflow-hidden">
      {/* ===== Header ===== */}
      <div className="flex-shrink-0 border-b border-border bg-dark-card/50 backdrop-blur-sm px-4 md:px-6 py-3 md:py-4">
        <div className="flex items-center justify-between">
          <div className="flex items-center gap-3">
            <span
              onClick={() => nav('/')}
              className="text-2xl cursor-pointer hover:scale-110 transition-transform"
              title="返回主页"
            >🧬</span>
            <div>
              <h1 className="text-lg md:text-xl font-bold text-white">Alpha Lab</h1>
              <p className="text-xs text-gray-500 hidden md:block">AI 自动策略生成与迭代优化</p>
            </div>
          </div>
          {runStatus === 'running' && (
            <div className="flex items-center gap-2 text-sm text-primary">
              <svg className="animate-spin h-4 w-4" viewBox="0 0 24 24">
                <circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4" fill="none" />
                <path className="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4zm2 5.291A7.962 7.962 0 014 12H0c0 3.042 1.135 5.824 3 7.938l3-2.647z" />
              </svg>
              <span className="hidden md:inline">{currentPhase === 'explore' ? '探索期' : '精炼期'}</span>
            </div>
          )}
        </div>
      </div>

      {/* ===== 主内容（可滚动） ===== */}
      <div className="flex-1 overflow-y-auto scroll-smooth" ref={scrollContainerRef}>
        <div className="max-w-6xl mx-auto px-3 md:px-6 py-4 md:py-6 space-y-4 md:space-y-6">

          {/* --- 配置面板 --- */}
          <div className="bg-gradient-card border border-border rounded-2xl rounded-tl-sm px-4 md:px-6 py-4 md:py-5">
            <h2 className="text-sm font-semibold text-gray-400 mb-3 uppercase tracking-wider">目标设定</h2>
            <div className="grid grid-cols-2 md:grid-cols-6 gap-3">
              <div>
                <label className="text-xs text-gray-500 mb-1 block">股票代码</label>
                <input
                  type="text"
                  value={symbol}
                  onChange={(e) => setSymbol(e.target.value)}
                  disabled={runStatus === 'running'}
                  className="w-full px-3 py-2 bg-dark border border-border rounded-lg text-white text-sm focus:border-primary focus:outline-none disabled:opacity-50"
                  placeholder="600519.SH"
                />
              </div>
              <div>
                <label className="text-xs text-gray-500 mb-1 block">AI 模型</label>
                <select
                  value={selectedProvider}
                  onChange={(e) => setSelectedProvider(e.target.value)}
                  disabled={runStatus === 'running'}
                  className="w-full px-3 py-2 bg-dark border border-border rounded-lg text-white text-sm focus:border-primary focus:outline-none disabled:opacity-50"
                >
                  {providers.map((p) => (
                    <option key={p.id} value={p.id} disabled={!p.available}>
                      {p.label}{!p.available ? ' (未配置)' : ''}
                    </option>
                  ))}
                </select>
              </div>
              <div>
                <label className="text-xs text-gray-500 mb-1 block">优化目标</label>
                <select
                  value={goal}
                  onChange={(e) => setGoal(e.target.value)}
                  disabled={runStatus === 'running'}
                  className="w-full px-3 py-2 bg-dark border border-border rounded-lg text-white text-sm focus:border-primary focus:outline-none disabled:opacity-50"
                >
                  <option value="sharpe">夏普比率</option>
                  <option value="return">年化收益</option>
                  <option value="win_rate">胜率</option>
                  <option value="drawdown">最小回撤</option>
                </select>
              </div>
              <div>
                <label className="text-xs text-gray-500 mb-1 block">开始日期</label>
                <input
                  type="date"
                  value={dataStart}
                  onChange={(e) => setDataStart(e.target.value)}
                  disabled={runStatus === 'running'}
                  className="w-full px-3 py-2 bg-dark border border-border rounded-lg text-white text-sm focus:border-primary focus:outline-none disabled:opacity-50"
                />
              </div>
              <div>
                <label className="text-xs text-gray-500 mb-1 block">结束日期</label>
                <input
                  type="date"
                  value={dataEnd}
                  onChange={(e) => setDataEnd(e.target.value)}
                  disabled={runStatus === 'running'}
                  className="w-full px-3 py-2 bg-dark border border-border rounded-lg text-white text-sm focus:border-primary focus:outline-none disabled:opacity-50"
                />
              </div>
              <div className="flex items-end">
                {runStatus === 'running' ? (
                  <button
                    onClick={handleStop}
                    className="w-full px-4 py-2 bg-red-500/15 text-red-400 border border-red-500/20 rounded-xl hover:bg-red-500/25 transition-all text-sm font-medium"
                  >
                    停止
                  </button>
                ) : (
                  <button
                    onClick={handleStart}
                    className="w-full px-4 py-2 bg-gradient-to-r from-violet-500 to-purple-600 text-white rounded-xl hover:from-violet-600 hover:to-purple-700 shadow-lg shadow-purple-500/25 transition-all text-sm font-medium"
                  >
                    开始探索
                  </button>
                )}
              </div>
            </div>
            <div className="mt-3 flex flex-wrap items-center gap-4 text-xs text-gray-500">
              {selectedProvider && providers.find(p => p.id === selectedProvider) && (
                <span className="text-gray-600" title="探索期 / 精炼期模型">
                  {(() => { const p = providers.find(p => p.id === selectedProvider)!; return p.explore_model === p.refine_model ? p.explore_model : `${p.explore_model} → ${p.refine_model}`; })()}
                </span>
              )}
              <span>迭代轮数: {maxIter}</span>
              <input
                type="range"
                min={3}
                max={20}
                value={maxIter}
                onChange={(e) => setMaxIter(Number(e.target.value))}
                disabled={runStatus === 'running'}
                className="flex-1 max-w-[200px] accent-violet-500"
              />
              <span className="text-gray-600">
                预估: ~{(estimatedTokens / 1000).toFixed(0)}K tokens / ${estimatedCost.toFixed(3)}
              </span>
              {totalCost > 0 && <span className="text-accent-orange">实际成本: ${totalCost.toFixed(4)}</span>}
            </div>
          </div>

          {/* --- 状态消息 --- */}
          {statusMessage && (
            <div className={`px-4 py-2.5 rounded-xl text-sm ${
              runStatus === 'error'
                ? 'bg-red-500/10 text-red-400 border border-red-500/20'
                : runStatus === 'completed'
                ? 'bg-emerald-500/10 text-emerald-400 border border-emerald-500/20'
                : 'bg-primary/10 text-primary border border-primary/20'
            }`}>
              {statusMessage}
            </div>
          )}

          {/* --- 迭代进度表 --- */}
          {iterations.length > 0 && (
            <div className="bg-gradient-card border border-border rounded-2xl rounded-tl-sm px-4 md:px-6 py-4 md:py-5">
              <h2 className="text-sm font-semibold text-gray-400 mb-3 uppercase tracking-wider">
                迭代进度 ({iterations.filter((r) => r.status === 'done').length}/{iterations.length})
              </h2>
              <div className="overflow-x-auto max-h-[400px] overflow-y-auto" ref={logRef}>
                <table className="w-full text-sm">
                  <thead className="sticky top-0 bg-dark-card z-10">
                    <tr className="text-gray-500 border-b border-border">
                      <th className="py-2 px-2 text-left font-medium">#</th>
                      <th className="py-2 px-2 text-left font-medium">阶段</th>
                      <th className="py-2 px-2 text-right font-medium">训练 Sharpe</th>
                      <th className="py-2 px-2 text-right font-medium">验证 Sharpe</th>
                      <th className="py-2 px-2 text-right font-medium">过拟合</th>
                      <th className="py-2 px-2 text-right font-medium">综合分</th>
                      <th className="py-2 px-2 text-center font-medium">状态</th>
                    </tr>
                  </thead>
                  <tbody>
                    {iterations.map((row) => (
                      <tr
                        key={row.iteration}
                        className={`border-b border-border/50 transition-colors ${
                          row.isBest ? 'bg-violet-500/5' : 'hover:bg-dark-light/30'
                        }`}
                      >
                        <td className="py-2.5 px-2 text-white font-mono">
                          {row.iteration}
                          {row.isBest && <span className="ml-1 text-yellow-400">★</span>}
                        </td>
                        <td className="py-2.5 px-2">
                          <span className={`px-2 py-0.5 rounded-full text-xs ${
                            row.phase === 'explore'
                              ? 'bg-primary/15 text-primary-light'
                              : 'bg-purple-500/15 text-purple-400'
                          }`}>
                            {row.phase === 'explore' ? '探索' : '精炼'}
                          </span>
                        </td>
                        <td className="py-2.5 px-2 text-right font-mono text-gray-300">
                          {row.trainSharpe !== null ? row.trainSharpe.toFixed(3) : '—'}
                        </td>
                        <td className={`py-2.5 px-2 text-right font-mono ${
                          row.valSharpe !== null && row.valSharpe > 0 ? 'text-emerald-400' : 'text-gray-300'
                        }`}>
                          {row.valSharpe !== null ? row.valSharpe.toFixed(3) : '—'}
                        </td>
                        <td className="py-2.5 px-2 text-right">
                          {row.overfitScore !== null ? (
                            <span className={`font-mono ${
                              row.overfitScore > 0.5
                                ? 'text-red-400'
                                : row.overfitScore > 0.3
                                ? 'text-yellow-400'
                                : 'text-emerald-400'
                            }`}>
                              {row.overfitScore.toFixed(2)}
                            </span>
                          ) : '—'}
                        </td>
                        <td className="py-2.5 px-2 text-right font-mono text-white">
                          {row.compositeScore !== null ? row.compositeScore.toFixed(4) : '—'}
                        </td>
                        <td className="py-2.5 px-2 text-center">
                          {row.status === 'done' && <span className="text-emerald-400">&#10003;</span>}
                          {row.status === 'failed' && (
                            <span className="text-red-400" title={row.error}>&#10007;</span>
                          )}
                          {(row.status === 'generating' || row.status === 'backtesting' || row.status === 'pending') && (
                            <svg className="animate-spin h-4 w-4 text-primary inline-block" viewBox="0 0 24 24">
                              <circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4" fill="none" />
                              <path className="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4zm2 5.291A7.962 7.962 0 014 12H0c0 3.042 1.135 5.824 3 7.938l3-2.647z" />
                            </svg>
                          )}
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </div>
          )}

          {/* --- 历史会话 --- */}
          <div className="bg-gradient-card border border-border rounded-2xl rounded-tl-sm px-4 md:px-6 py-4 md:py-5">
            <h2 className="text-sm font-semibold text-gray-400 mb-3 uppercase tracking-wider">
              历史会话 {sessions.length > 0 && <span className="text-gray-600 normal-case">({sessions.length})</span>}
            </h2>
            {sessions.length === 0 ? (
              <p className="text-gray-500 text-sm">暂无历史记录，开始你的第一次探索吧！</p>
            ) : (
              <div className="space-y-2 max-h-[300px] overflow-y-auto pr-1">
                {sessions.map((s) => (
                  <div
                    key={s.id}
                    onClick={() => handleViewSession(s.id)}
                    className={`group flex items-center justify-between px-4 py-3 rounded-xl cursor-pointer transition-all ${
                      selectedSession?.id === s.id
                        ? 'bg-violet-500/10 border border-violet-500/30'
                        : 'bg-dark/50 border border-transparent hover:border-border hover:bg-dark-light/30'
                    }`}
                  >
                    <div className="flex items-center gap-3 min-w-0">
                      <span className={`w-2 h-2 rounded-full flex-shrink-0 ${
                        s.status === 'completed' ? 'bg-emerald-400' :
                        s.status === 'running' ? 'bg-primary animate-pulse' :
                        'bg-gray-500'
                      }`} />
                      <div className="min-w-0">
                        <span className="text-sm text-white font-medium truncate block">
                          {(s.target_symbols || []).join(', ')} — {goalLabels[s.optimization_goal] || s.optimization_goal}
                        </span>
                        <span className="text-xs text-gray-500">
                          {s.total_iterations} 轮 · {s.created_at?.split(' ')[0]}
                        </span>
                      </div>
                    </div>
                    <div className="flex items-center gap-3 flex-shrink-0 ml-3">
                      <div className="text-right">
                        {s.best_sharpe != null && (
                          <span className="text-sm font-mono text-emerald-400">
                            Sharpe {s.best_sharpe.toFixed(3)}
                          </span>
                        )}
                        <span className="text-xs text-gray-500 block">${(s.cost_usd || 0).toFixed(4)}</span>
                      </div>
                      <button
                        onClick={(e) => handleDeleteSession(e, s.id)}
                        className="opacity-0 group-hover:opacity-100 p-1.5 rounded-lg text-gray-500 hover:text-red-400 hover:bg-red-500/10 transition-all"
                        title="删除会话"
                      >
                        <svg className="w-3.5 h-3.5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                          <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M19 7l-.867 12.142A2 2 0 0116.138 21H7.862a2 2 0 01-1.995-1.858L5 7m5 4v6m4-6v6m1-10V4a1 1 0 00-1-1h-4a1 1 0 00-1 1v3M4 7h16" />
                        </svg>
                      </button>
                    </div>
                  </div>
                ))}
              </div>
            )}
          </div>

          {/* --- 选中会话的策略列表 --- */}
          {selectedSession && selectedSession.strategies && (
            <div className="bg-gradient-card border border-border rounded-2xl rounded-tl-sm px-4 md:px-6 py-4 md:py-5">
              <h2 className="text-sm font-semibold text-gray-400 mb-3 uppercase tracking-wider">
                会话策略 — {(selectedSession.target_symbols || []).join(', ')}
              </h2>
              <div className="space-y-2 max-h-[350px] overflow-y-auto pr-1">
                {selectedSession.strategies.map((strat) => {
                  const valSharpe = (strat.val_metrics as any)?.sharpe_ratio ?? null;
                  return (
                    <div
                      key={strat.id}
                      className="flex items-center justify-between px-4 py-3 rounded-xl bg-dark/50 border border-transparent hover:border-border transition-all"
                    >
                      <div className="flex items-center gap-3">
                        <span className="text-sm text-gray-400 font-mono w-8">#{strat.iteration}</span>
                        <span className={`px-2 py-0.5 rounded-full text-xs ${
                          strat.status === 'completed' ? 'bg-emerald-500/15 text-emerald-400' :
                          strat.status === 'failed' ? 'bg-red-500/15 text-red-400' :
                          'bg-yellow-500/15 text-yellow-400'
                        }`}>
                          {strat.status}
                        </span>
                        {valSharpe !== null && (
                          <span className="text-sm font-mono text-gray-300">
                            Sharpe {Number(valSharpe).toFixed(3)}
                          </span>
                        )}
                        {strat.overfit_score !== null && (
                          <span className={`text-xs ${
                            strat.overfit_score > 0.5 ? 'text-red-400' :
                            strat.overfit_score > 0.3 ? 'text-yellow-400' :
                            'text-gray-500'
                          }`}>
                            OF: {strat.overfit_score.toFixed(2)}
                          </span>
                        )}
                      </div>
                      <button
                        onClick={() => {
                          setViewingCode(strat.code);
                          setViewingIteration(strat.iteration);
                        }}
                        className="text-xs text-primary hover:text-primary-light transition-colors"
                      >
                        查看代码
                      </button>
                    </div>
                  );
                })}
              </div>
            </div>
          )}

          {/* --- 代码查看器 --- */}
          {viewingCode && (
            <div className="bg-gradient-card border border-border rounded-2xl rounded-tl-sm px-4 md:px-6 py-4 md:py-5">
              <div className="flex items-center justify-between mb-3">
                <h2 className="text-sm font-semibold text-gray-400 uppercase tracking-wider">
                  策略代码 — 第 {viewingIteration} 轮
                </h2>
                <div className="flex items-center gap-2">
                  <button
                    onClick={() => { navigator.clipboard.writeText(viewingCode); }}
                    className="text-xs px-3 py-1 bg-dark border border-border rounded-lg text-gray-400 hover:text-white transition-colors"
                  >
                    复制
                  </button>
                  <button
                    onClick={() => { setViewingCode(null); setViewingIteration(null); }}
                    className="text-xs px-3 py-1 bg-dark border border-border rounded-lg text-gray-400 hover:text-white transition-colors"
                  >
                    关闭
                  </button>
                </div>
              </div>
              <pre className="bg-dark rounded-xl p-4 overflow-x-auto text-sm text-gray-300 font-mono border border-border leading-relaxed max-h-[500px] overflow-y-auto">
                {viewingCode}
              </pre>
            </div>
          )}

        </div>
      </div>

      {/* ===== 页面滚动控制按钮 ===== */}
      <div className="fixed right-4 bottom-24 md:bottom-6 flex flex-col gap-2 z-20">
        <button
          onClick={() => scrollTo('top')}
          className="w-9 h-9 rounded-full bg-dark-card/80 backdrop-blur border border-border text-gray-400 hover:text-white hover:border-primary/50 transition-all flex items-center justify-center shadow-lg"
          title="回到顶部"
        >
          <svg className="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
            <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M5 15l7-7 7 7" />
          </svg>
        </button>
        <button
          onClick={() => scrollTo('bottom')}
          className="w-9 h-9 rounded-full bg-dark-card/80 backdrop-blur border border-border text-gray-400 hover:text-white hover:border-primary/50 transition-all flex items-center justify-center shadow-lg"
          title="滚到底部"
        >
          <svg className="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
            <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M19 9l-7 7-7-7" />
          </svg>
        </button>
      </div>
    </div>
  );
};

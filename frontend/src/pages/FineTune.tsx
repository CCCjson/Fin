/**
 * Fine-Tune Lab — 训练可视化与流式进度
 */
import React, { useState, useRef, useCallback, useEffect, useMemo } from 'react';
import { useNavigate } from 'react-router-dom';
import {
  LineChart, Line, XAxis, YAxis, CartesianGrid, Tooltip, Legend, ResponsiveContainer,
} from 'recharts';
import {
  fineTuneService,
  type FineTuneStreamEvent,
  type DataStats,
} from '../services/fineTuneService';

// ==================== 类型 ====================

interface TrainPoint {
  iter: number;
  train_loss?: number;
  val_loss?: number;
}

interface EvalItem {
  index: number;
  prompt: string;
  quality: {
    ast_valid: boolean;
    has_class: boolean;
    has_method: boolean;
    has_imports: boolean;
    safety_ok: boolean;
    score: number;
  };
}

type PipelineStage = 'train';
type StageStatus = 'pending' | 'running' | 'done' | 'error';
type RunStatus = 'idle' | 'running' | 'completed' | 'error';

const STAGE_LABELS: Record<PipelineStage, string> = {
  train: '训练',
};

const STAGE_ICONS: Record<PipelineStage, string> = {
  train: '🧠',
};

// ==================== 组件 ====================

export const FineTune: React.FC = () => {
  const nav = useNavigate();

  // --- 配置状态 ---
  const [iters, setIters] = useState(5000);
  const [lr, setLr] = useState(2e-4);
  const [batchSize, setBatchSize] = useState(1);

  // --- 运行状态 ---
  const [runStatus, setRunStatus] = useState<RunStatus>('idle');
  const runStatusRef = useRef<RunStatus>('idle');
  const updateRunStatus = useCallback((s: RunStatus) => {
    runStatusRef.current = s;
    setRunStatus(s);
  }, []);

  // --- 数据统计 ---
  const [dataStats, setDataStats] = useState<DataStats | null>(null);

  // --- Pipeline 步骤状态 ---
  const [stageStatuses, setStageStatuses] = useState<Record<PipelineStage, StageStatus>>({
    train: 'pending',
  });
  const [stageData, setStageData] = useState<Record<string, any>>({});

  // --- 训练指标 ---
  const [totalIters, setTotalIters] = useState(0);
  const [currentIter, setCurrentIter] = useState(0);
  const [latestTrainLoss, setLatestTrainLoss] = useState<number | null>(null);
  const [latestValLoss, setLatestValLoss] = useState<number | null>(null);
  const [latestSpeed, setLatestSpeed] = useState<number | null>(null);
  const [trainPoints, setTrainPoints] = useState<TrainPoint[]>([]);
  const [startTime, setStartTime] = useState<number | null>(null);

  // --- 评估结果 ---
  const [evalReport, setEvalReport] = useState<Record<string, any> | null>(null);
  const [evalItems, setEvalItems] = useState<EvalItem[]>([]);
  const [evalTotal, setEvalTotal] = useState(0);
  const [showEvalDetails, setShowEvalDetails] = useState(false);

  // --- 日志 ---
  const [logs, setLogs] = useState<string[]>([]);
  const logRef = useRef<HTMLDivElement>(null);
  const abortRef = useRef<AbortController | null>(null);

  // 加载数据统计
  useEffect(() => {
    setDataStats(null);
    fineTuneService.getDataStats().then(setDataStats).catch(() => {});
  }, []);

  // 自动滚动日志
  useEffect(() => {
    if (logRef.current) {
      logRef.current.scrollTop = logRef.current.scrollHeight;
    }
  }, [logs]);

  // 已用时间（每秒刷新）
  const [elapsed, setElapsed] = useState(0);

  useEffect(() => {
    if (!startTime || runStatus !== 'running') return;
    const timer = setInterval(() => {
      setElapsed(Math.floor((Date.now() - startTime) / 1000));
    }, 1000);
    return () => clearInterval(timer);
  }, [startTime, runStatus]);

  const formatTime = (s: number) => {
    const m = Math.floor(s / 60);
    const sec = s % 60;
    return m > 0 ? `${m}m ${sec}s` : `${sec}s`;
  };

  // 进度百分比
  const progress = totalIters > 0 ? Math.min((currentIter / totalIters) * 100, 100) : 0;

  // 预估剩余时间
  const eta = useMemo(() => {
    if (!latestSpeed || latestSpeed === 0 || currentIter >= totalIters) return null;
    const remaining = totalIters - currentIter;
    return Math.ceil(remaining / latestSpeed);
  }, [latestSpeed, currentIter, totalIters]);

  // --- 事件处理回调（start 和 reconnect 共用）---
  const handleEvent = useCallback((event: FineTuneStreamEvent) => {
    switch (event.event) {
      case 'pipeline_start':
        setLogs((p) => [...p, '🚀 Pipeline 启动']);
        break;

      case 'stage_start':
        if (event.stage) {
          setStageStatuses((p) => ({ ...p, [event.stage!]: 'running' }));
          setLogs((p) => [...p, `▶ 开始阶段: ${STAGE_LABELS[event.stage as PipelineStage] || event.stage}`]);
        }
        break;

      case 'stage_complete':
        if (event.stage) {
          setStageStatuses((p) => ({ ...p, [event.stage!]: 'done' }));
          if (event.data) {
            setStageData((p) => ({ ...p, [event.stage!]: event.data }));
          }
          setLogs((p) => [...p, `✓ 阶段完成: ${STAGE_LABELS[event.stage as PipelineStage] || event.stage}`]);
        }
        break;

      case 'train_start':
        setTotalIters(event.total_iters || 0);
        setLogs((p) => [...p, `🧠 训练开始 — 共 ${event.total_iters} 步`]);
        break;

      case 'train_step':
        setCurrentIter(event.iter || 0);
        if (event.train_loss != null) {
          setLatestTrainLoss(event.train_loss);
          setTrainPoints((p) => {
            const existing = p.find((pt) => pt.iter === event.iter);
            if (existing) {
              return p.map((pt) => pt.iter === event.iter ? { ...pt, train_loss: event.train_loss } : pt);
            }
            return [...p, { iter: event.iter!, train_loss: event.train_loss }];
          });
        }
        if (event.it_sec != null) setLatestSpeed(event.it_sec);
        break;

      case 'val_step':
        if (event.val_loss != null) {
          setLatestValLoss(event.val_loss);
          setTrainPoints((p) => {
            const existing = p.find((pt) => pt.iter === event.iter);
            if (existing) {
              return p.map((pt) => pt.iter === event.iter ? { ...pt, val_loss: event.val_loss } : pt);
            }
            return [...p, { iter: event.iter!, val_loss: event.val_loss }];
          });
        }
        setLogs((p) => [...p, `📈 Iter ${event.iter}: Val loss ${event.val_loss?.toFixed(4)}`]);
        break;

      case 'log':
        if (event.line) {
          setLogs((p) => [...p, event.line!]);
        }
        break;

      case 'train_complete':
        if (event.success) {
          setLogs((p) => [...p, '✓ 训练完成']);
        } else {
          setLogs((p) => [...p, `✗ 训练失败: ${event.error || ''}`]);
        }
        break;

      case 'eval_start':
        setEvalTotal(event.total_tests || 0);
        setLogs((p) => [...p, `📊 评估开始 — ${event.total_tests} 个测试`]);
        break;

      case 'eval_progress':
        if (event.quality) {
          setEvalItems((p) => [...p, {
            index: event.index!,
            prompt: event.prompt || '',
            quality: event.quality!,
          }]);
        }
        break;

      case 'eval_complete':
        setEvalReport({
          avg_score: event.avg_score,
          ast_pass_rate: event.ast_pass_rate,
          class_rate: event.class_rate,
          method_rate: event.method_rate,
          imports_rate: event.imports_rate,
          safety_rate: event.safety_rate,
          total_tests: event.total_tests,
        });
        setLogs((p) => [...p, `✓ 评估完成 — 综合评分: ${((event.avg_score || 0) * 100).toFixed(1)}%`]);
        break;

      case 'pipeline_complete':
        updateRunStatus('completed');
        setLogs((p) => [...p, '🎉 Pipeline 全部完成！']);
        break;

      case 'pipeline_error':
        updateRunStatus('error');
        if (event.stage) {
          setStageStatuses((p) => ({ ...p, [event.stage!]: 'error' }));
        }
        setLogs((p) => [...p, `✗ 错误 [${event.stage}]: ${event.error || ''}`]);
        break;
    }
  }, [updateRunStatus]);

  // --- 页面加载时自动重连（恢复训练状态）---
  useEffect(() => {
    fineTuneService.getStatus().then((status) => {
      if (status.running || (status as any).has_history) {
        updateRunStatus(status.running ? 'running' : 'completed');
        setStartTime(Date.now());
        fineTuneService.reconnect(handleEvent).catch(() => {});
      }
    }).catch(() => {});
  }, []); // eslint-disable-line react-hooks/exhaustive-deps

  // --- 启动 ---
  const handleStart = useCallback(async () => {
    if (runStatus === 'running') return;

    updateRunStatus('running');
    setStageStatuses({ train: 'pending' });
    setStageData({});
    setTrainPoints([]);
    setCurrentIter(0);
    setTotalIters(0);
    setLatestTrainLoss(null);
    setLatestValLoss(null);
    setLatestSpeed(null);
    setEvalReport(null);
    setEvalItems([]);
    setEvalTotal(0);
    setShowEvalDetails(false);
    setLogs([]);
    setStartTime(Date.now());

    const controller = new AbortController();
    abortRef.current = controller;

    try {
      await fineTuneService.start(
        { mode: 'remote', iters, learning_rate: lr, batch_size: batchSize },
        handleEvent,
        controller.signal,
      );

      if (runStatusRef.current !== 'error') {
        updateRunStatus('completed');
      }
    } catch (err: any) {
      if (err?.name !== 'AbortError') {
        updateRunStatus('error');
        setLogs((p) => [...p, `✗ 请求失败: ${err.message}`]);
      }
    }
  }, [iters, lr, batchSize, runStatus, updateRunStatus, handleEvent]);

  // --- 停止 ---
  const handleStop = useCallback(async () => {
    abortRef.current?.abort();
    try { await fineTuneService.stop(); } catch { /* ignore */ }
    updateRunStatus('idle');
    setLogs((p) => [...p, '⏹ 已手动停止']);
  }, [updateRunStatus]);

  // ==================== 渲染 ====================

  const stageList: PipelineStage[] = ['train'];

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
            >🔬</span>
            <div>
              <h1 className="text-lg md:text-xl font-bold text-white">Fine-Tuning Lab</h1>
              <p className="text-xs text-gray-500 hidden md:block">模型微调训练与评估</p>
            </div>
          </div>
          {runStatus === 'running' && (
            <div className="flex items-center gap-2 text-sm text-purple-400">
              <svg className="animate-spin h-4 w-4" viewBox="0 0 24 24">
                <circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4" fill="none" />
                <path className="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4zm2 5.291A7.962 7.962 0 014 12H0c0 3.042 1.135 5.824 3 7.938l3-2.647z" />
              </svg>
              <span className="hidden md:inline">Remote GPU 训练中</span>
            </div>
          )}
        </div>
      </div>

      {/* ===== 主内容 ===== */}
      <div className="flex-1 overflow-y-auto scroll-smooth">
        <div className="max-w-6xl mx-auto px-3 md:px-6 py-4 md:py-6 space-y-4 md:space-y-5">

          {/* --- 配置面板 --- */}
          <div className="bg-gradient-card border border-border rounded-2xl rounded-tl-sm px-4 md:px-6 py-4 md:py-5">
            <div className="flex items-center justify-between mb-3">
              <h2 className="text-sm font-semibold text-gray-400 uppercase tracking-wider">训练配置</h2>
              <span className="text-xs text-purple-400 font-medium">Remote GPU</span>
            </div>
            <div className="grid grid-cols-2 md:grid-cols-5 gap-3">
              <div>
                <label className="text-xs text-gray-500 mb-1 block">迭代次数</label>
                <input
                  type="number"
                  value={iters}
                  onChange={(e) => setIters(Number(e.target.value))}
                  disabled={runStatus === 'running'}
                  className="w-full px-3 py-2 bg-dark border border-border rounded-lg text-white text-sm focus:border-pink-500 focus:outline-none disabled:opacity-50"
                  min={10} max={5000}
                />
              </div>
              <div>
                <label className="text-xs text-gray-500 mb-1 block">学习率</label>
                <input
                  type="number"
                  value={lr}
                  onChange={(e) => setLr(Number(e.target.value))}
                  disabled={runStatus === 'running'}
                  step={0.000001}
                  className="w-full px-3 py-2 bg-dark border border-border rounded-lg text-white text-sm focus:border-pink-500 focus:outline-none disabled:opacity-50"
                />
              </div>
              <div>
                <label className="text-xs text-gray-500 mb-1 block">Batch Size</label>
                <select
                  value={batchSize}
                  onChange={(e) => setBatchSize(Number(e.target.value))}
                  disabled={runStatus === 'running'}
                  className="w-full px-3 py-2 bg-dark border border-border rounded-lg text-white text-sm focus:border-pink-500 focus:outline-none disabled:opacity-50"
                >
                  <option value={1}>1</option>
                  <option value={2}>2</option>
                  <option value={4}>4</option>
                </select>
              </div>
              <div>
                <label className="text-xs text-gray-500 mb-1 block">训练数据</label>
                <div className="px-3 py-2 bg-dark border border-border rounded-lg text-sm">
                  {dataStats ? (
                    <span className={dataStats.has_data ? 'text-emerald-400' : 'text-red-400'}>
                      {dataStats.has_data
                        ? `${dataStats.train} / ${dataStats.valid} / ${dataStats.test}`
                        : '无数据'}
                    </span>
                  ) : (
                    <span className="text-gray-600">加载中...</span>
                  )}
                </div>
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
                    disabled={!dataStats?.has_data}
                    className="w-full px-4 py-2 bg-gradient-to-r from-pink-500 to-rose-600 text-white rounded-xl hover:from-pink-600 hover:to-rose-700 shadow-lg shadow-pink-500/25 transition-all text-sm font-medium disabled:opacity-40 disabled:cursor-not-allowed"
                  >
                    开始训练
                  </button>
                )}
              </div>
            </div>
          </div>

          {/* --- Pipeline 步骤条 --- */}
          {runStatus !== 'idle' && (
            <div className="bg-gradient-card border border-border rounded-2xl rounded-tl-sm px-4 md:px-6 py-4">
              <div className="flex items-center justify-between gap-1 md:gap-2">
                {stageList.map((stage, i) => {
                  const status = stageStatuses[stage];
                  const bgColor = status === 'done' ? 'bg-emerald-500/20 border-emerald-500/40'
                    : status === 'running' ? 'bg-pink-500/20 border-pink-500/40'
                    : status === 'error' ? 'bg-red-500/20 border-red-500/40'
                    : 'bg-dark border-border';
                  const textColor = status === 'done' ? 'text-emerald-400'
                    : status === 'running' ? 'text-pink-400'
                    : status === 'error' ? 'text-red-400'
                    : 'text-gray-600';

                  return (
                    <React.Fragment key={stage}>
                      <div className={`flex-1 flex flex-col items-center gap-1 py-2 px-1 rounded-xl border ${bgColor} transition-all`}>
                        <span className="text-base md:text-lg">{STAGE_ICONS[stage]}</span>
                        <span className={`text-[10px] md:text-xs font-medium ${textColor}`}>
                          {STAGE_LABELS[stage]}
                        </span>
                        {status === 'running' && (
                          <div className="w-4 h-4">
                            <svg className="animate-spin h-4 w-4 text-pink-400" viewBox="0 0 24 24">
                              <circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4" fill="none" />
                              <path className="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4zm2 5.291A7.962 7.962 0 014 12H0c0 3.042 1.135 5.824 3 7.938l3-2.647z" />
                            </svg>
                          </div>
                        )}
                        {status === 'done' && <span className="text-emerald-400 text-sm">&#10003;</span>}
                        {status === 'error' && <span className="text-red-400 text-sm">&#10007;</span>}
                      </div>
                      {i < stageList.length - 1 && (
                        <div className={`w-4 md:w-8 h-0.5 rounded ${status === 'done' ? 'bg-emerald-500/40' : 'bg-border'}`} />
                      )}
                    </React.Fragment>
                  );
                })}
              </div>
            </div>
          )}

          {/* --- 验证/格式化结果 --- */}
          {stageData.validate && (
            <div className="bg-gradient-card border border-border rounded-2xl rounded-tl-sm px-4 md:px-6 py-3">
              <div className="flex flex-wrap gap-4 text-sm">
                <span className="text-gray-400">🔍 验证:</span>
                <span className="text-emerald-400">{stageData.validate.passed} 通过</span>
                <span className="text-red-400">{stageData.validate.failed} 失败</span>
                <span className="text-gray-500">通过率 {stageData.validate.pass_rate?.toFixed(1)}%</span>
              </div>
            </div>
          )}
          {stageData.format && (
            <div className="bg-gradient-card border border-border rounded-2xl rounded-tl-sm px-4 md:px-6 py-3">
              <div className="flex flex-wrap gap-4 text-sm">
                <span className="text-gray-400">📋 格式化:</span>
                <span className="text-blue-400">Train {stageData.format.train}</span>
                <span className="text-purple-400">Valid {stageData.format.valid}</span>
                <span className="text-orange-400">Test {stageData.format.test}</span>
              </div>
            </div>
          )}

          {/* --- 训练指标卡片 --- */}
          {(stageStatuses.train === 'running' || stageStatuses.train === 'done') && (
            <>
              <div className="grid grid-cols-2 md:grid-cols-4 gap-3">
                <MetricCard
                  label="Train Loss"
                  value={latestTrainLoss !== null ? latestTrainLoss.toFixed(4) : '—'}
                  color="text-blue-400"
                />
                <MetricCard
                  label="Val Loss"
                  value={latestValLoss !== null ? latestValLoss.toFixed(4) : '—'}
                  color="text-orange-400"
                />
                <MetricCard
                  label="速度"
                  value={latestSpeed !== null ? `${latestSpeed.toFixed(2)} it/s` : '—'}
                  color="text-emerald-400"
                />
                <MetricCard
                  label="已用时间"
                  value={formatTime(elapsed)}
                  color="text-pink-400"
                />
              </div>

              {/* --- 进度条 --- */}
              <div className="bg-gradient-card border border-border rounded-2xl rounded-tl-sm px-4 md:px-6 py-3">
                <div className="flex items-center justify-between text-xs text-gray-500 mb-1.5">
                  <span>{currentIter} / {totalIters}</span>
                  <span>
                    {progress.toFixed(1)}%
                    {eta !== null && ` · 剩余 ~${formatTime(eta)}`}
                  </span>
                </div>
                <div className="w-full h-2.5 bg-dark rounded-full overflow-hidden">
                  <div
                    className="h-full rounded-full transition-all duration-300 bg-gradient-to-r from-pink-500 to-rose-500"
                    style={{ width: `${progress}%` }}
                  />
                </div>
              </div>

              {/* --- Loss 曲线 --- */}
              {trainPoints.length > 0 && (
                <div className="bg-gradient-card border border-border rounded-2xl rounded-tl-sm px-4 md:px-6 py-4">
                  <h2 className="text-sm font-semibold text-gray-400 mb-3 uppercase tracking-wider">Loss 曲线</h2>
                  <ResponsiveContainer width="100%" height={280}>
                    <LineChart data={trainPoints}>
                      <CartesianGrid strokeDasharray="3 3" stroke="#1E293B" />
                      <XAxis dataKey="iter" stroke="#6B7280" tick={{ fontSize: 11 }} />
                      <YAxis stroke="#6B7280" tick={{ fontSize: 11 }} />
                      <Tooltip
                        contentStyle={{
                          backgroundColor: '#1F2937',
                          border: '1px solid #374151',
                          borderRadius: 8,
                          fontSize: 12,
                        }}
                      />
                      <Legend wrapperStyle={{ fontSize: 12 }} />
                      <Line
                        type="monotone"
                        dataKey="train_loss"
                        stroke="#3B82F6"
                        strokeWidth={2}
                        dot={false}
                        name="Train Loss"
                        connectNulls
                      />
                      <Line
                        type="monotone"
                        dataKey="val_loss"
                        stroke="#F97316"
                        strokeWidth={2}
                        strokeDasharray="5 5"
                        dot={{ fill: '#F97316', r: 3 }}
                        name="Val Loss"
                        connectNulls
                      />
                    </LineChart>
                  </ResponsiveContainer>
                </div>
              )}
            </>
          )}

          {/* --- 评估报告 --- */}
          {evalReport && (
            <div className="bg-gradient-card border border-border rounded-2xl rounded-tl-sm px-4 md:px-6 py-4 md:py-5">
              <h2 className="text-sm font-semibold text-gray-400 mb-4 uppercase tracking-wider">评估报告</h2>

              {/* 总评分 */}
              <div className="text-center mb-5">
                <span className={`text-4xl md:text-5xl font-bold ${
                  (evalReport.avg_score || 0) >= 0.8 ? 'text-emerald-400'
                  : (evalReport.avg_score || 0) >= 0.5 ? 'text-yellow-400'
                  : 'text-red-400'
                }`}>
                  {((evalReport.avg_score || 0) * 100).toFixed(1)}%
                </span>
                <p className="text-xs text-gray-500 mt-1">综合评分 ({evalReport.total_tests} 项测试)</p>
              </div>

              {/* 指标卡片 */}
              <div className="grid grid-cols-2 md:grid-cols-5 gap-3 mb-4">
                <EvalMetric label="AST 通过率" value={evalReport.ast_pass_rate} />
                <EvalMetric label="类结构完整" value={evalReport.class_rate} />
                <EvalMetric label="方法完整" value={evalReport.method_rate} />
                <EvalMetric label="Import 正确" value={evalReport.imports_rate} />
                <EvalMetric label="安全检查" value={evalReport.safety_rate} />
              </div>

              {/* 测试详情 */}
              {evalItems.length > 0 && (
                <div>
                  <button
                    onClick={() => setShowEvalDetails(!showEvalDetails)}
                    className="text-xs text-pink-400 hover:text-pink-300 transition-colors mb-2"
                  >
                    {showEvalDetails ? '收起' : '展开'} 测试详情 ({evalItems.length})
                  </button>
                  {showEvalDetails && (
                    <div className="max-h-[300px] overflow-y-auto space-y-1.5">
                      {evalItems.map((item) => (
                        <div
                          key={item.index}
                          className="flex items-center justify-between px-3 py-2 rounded-lg bg-dark/50 text-xs"
                        >
                          <div className="flex items-center gap-2 min-w-0 flex-1">
                            <span className={`flex-shrink-0 ${item.quality.score >= 0.8 ? 'text-emerald-400' : item.quality.score >= 0.5 ? 'text-yellow-400' : 'text-red-400'}`}>
                              {item.quality.score >= 0.8 ? '●' : item.quality.score >= 0.5 ? '●' : '●'}
                            </span>
                            <span className="text-gray-400 truncate">{item.prompt}</span>
                          </div>
                          <span className="text-gray-300 font-mono ml-2 flex-shrink-0">
                            {(item.quality.score * 100).toFixed(0)}%
                          </span>
                        </div>
                      ))}
                    </div>
                  )}
                </div>
              )}
            </div>
          )}

          {/* --- 部署状态 --- */}
          {stageData.deploy && (
            <div className={`bg-gradient-card border rounded-2xl rounded-tl-sm px-4 md:px-6 py-3 ${
              stageData.deploy.success ? 'border-emerald-500/30' : 'border-red-500/30'
            }`}>
              <div className="flex flex-wrap items-center gap-3 text-sm">
                <span>{stageData.deploy.success ? '🚀' : '❌'}</span>
                <span className={stageData.deploy.success ? 'text-emerald-400' : 'text-red-400'}>
                  {stageData.deploy.success ? '部署成功' : '部署失败'}
                </span>
                {stageData.deploy.fused_model_path && (
                  <span className="text-xs text-gray-500 font-mono">{stageData.deploy.fused_model_path}</span>
                )}
              </div>
            </div>
          )}

          {/* --- 训练日志 --- */}
          {logs.length > 0 && (
            <div className="bg-gradient-card border border-border rounded-2xl rounded-tl-sm px-4 md:px-6 py-4">
              <h2 className="text-sm font-semibold text-gray-400 mb-3 uppercase tracking-wider">训练日志</h2>
              <div
                ref={logRef}
                className="max-h-[300px] overflow-y-auto bg-dark rounded-xl p-3 border border-border"
              >
                {logs.map((line, i) => (
                  <div key={i} className="text-xs font-mono text-gray-400 leading-5 whitespace-pre-wrap">
                    {line}
                  </div>
                ))}
              </div>
            </div>
          )}

        </div>
      </div>
    </div>
  );
};

// ==================== 子组件 ====================

const MetricCard: React.FC<{ label: string; value: string; color: string }> = ({ label, value, color }) => (
  <div className="bg-gradient-card border border-border rounded-xl px-4 py-3 text-center">
    <div className={`text-lg md:text-xl font-bold font-mono ${color}`}>{value}</div>
    <div className="text-[10px] md:text-xs text-gray-500 mt-0.5">{label}</div>
  </div>
);

const EvalMetric: React.FC<{ label: string; value: number }> = ({ label, value: rawValue }) => {
  const value = rawValue ?? 0;
  const color = value >= 80 ? 'text-emerald-400' : value >= 50 ? 'text-yellow-400' : 'text-red-400';
  const bgColor = value >= 80 ? 'bg-emerald-500/60' : value >= 50 ? 'bg-yellow-500/60' : 'bg-red-500/60';

  return (
    <div className="bg-dark/50 rounded-xl px-3 py-2.5 text-center">
      <div className={`text-lg font-bold font-mono ${color}`}>{value.toFixed(1)}%</div>
      <div className="w-full h-1.5 bg-dark-light rounded-full mt-1.5 overflow-hidden">
        <div className={`h-full rounded-full ${bgColor}`} style={{ width: `${Math.min(value, 100)}%` }} />
      </div>
      <div className="text-[10px] text-gray-500 mt-1">{label}</div>
    </div>
  );
};

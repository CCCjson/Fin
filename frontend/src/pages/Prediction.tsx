import React, { useState, useEffect, useRef, useCallback } from 'react';
import { StockSymbolInput } from '../components/common/StockSymbolInput';
import {
  predictionService,
  type TrainProgressEvent,
  type TrainResult,
  type ModelInfo,
  type PredictionResult,
  type PredictionRecord,
  type PerformanceData,
} from '../services/predictionService';
import {
  LineChart, Line, XAxis, YAxis, CartesianGrid, Tooltip, Legend,
  ResponsiveContainer, Area, ComposedChart, BarChart, Bar, Cell,
  ReferenceLine,
} from 'recharts';

// ════════════════════ Tab 容器 ════════════════════

type TabKey = 'train' | 'predict' | 'validate';

const TABS: { key: TabKey; label: string }[] = [
  { key: 'train', label: '模型训练' },
  { key: 'predict', label: '预测分析' },
  { key: 'validate', label: '预测验证' },
];

export const Prediction: React.FC = () => {
  const [activeTab, setActiveTab] = useState<TabKey>('train');

  return (
    <div className="min-h-screen bg-gradient-dark p-3 md:p-6 pb-20 md:pb-6 space-y-4 md:space-y-6">
      <h1 className="text-2xl md:text-3xl font-bold text-white">股价预测</h1>

      {/* Tab 切换 */}
      <div className="flex gap-1 bg-dark-card rounded-lg p-1 w-fit">
        {TABS.map(t => (
          <button
            key={t.key}
            onClick={() => setActiveTab(t.key)}
            className={`px-3 py-1.5 md:px-5 md:py-2 rounded-md text-sm font-medium transition-all ${
              activeTab === t.key
                ? 'bg-primary text-white shadow-glow-blue'
                : 'text-gray-400 hover:text-white hover:bg-dark-light'
            }`}
          >
            {t.label}
          </button>
        ))}
      </div>

      {/* Tab 内容 */}
      {activeTab === 'train' && <TrainTab />}
      {activeTab === 'predict' && <PredictTab />}
      {activeTab === 'validate' && <ValidateTab />}
    </div>
  );
};

// ════════════════════ Tab 1: 模型训练 ════════════════════

const TrainTab: React.FC = () => {
  const [symbol, setSymbol] = useState('');
  const [period, setPeriod] = useState('2y');
  const [training, setTraining] = useState(false);
  const [progress, setProgress] = useState(0);
  const [stage, setStage] = useState('');
  const [message, setMessage] = useState('');
  const [trainResult, setTrainResult] = useState<TrainResult | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [models, setModels] = useState<ModelInfo[]>([]);
  const abortRef = useRef<AbortController | null>(null);
  const progressRef = useRef({ progress: 0, stage: '', message: '' });
  const rafRef = useRef(0);

  const loadModels = useCallback(async () => {
    try {
      const res = await predictionService.getModels();
      setModels(res.models || []);
    } catch {}
  }, []);

  useEffect(() => { loadModels(); }, [loadModels]);

  const handleTrain = async () => {
    if (!symbol || training) return;
    setTraining(true);
    setError(null);
    setTrainResult(null);
    setProgress(0);
    setStage('');
    setMessage('');

    abortRef.current = new AbortController();

    // rAF 节流更新 UI
    const flushProgress = () => {
      setProgress(progressRef.current.progress);
      setStage(progressRef.current.stage);
      setMessage(progressRef.current.message);
    };

    try {
      await predictionService.trainModel(
        { symbol, period, forward_days: 5 },
        (event: TrainProgressEvent) => {
          if (event.event === 'progress') {
            progressRef.current = {
              progress: event.progress || 0,
              stage: event.stage || '',
              message: event.message || '',
            };
            cancelAnimationFrame(rafRef.current);
            rafRef.current = requestAnimationFrame(flushProgress);
          } else if (event.event === 'complete') {
            setProgress(1);
            setTrainResult(event.result || null);
          } else if (event.event === 'error') {
            setError(event.message || '训练出错');
          }
        },
        abortRef.current.signal,
      );
    } catch (e: any) {
      if (e.name !== 'AbortError') {
        setError(e.message || '训练失败');
      }
    } finally {
      setTraining(false);
      loadModels();
    }
  };

  const stages = [
    { key: 'data', label: '获取数据' },
    { key: 'features', label: '特征工程' },
    { key: 'lstm', label: 'LSTM' },
    { key: 'xgboost', label: 'XGBoost' },
    { key: 'save', label: '保存模型' },
  ];
  const currentStageIdx = stages.findIndex(s => s.key === stage);

  return (
    <div className="space-y-4 md:space-y-6">
      {/* 训练配置 */}
      <div className="bg-dark-card rounded-xl p-3 md:p-6 border border-border">
        <h2 className="text-lg md:text-xl font-semibold text-white mb-4">训练配置</h2>
        <div className="flex flex-wrap gap-4 items-end">
          <div className="w-64">
            <label className="block text-sm text-gray-400 mb-1">股票代码</label>
            <StockSymbolInput value={symbol} onChange={(s) => setSymbol(s)} disabled={training} />
          </div>
          <div>
            <label className="block text-sm text-gray-400 mb-1">训练周期</label>
            <select
              value={period}
              onChange={e => setPeriod(e.target.value)}
              disabled={training}
              className="px-3 py-2 bg-dark-light text-white rounded-lg border border-border focus:border-primary outline-none"
            >
              {['1y', '2y', '3y', '5y'].map(p => (
                <option key={p} value={p}>{p === '1y' ? '1 年' : p === '2y' ? '2 年' : p === '3y' ? '3 年' : '5 年'}</option>
              ))}
            </select>
          </div>
          <button
            onClick={handleTrain}
            disabled={!symbol || training}
            className="px-6 py-2 bg-primary hover:bg-primary-dark text-white rounded-lg font-medium disabled:opacity-50 disabled:cursor-not-allowed transition-all"
          >
            {training ? '训练中...' : '开始训练'}
          </button>
        </div>
      </div>

      {/* 训练进度 */}
      {(training || trainResult) && (
        <div className="bg-dark-card rounded-xl p-3 md:p-6 border border-border space-y-4">
          <h2 className="text-lg md:text-xl font-semibold text-white">
            {trainResult ? '训练完成' : '训练进度'}
          </h2>

          {/* 阶段指示 */}
          <div className="flex flex-wrap gap-2 items-center">
            {stages.map((s, i) => {
              const done = i < currentStageIdx || trainResult;
              const active = i === currentStageIdx && !trainResult;
              return (
                <div key={s.key} className="flex items-center gap-1">
                  <span className={`w-6 h-6 rounded-full flex items-center justify-center text-xs font-bold ${
                    done ? 'bg-green-500 text-white' : active ? 'bg-primary text-white animate-pulse' : 'bg-dark-light text-gray-500'
                  }`}>
                    {done ? '\u2713' : i + 1}
                  </span>
                  <span className={`text-sm ${done ? 'text-green-400' : active ? 'text-white' : 'text-gray-500'}`}>
                    {s.label}
                  </span>
                  {i < stages.length - 1 && <span className="text-gray-600 mx-1">-</span>}
                </div>
              );
            })}
          </div>

          {/* 进度条 */}
          <div className="w-full bg-dark-light rounded-full h-3">
            <div
              className="bg-primary h-3 rounded-full transition-all duration-300"
              style={{ width: `${Math.round(progress * 100)}%` }}
            />
          </div>
          <p className="text-sm text-gray-400">{message || `${Math.round(progress * 100)}%`}</p>

          {/* 训练结果 */}
          {trainResult && (
            <div className="grid grid-cols-2 gap-4 mt-4">
              <div className="bg-dark-light rounded-lg p-4">
                <h3 className="text-sm text-gray-400 mb-2">LSTM 验证指标</h3>
                <div className="space-y-1">
                  <div className="flex justify-between text-sm">
                    <span className="text-gray-300">MSE</span>
                    <span className="text-white font-mono">{trainResult.lstm.mse}</span>
                  </div>
                  <div className="flex justify-between text-sm">
                    <span className="text-gray-300">MAE</span>
                    <span className="text-white font-mono">{trainResult.lstm.mae}</span>
                  </div>
                </div>
              </div>
              <div className="bg-dark-light rounded-lg p-4">
                <h3 className="text-sm text-gray-400 mb-2">XGBoost 验证指标</h3>
                <div className="space-y-1">
                  <div className="flex justify-between text-sm">
                    <span className="text-gray-300">准确率</span>
                    <span className="text-white font-mono">{(trainResult.xgboost.accuracy * 100).toFixed(1)}%</span>
                  </div>
                  <div className="flex justify-between text-sm">
                    <span className="text-gray-300">AUC</span>
                    <span className="text-white font-mono">{trainResult.xgboost.auc}</span>
                  </div>
                </div>
              </div>
              <div className="col-span-2 text-sm text-gray-400">
                训练样本: {trainResult.data_points} | 数据范围: {trainResult.train_range[0]} ~ {trainResult.train_range[1]}
              </div>
            </div>
          )}
        </div>
      )}

      {error && (
        <div className="bg-red-500/10 border border-red-500/30 rounded-xl p-4 text-red-400">
          {error}
        </div>
      )}

      {/* 已训练模型列表 */}
      <div className="bg-dark-card rounded-xl p-3 md:p-6 border border-border">
        <h2 className="text-lg md:text-xl font-semibold text-white mb-4">已训练模型</h2>
        {models.length === 0 ? (
          <p className="text-gray-500 text-sm">暂无已训练模型</p>
        ) : (
          <div className="overflow-x-auto">
            <table className="w-full text-sm">
              <thead>
                <tr className="text-gray-400 border-b border-border">
                  <th className="text-left py-2 px-3">股票</th>
                  <th className="text-left py-2 px-3">周期</th>
                  <th className="text-right py-2 px-3">数据量</th>
                  <th className="text-right py-2 px-3">LSTM MSE</th>
                  <th className="text-right py-2 px-3">XGB 准确率</th>
                  <th className="text-left py-2 px-3">训练时间</th>
                </tr>
              </thead>
              <tbody>
                {models.map(m => {
                  const metrics = m.val_metrics || {};
                  const lstmMse = metrics.lstm?.mse ?? '-';
                  const xgbAcc = metrics.xgboost?.accuracy != null
                    ? `${(metrics.xgboost.accuracy * 100).toFixed(1)}%`
                    : '-';
                  return (
                    <tr key={m.id} className="border-b border-border/50 hover:bg-dark-light/50">
                      <td className="py-2 px-3 text-primary-light font-mono">{m.symbol}</td>
                      <td className="py-2 px-3 text-gray-300">{m.train_period}</td>
                      <td className="py-2 px-3 text-right text-gray-300">{m.data_points}</td>
                      <td className="py-2 px-3 text-right text-gray-300 font-mono">{lstmMse}</td>
                      <td className="py-2 px-3 text-right text-gray-300 font-mono">{xgbAcc}</td>
                      <td className="py-2 px-3 text-gray-500">{m.created_at?.slice(0, 16).replace('T', ' ')}</td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        )}
      </div>
    </div>
  );
};

// ════════════════════ Tab 2: 预测分析 ════════════════════

const PredictTab: React.FC = () => {
  const [symbol, setSymbol] = useState('');
  const [forwardDays, setForwardDays] = useState(5);
  const [loading, setLoading] = useState(false);
  const [result, setResult] = useState<PredictionResult | null>(null);
  const [history, setHistory] = useState<PredictionRecord[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [chartData, setChartData] = useState<any[]>([]);

  const handlePredict = async () => {
    if (!symbol) return;
    setLoading(true);
    setError(null);
    try {
      const res = await predictionService.predict({ symbol, forward_days: forwardDays });
      setResult(res);
      buildChartData(res);

      // 同时加载历史记录
      const histRes = await predictionService.getHistory(symbol, 20);
      setHistory(histRes.records || []);
    } catch (e: any) {
      const msg = e?.response?.data?.detail || e?.message || '预测失败';
      setError(msg);
    } finally {
      setLoading(false);
    }
  };

  const buildChartData = (res: PredictionResult) => {
    // 构建图表数据：预测价格 + 置信区间
    const data = res.predicted_prices.map((price, i) => {
      const spread = price * (1 - res.confidence) * 0.1;
      return {
        day: `Day ${i + 1}`,
        predicted: price,
        upper: +(price + spread).toFixed(2),
        lower: +(price - spread).toFixed(2),
      };
    });
    setChartData(data);
  };

  return (
    <div className="space-y-4 md:space-y-6">
      {/* 输入区 */}
      <div className="bg-dark-card rounded-xl p-3 md:p-6 border border-border">
        <div className="flex flex-wrap gap-4 items-end">
          <div className="w-64">
            <label className="block text-sm text-gray-400 mb-1">股票代码</label>
            <StockSymbolInput value={symbol} onChange={(s) => setSymbol(s)} disabled={loading} />
          </div>
          <div>
            <label className="block text-sm text-gray-400 mb-1">预测天数</label>
            <select
              value={forwardDays}
              onChange={e => setForwardDays(+e.target.value)}
              className="px-3 py-2 bg-dark-light text-white rounded-lg border border-border focus:border-primary outline-none"
            >
              {[3, 5, 10, 15, 20].map(d => (
                <option key={d} value={d}>{d} 天</option>
              ))}
            </select>
          </div>
          <button
            onClick={handlePredict}
            disabled={!symbol || loading}
            className="px-6 py-2 bg-primary hover:bg-primary-dark text-white rounded-lg font-medium disabled:opacity-50 disabled:cursor-not-allowed transition-all"
          >
            {loading ? '预测中...' : '预测'}
          </button>
        </div>
        {error && (
          <p className="mt-3 text-red-400 text-sm">{error}</p>
        )}
      </div>

      {/* 预测结果 */}
      {result && (
        <>
          {/* 图表 */}
          <div className="bg-dark-card rounded-xl p-3 md:p-6 border border-border">
            <h2 className="text-lg md:text-xl font-semibold text-white mb-4">价格预测图表</h2>
            <ResponsiveContainer width="100%" height={300}>
              <ComposedChart data={chartData}>
                <CartesianGrid strokeDasharray="3 3" stroke="#2a2a3e" />
                <XAxis dataKey="day" stroke="#666" />
                <YAxis domain={['auto', 'auto']} stroke="#666" />
                <Tooltip
                  contentStyle={{ backgroundColor: '#1a1a2e', border: '1px solid #333', borderRadius: '8px' }}
                  labelStyle={{ color: '#999' }}
                />
                <Area type="monotone" dataKey="upper" stroke="none" fill="#3b82f6" fillOpacity={0.1} />
                <Area type="monotone" dataKey="lower" stroke="none" fill="#3b82f6" fillOpacity={0.1} />
                <Line type="monotone" dataKey="predicted" stroke="#3b82f6" strokeWidth={2} strokeDasharray="8 4" dot={{ r: 4 }} />
              </ComposedChart>
            </ResponsiveContainer>
          </div>

          {/* 摘要 + 模型详情 */}
          <div className="grid grid-cols-1 md:grid-cols-2 gap-4 md:gap-6">
            <div className="bg-dark-card rounded-xl p-3 md:p-6 border border-border">
              <h3 className="text-base font-semibold text-white mb-3">预测摘要</h3>
              <div className="space-y-3">
                <div className="flex justify-between">
                  <span className="text-gray-400">方向</span>
                  <span className={`font-bold ${result.direction === 'UP' ? 'text-green-400' : 'text-red-400'}`}>
                    {result.direction === 'UP' ? '\u2191 看涨' : '\u2193 看跌'}
                  </span>
                </div>
                <div className="flex justify-between">
                  <span className="text-gray-400">置信度</span>
                  <span className="text-white font-mono">{(result.confidence * 100).toFixed(0)}%</span>
                </div>
                <div className="flex justify-between">
                  <span className="text-gray-400">预测收益</span>
                  <span className={`font-mono ${result.predicted_return >= 0 ? 'text-green-400' : 'text-red-400'}`}>
                    {result.predicted_return >= 0 ? '+' : ''}{(result.predicted_return * 100).toFixed(2)}%
                  </span>
                </div>
                <div className="flex justify-between">
                  <span className="text-gray-400">模型一致</span>
                  <span className={result.agreement ? 'text-green-400' : 'text-yellow-400'}>
                    {result.agreement ? '一致' : '分歧'}
                  </span>
                </div>
              </div>
            </div>

            <div className="bg-dark-card rounded-xl p-3 md:p-6 border border-border">
              <h3 className="text-base font-semibold text-white mb-3">模型详情</h3>
              <div className="space-y-4">
                <div>
                  <div className="flex justify-between text-sm mb-1">
                    <span className="text-gray-400">LSTM</span>
                    <span className={result.models.lstm.direction === 'UP' ? 'text-green-400' : 'text-red-400'}>
                      {result.models.lstm.direction === 'UP' ? '\u2191 看涨' : '\u2193 看跌'}
                    </span>
                  </div>
                  <p className="text-xs text-gray-500">收益: {(result.models.lstm.predicted_return * 100).toFixed(2)}% | MSE: {result.models.lstm.mse}</p>
                </div>
                <div>
                  <div className="flex justify-between text-sm mb-1">
                    <span className="text-gray-400">XGBoost</span>
                    <span className={result.models.xgboost.direction === 'UP' ? 'text-green-400' : 'text-red-400'}>
                      {result.models.xgboost.direction === 'UP' ? '\u2191 看涨' : '\u2193 看跌'}
                    </span>
                  </div>
                  <p className="text-xs text-gray-500">看涨概率: {(result.models.xgboost.probability_up * 100).toFixed(1)}% | 准确率: {(result.models.xgboost.accuracy * 100).toFixed(1)}%</p>
                </div>
              </div>
            </div>
          </div>

          {/* 历史预测记录 */}
          {history.length > 0 && (
            <div className="bg-dark-card rounded-xl p-3 md:p-6 border border-border">
              <h3 className="text-base font-semibold text-white mb-4">历史预测记录</h3>
              <div className="overflow-x-auto">
                <table className="w-full text-sm">
                  <thead>
                    <tr className="text-gray-400 border-b border-border">
                      <th className="text-left py-2 px-3">日期</th>
                      <th className="text-center py-2 px-3">方向</th>
                      <th className="text-right py-2 px-3">置信度</th>
                      <th className="text-right py-2 px-3">预测收益</th>
                      <th className="text-right py-2 px-3">实际收益</th>
                      <th className="text-center py-2 px-3">结果</th>
                    </tr>
                  </thead>
                  <tbody>
                    {history.map(r => (
                      <tr key={r.id} className="border-b border-border/50 hover:bg-dark-light/50">
                        <td className="py-2 px-3 text-gray-300">{r.prediction_date}</td>
                        <td className="py-2 px-3 text-center">
                          <span className={r.direction === 'UP' ? 'text-green-400' : 'text-red-400'}>
                            {r.direction === 'UP' ? '\u2191' : '\u2193'}
                          </span>
                        </td>
                        <td className="py-2 px-3 text-right text-gray-300">{(r.confidence * 100).toFixed(0)}%</td>
                        <td className="py-2 px-3 text-right font-mono">
                          <span className={r.predicted_return >= 0 ? 'text-green-400' : 'text-red-400'}>
                            {r.predicted_return >= 0 ? '+' : ''}{(r.predicted_return * 100).toFixed(2)}%
                          </span>
                        </td>
                        <td className="py-2 px-3 text-right font-mono">
                          {r.actual_return != null ? (
                            <span className={r.actual_return >= 0 ? 'text-green-400' : 'text-red-400'}>
                              {r.actual_return >= 0 ? '+' : ''}{(r.actual_return * 100).toFixed(2)}%
                            </span>
                          ) : <span className="text-gray-500">待验证</span>}
                        </td>
                        <td className="py-2 px-3 text-center">
                          {r.outcome === 'WIN' && <span className="text-green-400 font-bold">WIN</span>}
                          {r.outcome === 'LOSS' && <span className="text-red-400 font-bold">LOSS</span>}
                          {!r.outcome && <span className="text-gray-500">-</span>}
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </div>
          )}
        </>
      )}
    </div>
  );
};

// ════════════════════ Tab 3: 预测验证 ════════════════════

const ValidateTab: React.FC = () => {
  const [perf, setPerf] = useState<PerformanceData | null>(null);
  const [records, setRecords] = useState<PredictionRecord[]>([]);
  const [loading, setLoading] = useState(false);
  const [backfilling, setBackfilling] = useState(false);
  const [backfillMsg, setBackfillMsg] = useState<string | null>(null);
  const [symbol, setSymbol] = useState('');

  const loadData = useCallback(async () => {
    setLoading(true);
    try {
      const s = symbol || undefined;
      const [perfRes, histRes] = await Promise.all([
        predictionService.getPerformance(s),
        predictionService.getHistory(s, 100),
      ]);
      setPerf(perfRes);
      setRecords(histRes.records || []);
    } catch (e) {
      console.error('加载验证数据失败', e);
    } finally {
      setLoading(false);
    }
  }, [symbol]);

  useEffect(() => { loadData(); }, [loadData]);

  const handleBackfill = async () => {
    setBackfilling(true);
    setBackfillMsg(null);
    try {
      const res = await predictionService.backfill();
      setBackfillMsg(`回填完成: 成功 ${res.filled} / ${res.total}, 失败 ${res.errors}`);
      loadData();
    } catch (e: any) {
      setBackfillMsg(`回填失败: ${e.message}`);
    } finally {
      setBackfilling(false);
    }
  };

  // 置信度校准图数据
  const calibrationData = perf?.confidence_calibration?.map(c => ({
    bucket: c.bucket,
    actual: c.actual_accuracy != null ? +(c.actual_accuracy * 100).toFixed(1) : 0,
    ideal: (() => {
      const mid = (parseFloat(c.bucket.split('-')[0]) + parseFloat(c.bucket.split('-')[1])) / 2;
      return +(mid * 100).toFixed(1);
    })(),
    count: c.count,
  })) || [];

  // 分模型对比数据
  const modelCompareData = perf?.by_model ? [
    { name: 'LSTM', accuracy: +(perf.by_model.lstm.direction_accuracy * 100).toFixed(1) },
    { name: 'XGBoost', accuracy: +(perf.by_model.xgboost.direction_accuracy * 100).toFixed(1) },
    { name: '集成', accuracy: +(perf.by_model.ensemble.direction_accuracy * 100).toFixed(1) },
  ] : [];

  return (
    <div className="space-y-4 md:space-y-6">
      {/* 筛选栏 */}
      <div className="bg-dark-card rounded-xl p-3 md:p-6 border border-border">
        <div className="flex flex-wrap gap-4 items-end">
          <div className="w-64">
            <label className="block text-sm text-gray-400 mb-1">筛选股票 (可选)</label>
            <StockSymbolInput value={symbol} onChange={(s) => setSymbol(s)} placeholder="全部股票" />
          </div>
          <button
            onClick={handleBackfill}
            disabled={backfilling}
            className="px-5 py-2 bg-yellow-600 hover:bg-yellow-700 text-white rounded-lg font-medium disabled:opacity-50 transition-all"
          >
            {backfilling ? '回填中...' : '手动回填'}
          </button>
          {backfillMsg && <span className="text-sm text-gray-400">{backfillMsg}</span>}
        </div>
      </div>

      {loading ? (
        <div className="text-center py-12 text-gray-500">加载中...</div>
      ) : perf && (
        <>
          {/* 成绩总览 */}
          <div className="grid grid-cols-2 md:grid-cols-4 gap-4">
            <StatCard
              label="方向准确率"
              value={perf.overall ? `${(perf.overall.direction_accuracy * 100).toFixed(1)}%` : '-'}
              color={perf.overall && perf.overall.direction_accuracy > 0.55 ? 'green' : 'gray'}
            />
            <StatCard
              label="价格 MAE"
              value={perf.overall?.price_mae != null ? perf.overall.price_mae.toFixed(2) : '-'}
              color="blue"
            />
            <StatCard
              label="累计收益"
              value={`${perf.cumulative_return >= 0 ? '+' : ''}${(perf.cumulative_return * 100).toFixed(1)}%`}
              color={perf.cumulative_return >= 0 ? 'green' : 'red'}
            />
            <StatCard
              label={perf.recent_streak ? `当前连${perf.recent_streak.type === 'WIN' ? '胜' : '败'}` : '连胜/连败'}
              value={perf.recent_streak ? `${perf.recent_streak.count} 次` : '-'}
              color={perf.recent_streak?.type === 'WIN' ? 'green' : 'red'}
            />
          </div>

          {/* 图表区 */}
          <div className="grid grid-cols-1 md:grid-cols-2 gap-4 md:gap-6">
            {/* 分模型准确率对比 */}
            {modelCompareData.length > 0 && (
              <div className="bg-dark-card rounded-xl p-3 md:p-6 border border-border">
                <h3 className="text-base font-semibold text-white mb-4">分模型准确率对比</h3>
                <ResponsiveContainer width="100%" height={200}>
                  <BarChart data={modelCompareData}>
                    <CartesianGrid strokeDasharray="3 3" stroke="#2a2a3e" />
                    <XAxis dataKey="name" stroke="#666" />
                    <YAxis domain={[0, 100]} stroke="#666" unit="%" />
                    <Tooltip contentStyle={{ backgroundColor: '#1a1a2e', border: '1px solid #333', borderRadius: '8px' }} />
                    <ReferenceLine y={50} stroke="#666" strokeDasharray="3 3" label={{ value: '50%', fill: '#666' }} />
                    <Bar dataKey="accuracy" radius={[4, 4, 0, 0]}>
                      {modelCompareData.map((_, i) => (
                        <Cell key={i} fill={['#f59e0b', '#8b5cf6', '#3b82f6'][i]} />
                      ))}
                    </Bar>
                  </BarChart>
                </ResponsiveContainer>
              </div>
            )}

            {/* 置信度校准图 */}
            {calibrationData.length > 0 && (
              <div className="bg-dark-card rounded-xl p-3 md:p-6 border border-border">
                <h3 className="text-base font-semibold text-white mb-4">置信度校准</h3>
                <ResponsiveContainer width="100%" height={200}>
                  <LineChart data={calibrationData}>
                    <CartesianGrid strokeDasharray="3 3" stroke="#2a2a3e" />
                    <XAxis dataKey="bucket" stroke="#666" />
                    <YAxis domain={[0, 100]} stroke="#666" unit="%" />
                    <Tooltip contentStyle={{ backgroundColor: '#1a1a2e', border: '1px solid #333', borderRadius: '8px' }} />
                    <Legend />
                    <Line type="monotone" dataKey="actual" name="实际准确率" stroke="#3b82f6" strokeWidth={2} dot={{ r: 4 }} />
                    <Line type="monotone" dataKey="ideal" name="理想校准" stroke="#666" strokeDasharray="5 5" dot={false} />
                  </LineChart>
                </ResponsiveContainer>
              </div>
            )}
          </div>

          {/* 验证明细表格 */}
          <div className="bg-dark-card rounded-xl p-3 md:p-6 border border-border">
            <h3 className="text-base font-semibold text-white mb-4">
              验证明细
              <span className="text-sm text-gray-500 ml-2">
                (已评估 {perf.evaluated} / {perf.total_predictions}, 待验证 {perf.pending})
              </span>
            </h3>
            <div className="overflow-x-auto">
              <table className="w-full text-sm">
                <thead>
                  <tr className="text-gray-400 border-b border-border">
                    <th className="text-left py-2 px-3">日期</th>
                    <th className="text-left py-2 px-3">股票</th>
                    <th className="text-center py-2 px-3">预测方向</th>
                    <th className="text-right py-2 px-3">置信度</th>
                    <th className="text-right py-2 px-3">预测收益</th>
                    <th className="text-right py-2 px-3">实际收益</th>
                    <th className="text-center py-2 px-3">结果</th>
                  </tr>
                </thead>
                <tbody>
                  {records.map(r => (
                    <tr key={r.id} className="border-b border-border/50 hover:bg-dark-light/50">
                      <td className="py-2 px-3 text-gray-300">{r.prediction_date}</td>
                      <td className="py-2 px-3 text-primary-light font-mono">{r.symbol}</td>
                      <td className="py-2 px-3 text-center">
                        <span className={r.direction === 'UP' ? 'text-green-400' : 'text-red-400'}>
                          {r.direction === 'UP' ? '\u2191 UP' : '\u2193 DOWN'}
                        </span>
                      </td>
                      <td className="py-2 px-3 text-right text-gray-300">{(r.confidence * 100).toFixed(0)}%</td>
                      <td className="py-2 px-3 text-right font-mono">
                        <span className={r.predicted_return >= 0 ? 'text-green-400' : 'text-red-400'}>
                          {r.predicted_return >= 0 ? '+' : ''}{(r.predicted_return * 100).toFixed(2)}%
                        </span>
                      </td>
                      <td className="py-2 px-3 text-right font-mono">
                        {r.actual_return != null ? (
                          <span className={r.actual_return >= 0 ? 'text-green-400' : 'text-red-400'}>
                            {r.actual_return >= 0 ? '+' : ''}{(r.actual_return * 100).toFixed(2)}%
                          </span>
                        ) : <span className="text-gray-500">-</span>}
                      </td>
                      <td className="py-2 px-3 text-center">
                        {r.outcome === 'WIN' && <span className="px-2 py-0.5 bg-green-500/20 text-green-400 rounded text-xs font-bold">WIN</span>}
                        {r.outcome === 'LOSS' && <span className="px-2 py-0.5 bg-red-500/20 text-red-400 rounded text-xs font-bold">LOSS</span>}
                        {!r.outcome && <span className="text-gray-500">-</span>}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
              {records.length === 0 && (
                <p className="text-center py-8 text-gray-500">暂无预测记录</p>
              )}
            </div>
          </div>
        </>
      )}
    </div>
  );
};

// ════════════════════ 通用组件 ════════════════════

const StatCard: React.FC<{ label: string; value: string; color: string }> = ({ label, value, color }) => {
  const colorMap: Record<string, string> = {
    green: 'text-green-400',
    red: 'text-red-400',
    blue: 'text-blue-400',
    yellow: 'text-yellow-400',
    gray: 'text-gray-300',
  };

  return (
    <div className="bg-dark-card rounded-xl p-3 md:p-4 border border-border">
      <p className="text-xs text-gray-500 mb-1">{label}</p>
      <p className={`text-xl md:text-2xl font-bold font-mono ${colorMap[color] || 'text-white'}`}>{value}</p>
    </div>
  );
};

import React, { useState, useEffect, useCallback, useRef } from 'react';
import { backtestService } from '../../services/backtestService';
import { toast } from '../common/Toast';
import { Card } from '../common/Card';
import { CppBacktestForm } from './CppBacktestForm';
import type { CppFormState } from './CppBacktestForm';
import { CppBacktestResult } from './CppBacktestResult';
import { CppHistoryList } from './CppHistoryList';
import { BacktestComparison } from './BacktestComparison';
import { BatchBacktestForm } from './BatchBacktestForm';
import { BatchProgressBar } from './BatchProgressBar';
import { BatchRanking } from './BatchRanking';
import { ParamHeatmap } from './ParamHeatmap';
import { WalkForwardPanel } from './WalkForwardPanel';
import type { BatchBacktestConfig, BatchProgressEvent, BatchRankingItem } from '../../types';

type ViewMode = 'single' | 'batch' | 'walkforward';

const defaultForm = (): CppFormState => {
  const yesterday = new Date();
  yesterday.setDate(yesterday.getDate() - 1);
  return {
    symbol: '000001.SZ',
    strategy: 'MA_CROSS',
    start_date: '2010-01-01',
    end_date: yesterday.toISOString().slice(0, 10),
    initial_capital: 100000,
    market: 'a_share',
    slippage_pct: -1,  // -1 = use market default
    risk_enabled: false,
    stop_loss_pct: 0.05,
    trailing_stop: false,
    trailing_stop_pct: 0.08,
    max_position_pct: 1.0,
    fast_period: 5,
    slow_period: 20,
    lookback: 20,
    buy_threshold: 0.05,
    sell_threshold: -0.03,
    macd_fast: 12,
    macd_slow: 26,
    signal_period: 9,
    rsi_period: 14,
    oversold: 30,
    overbought: 70,
    kdj_n: 9,
    kdj_m1: 3,
    kdj_m2: 3,
    kdj_oversold: 20,
    kdj_overbought: 80,
    boll_period: 20,
    num_std: 2.0,
    subStrategies: [],
    combo_threshold: 0.5,
    symbol2: '',
    pairs_lookback: 60,
    entry_z: 2.0,
    exit_z: 0.5,
  };
};

export const CppBacktestPanel: React.FC = () => {
  const [viewMode, setViewMode] = useState<ViewMode>('single');

  // ── 单次回测 state ──
  const [form, setForm] = useState<CppFormState>(defaultForm);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState('');
  const [result, setResult] = useState<any>(null);

  // 历史记录
  const [historyItems, setHistoryItems] = useState<any[]>([]);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [checkedIds, setCheckedIds] = useState<Set<string>>(new Set());
  const [showCompare, setShowCompare] = useState(false);
  const [showForm, setShowForm] = useState(true);

  // ── 批量回测 state ──
  const [batchLoading, setBatchLoading] = useState(false);
  const [batchError, setBatchError] = useState('');
  const [batchProgress, setBatchProgress] = useState<{ total: number; current: number; completed: number; failed: number }>({ total: 0, current: 0, completed: 0, failed: 0 });
  const [batchLogs, setBatchLogs] = useState<BatchProgressEvent[]>([]);
  const [batchRanking, setBatchRanking] = useState<BatchRankingItem[]>([]);
  const [batchId, setBatchId] = useState('');
  const [batchDone, setBatchDone] = useState(false);
  const [batchParamGrid, setBatchParamGrid] = useState<Record<string, number[]>>({});
  const [batchMode, setBatchMode] = useState('');
  const abortRef = useRef<AbortController | null>(null);

  // ── 排行榜 → 查看详情 ──
  const [detailResult, setDetailResult] = useState<any>(null);

  const loadHistory = useCallback(async () => {
    try {
      const data = await backtestService.getBacktestTasks({
        strategy_type: undefined,
        limit: 50,
      });
      const items = (data.tasks || []).filter((t: any) =>
        t.strategy_type?.startsWith('CPP_')
      );
      setHistoryItems(items);
    } catch (e) {
      console.error('Load history failed:', e);
    }
  }, []);

  useEffect(() => {
    loadHistory();
  }, [loadHistory]);

  // ── 单次回测逻辑 ──

  const buildParams = (): Record<string, any> => {
    switch (form.strategy) {
      case 'MA_CROSS':
        return { fast_period: form.fast_period, slow_period: form.slow_period };
      case 'MOMENTUM':
        return { lookback: form.lookback, buy_threshold: form.buy_threshold, sell_threshold: form.sell_threshold };
      case 'MACD':
        return { fast_period: form.macd_fast, slow_period: form.macd_slow, signal_period: form.signal_period };
      case 'RSI':
        return { period: form.rsi_period, oversold: form.oversold, overbought: form.overbought };
      case 'KDJ':
        return { n: form.kdj_n, m1: form.kdj_m1, m2: form.kdj_m2, oversold: form.kdj_oversold, overbought: form.kdj_overbought };
      case 'BOLLINGER':
        return { period: form.boll_period, num_std: form.num_std };
      case 'COMBO':
        return {
          threshold: form.combo_threshold,
          sub_strategies: form.subStrategies.map(s => ({
            name: s.name,
            weight: s.weight,
            params: s.params,
          })),
        };
      case 'PAIRS':
        return { symbol2: form.symbol2, lookback: form.pairs_lookback, entry_z: form.entry_z, exit_z: form.exit_z };
      default:
        return {};
    }
  };

  const handleSubmit = async () => {
    setLoading(true);
    setError('');
    setResult(null);
    try {
      const params = buildParams();
      const strategyLabel = form.strategy === 'PAIRS'
        ? `${form.strategy} ${form.symbol}/${form.symbol2}`
        : `${form.strategy} ${form.symbol}`;
      const res = await backtestService.runCppBacktest({
        symbol: form.symbol,
        strategy: form.strategy,
        params,
        initial_capital: form.initial_capital,
        market: form.market,
        start_date: form.start_date,
        end_date: form.end_date,
        name: `C++ ${strategyLabel}`,
        symbol2: form.strategy === 'PAIRS' ? form.symbol2 : undefined,
        slippage_pct: form.slippage_pct >= 0 ? form.slippage_pct : undefined,
        risk_config: form.risk_enabled ? {
          enabled: true,
          stop_loss_pct: form.stop_loss_pct,
          trailing_stop: form.trailing_stop,
          trailing_stop_pct: form.trailing_stop_pct,
          max_position_pct: form.max_position_pct,
        } : undefined,
      });
      setResult(res);
      setSelectedId(res.task_id);
      loadHistory();
    } catch (e: any) {
      setError(e.response?.data?.detail || e.message || '回测失败');
    } finally {
      setLoading(false);
    }
  };

  const handleSelectHistory = async (item: any) => {
    setSelectedId(item.task_id);
    if (item.status === 'completed') {
      try {
        setLoading(true);
        const data = await backtestService.getBacktestResult(item.task_id);
        setResult(data);
        setDetailResult(null);
        setShowForm(false);
      } catch (e) {
        console.error('Load result failed:', e);
      } finally {
        setLoading(false);
      }
    }
  };

  const handleCheck = (taskId: string) => {
    const next = new Set(checkedIds);
    if (next.has(taskId)) next.delete(taskId);
    else next.add(taskId);
    setCheckedIds(next);
  };

  const handleDelete = async (taskId: string) => {
    if (!confirm('确定删除这条回测记录吗？')) return;
    try {
      await backtestService.deleteBacktestTask(taskId);
      if (selectedId === taskId) {
        setSelectedId(null);
        setResult(null);
      }
      const next = new Set(checkedIds);
      next.delete(taskId);
      setCheckedIds(next);
      loadHistory();
    } catch (e: any) {
      toast.error('删除失败: ' + (e.response?.data?.detail || e.message));
    }
  };

  // ── 批量回测逻辑 ──

  const handleBatchSubmit = async (config: BatchBacktestConfig) => {
    setBatchLoading(true);
    setBatchError('');
    setBatchRanking([]);
    setBatchLogs([]);
    setBatchDone(false);
    setBatchProgress({ total: 0, current: 0, completed: 0, failed: 0 });
    setDetailResult(null);
    setBatchParamGrid(config.param_grid || {});
    setBatchMode(config.mode);

    const controller = new AbortController();
    abortRef.current = controller;

    try {
      await backtestService.runBatchBacktest(
        config,
        (event: BatchProgressEvent) => {
          if (event.event === 'start') {
            setBatchId(event.batch_id || '');
            setBatchProgress(prev => ({ ...prev, total: event.total || 0 }));
          } else if (event.event === 'progress') {
            setBatchProgress({
              total: event.total || 0,
              current: event.current || 0,
              completed: event.completed || 0,
              failed: event.failed || 0,
            });
            setBatchLogs(prev => [...prev, event]);
          } else if (event.event === 'complete') {
            setBatchRanking(event.ranking || []);
            setBatchDone(true);
            setBatchProgress({
              total: event.total || 0,
              current: event.total || 0,
              completed: event.completed || 0,
              failed: event.failed || 0,
            });
            loadHistory();
          } else if (event.event === 'error') {
            setBatchError(event.error || '未知错误');
          }
        },
        controller.signal,
      );
    } catch (e: any) {
      if (e.name !== 'AbortError') {
        setBatchError(e.message || '批量回测失败');
      }
    } finally {
      setBatchLoading(false);
      abortRef.current = null;
    }
  };

  const handleBatchCancel = () => {
    abortRef.current?.abort();
    setBatchLoading(false);
  };

  const handleSelectBatchTask = async (taskId: string) => {
    try {
      const data = await backtestService.getBacktestResult(taskId);
      setDetailResult(data);
    } catch (e) {
      console.error('Load batch task result failed:', e);
    }
  };

  return (
    <div className="space-y-4">
      {/* 模式切换 */}
      <div className="flex gap-2">
        <button
          onClick={() => { setViewMode('single'); setDetailResult(null); }}
          className={`px-5 py-2 text-sm rounded-xl font-medium transition-all ${
            viewMode === 'single'
              ? 'bg-primary text-dark shadow-lg shadow-primary/20'
              : 'bg-dark-light text-gray-400 hover:bg-dark-lighter'
          }`}
        >
          单次回测
        </button>
        <button
          onClick={() => { setViewMode('batch'); setDetailResult(null); }}
          className={`px-5 py-2 text-sm rounded-xl font-medium transition-all ${
            viewMode === 'batch'
              ? 'bg-accent-purple text-white shadow-lg shadow-accent-purple/20'
              : 'bg-dark-light text-gray-400 hover:bg-dark-lighter'
          }`}
        >
          批量回测
        </button>
        <button
          onClick={() => { setViewMode('walkforward'); setDetailResult(null); }}
          className={`px-5 py-2 text-sm rounded-xl font-medium transition-all ${
            viewMode === 'walkforward'
              ? 'bg-accent-cyan text-dark shadow-lg shadow-accent-cyan/20'
              : 'bg-dark-light text-gray-400 hover:bg-dark-lighter'
          }`}
        >
          Walk-Forward
        </button>
      </div>

      {viewMode === 'single' ? (
        /* ════════ 单次回测 ════════ */
        <div className="grid grid-cols-1 lg:grid-cols-3 gap-4">
          {/* 左侧：历史记录 */}
          <div className="lg:col-span-1 space-y-3">
            <button
              onClick={() => { setShowForm(true); setResult(null); setSelectedId(null); }}
              className="w-full py-2.5 bg-primary hover:bg-primary/80 text-dark rounded-xl font-semibold transition-all text-sm"
            >
              + 新建回测
            </button>

            <CppHistoryList
              items={historyItems}
              selectedId={selectedId}
              checkedIds={checkedIds}
              onSelect={handleSelectHistory}
              onCheck={handleCheck}
              onDelete={handleDelete}
              onRefresh={loadHistory}
            />

            {checkedIds.size >= 2 && (
              <button
                onClick={() => setShowCompare(true)}
                className="w-full py-2.5 bg-accent-purple/20 text-accent-purple border border-accent-purple/30 rounded-xl font-semibold hover:bg-accent-purple/30 transition-all text-sm"
              >
                对比选中项 ({checkedIds.size})
              </button>
            )}
          </div>

          {/* 右侧：表单 + 结果 */}
          <div className="lg:col-span-2 space-y-4">
            {showForm && (
              <CppBacktestForm
                form={form}
                onChange={setForm}
                onSubmit={handleSubmit}
                loading={loading}
                error={error}
              />
            )}

            {result && <CppBacktestResult result={result} />}

            {!showForm && !result && (
              <Card className="p-6 flex items-center justify-center h-64">
                <div className="text-center text-gray-500">
                  <div className="text-4xl mb-4 opacity-30">&#x1F4CA;</div>
                  <div>选择历史记录查看结果，或新建回测</div>
                </div>
              </Card>
            )}
          </div>

          {/* 对比弹窗 */}
          {showCompare && (
            <BacktestComparison
              taskIds={Array.from(checkedIds)}
              onClose={() => setShowCompare(false)}
            />
          )}
        </div>
      ) : viewMode === 'walkforward' ? (
        /* ════════ Walk-Forward ════════ */
        <WalkForwardPanel />
      ) : (
        /* ════════ 批量回测 ════════ */
        <div className="space-y-4">
          {/* 配置表单 */}
          {!batchLoading && !batchDone && (
            <BatchBacktestForm
              onSubmit={handleBatchSubmit}
              loading={batchLoading}
              error={batchError}
            />
          )}

          {/* 进度条 */}
          {batchLoading && (
            <BatchProgressBar
              batchId={batchId}
              total={batchProgress.total}
              current={batchProgress.current}
              completed={batchProgress.completed}
              failed={batchProgress.failed}
              logs={batchLogs}
              onCancel={handleBatchCancel}
            />
          )}

          {/* 完成后：排行榜 + 热力图 */}
          {batchDone && (
            <div className="space-y-4">
              <div className="flex items-center justify-between">
                <div className="text-sm text-gray-400">
                  批量回测完成：{batchProgress.completed} 成功, {batchProgress.failed} 失败
                </div>
                <button
                  onClick={() => { setBatchDone(false); setBatchRanking([]); setBatchLogs([]); setDetailResult(null); }}
                  className="text-xs px-4 py-1.5 bg-accent-purple/20 text-accent-purple border border-accent-purple/30 rounded-lg hover:bg-accent-purple/30 transition-all"
                >
                  新建批量回测
                </button>
              </div>

              {/* 参数热力图（仅 param_optimize 模式） */}
              {batchMode === 'param_optimize' && Object.keys(batchParamGrid).length > 0 && (
                <ParamHeatmap ranking={batchRanking} paramGrid={batchParamGrid} />
              )}

              {/* 排行榜 */}
              <BatchRanking ranking={batchRanking} onSelectTask={handleSelectBatchTask} />

              {/* 点击排行榜行 → 显示详细结果 */}
              {detailResult && <CppBacktestResult result={detailResult} />}
            </div>
          )}

          {batchError && !batchLoading && (
            <p className="text-red-400 text-sm bg-red-400/10 px-3 py-2 rounded">{batchError}</p>
          )}
        </div>
      )}
    </div>
  );
};

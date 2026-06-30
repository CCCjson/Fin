import React, { useState, useMemo, useEffect, useCallback, useRef } from 'react';
import { Card } from '../common/Card';
import { StockSymbolInput } from '../common/StockSymbolInput';
import { backtestService } from '../../services/backtestService';
import type { BatchBacktestConfig, BatchMode } from '../../types';

const STRATEGIES = [
  { value: 'MA_CROSS', label: '均线交叉' },
  { value: 'MOMENTUM', label: '动量策略' },
  { value: 'MACD', label: 'MACD' },
  { value: 'RSI', label: 'RSI' },
  { value: 'KDJ', label: 'KDJ' },
  { value: 'BOLLINGER', label: '布林带' },
];

// 每个策略的默认参数
const DEFAULT_PARAMS: Record<string, Record<string, number>> = {
  MA_CROSS: { fast_period: 5, slow_period: 20 },
  MOMENTUM: { lookback: 20, buy_threshold: 0.05, sell_threshold: -0.03 },
  MACD: { fast_period: 12, slow_period: 26, signal_period: 9 },
  RSI: { period: 14, oversold: 30, overbought: 70 },
  KDJ: { n: 9, m1: 3, m2: 3, oversold: 20, overbought: 80 },
  BOLLINGER: { period: 20, num_std: 2.0 },
};

// 参数优化时，每个策略可调参数
const OPTIMIZABLE_PARAMS: Record<string, { key: string; label: string; defaultMin: number; defaultMax: number; defaultStep: number }[]> = {
  MA_CROSS: [
    { key: 'fast_period', label: '快线周期', defaultMin: 3, defaultMax: 10, defaultStep: 1 },
    { key: 'slow_period', label: '慢线周期', defaultMin: 15, defaultMax: 30, defaultStep: 5 },
  ],
  MOMENTUM: [
    { key: 'lookback', label: '回看天数', defaultMin: 10, defaultMax: 30, defaultStep: 5 },
    { key: 'buy_threshold', label: '买入阈值', defaultMin: 0.03, defaultMax: 0.08, defaultStep: 0.01 },
  ],
  MACD: [
    { key: 'fast_period', label: '快线周期', defaultMin: 8, defaultMax: 15, defaultStep: 1 },
    { key: 'slow_period', label: '慢线周期', defaultMin: 20, defaultMax: 30, defaultStep: 2 },
  ],
  RSI: [
    { key: 'period', label: 'RSI周期', defaultMin: 10, defaultMax: 20, defaultStep: 2 },
    { key: 'oversold', label: '超卖线', defaultMin: 20, defaultMax: 35, defaultStep: 5 },
  ],
  KDJ: [
    { key: 'n', label: 'KDJ周期', defaultMin: 5, defaultMax: 15, defaultStep: 2 },
    { key: 'oversold', label: '超卖', defaultMin: 15, defaultMax: 25, defaultStep: 5 },
  ],
  BOLLINGER: [
    { key: 'period', label: '周期', defaultMin: 15, defaultMax: 30, defaultStep: 5 },
    { key: 'num_std', label: '标准差倍数', defaultMin: 1.5, defaultMax: 3.0, defaultStep: 0.5 },
  ],
};

// 预设股票池
const STOCK_POOLS = [
  { id: 'sse50', label: '上证50', count: 50 },
  { id: 'csi300', label: '沪深300', count: 300 },
  { id: 'csi500', label: '中证500', count: 500 },
];

interface Props {
  onSubmit: (config: BatchBacktestConfig) => void;
  loading: boolean;
  error: string;
}

export const BatchBacktestForm: React.FC<Props> = ({ onSubmit, loading, error }) => {
  const yesterday = new Date();
  yesterday.setDate(yesterday.getDate() - 1);

  const [mode, setMode] = useState<BatchMode>('multi_symbol');
  const [startDate, setStartDate] = useState('2010-01-01');
  const [endDate, setEndDate] = useState(yesterday.toISOString().slice(0, 10));
  const [initialCapital, setInitialCapital] = useState(100000);
  const [market, setMarket] = useState('a_share');

  // multi_symbol
  const [symbolsText, setSymbolsText] = useState('510300.SH, 600519.SH, 000858.SZ, 601318.SH, 300750.SZ, 000333.SZ, 600036.SH, 000001.SZ');
  const [strategy, setStrategy] = useState('MA_CROSS');
  const [params, setParams] = useState<Record<string, any>>(DEFAULT_PARAMS['MA_CROSS']);

  // 股票池加载状态
  const [poolLoading, setPoolLoading] = useState('');
  const [activePool, setActivePool] = useState('');
  const [poolError, setPoolError] = useState('');

  // 行业板块
  const [industries, setIndustries] = useState<{ name: string }[]>([]);
  const [industriesLoading, setIndustriesLoading] = useState(false);
  const [showIndustryDropdown, setShowIndustryDropdown] = useState(false);
  const [industryFilter, setIndustryFilter] = useState('');
  const industryDropdownRef = useRef<HTMLDivElement>(null);

  // multi_strategy
  const [singleSymbol, setSingleSymbol] = useState('000001.SZ');
  const [selectedStrategies, setSelectedStrategies] = useState<Set<string>>(new Set(['MA_CROSS', 'MACD']));

  // param_optimize
  const [optimizeSymbol, setOptimizeSymbol] = useState('000001.SZ');
  const [optimizeStrategy, setOptimizeStrategy] = useState('MA_CROSS');
  const [paramRanges, setParamRanges] = useState<Record<string, { min: number; max: number; step: number }>>(() => {
    const ops = OPTIMIZABLE_PARAMS['MA_CROSS'] || [];
    const ranges: Record<string, { min: number; max: number; step: number }> = {};
    for (const op of ops) {
      ranges[op.key] = { min: op.defaultMin, max: op.defaultMax, step: op.defaultStep };
    }
    return ranges;
  });

  // 切换策略时同步默认参数
  const handleStrategyChange = (s: string) => {
    setStrategy(s);
    setParams(DEFAULT_PARAMS[s] || {});
  };

  const handleOptimizeStrategyChange = (s: string) => {
    setOptimizeStrategy(s);
    const ops = OPTIMIZABLE_PARAMS[s] || [];
    const ranges: Record<string, { min: number; max: number; step: number }> = {};
    for (const op of ops) {
      ranges[op.key] = { min: op.defaultMin, max: op.defaultMax, step: op.defaultStep };
    }
    setParamRanges(ranges);
  };

  // ── 行业下拉 click-outside 关闭 ──
  useEffect(() => {
    const handleClickOutside = (e: MouseEvent) => {
      if (industryDropdownRef.current && !industryDropdownRef.current.contains(e.target as Node)) {
        setShowIndustryDropdown(false);
      }
    };
    document.addEventListener('mousedown', handleClickOutside);
    return () => document.removeEventListener('mousedown', handleClickOutside);
  }, []);

  // ── 股票池加载 ──

  const handleLoadPool = useCallback(async (poolId: string) => {
    setPoolLoading(poolId);
    setPoolError('');
    try {
      const data = await backtestService.getPoolStocks(poolId);
      const symbols = data.stocks.map((s: { symbol: string }) => s.symbol).join(', ');
      setSymbolsText(symbols);
      setActivePool(poolId);
    } catch (e: any) {
      console.error('加载股票池失败:', e);
      setPoolError('加载股票池失败: ' + (e.message || '未知错误'));
    } finally {
      setPoolLoading('');
    }
  }, []);

  const handleLoadIndustryList = useCallback(async () => {
    if (industries.length > 0) {
      setShowIndustryDropdown(prev => !prev);
      return;
    }
    setIndustriesLoading(true);
    setPoolError('');
    try {
      const data = await backtestService.getIndustryList();
      setIndustries(data.industries || []);
      setShowIndustryDropdown(true);
    } catch (e: any) {
      console.error('加载行业列表失败:', e);
      setPoolError('加载行业列表失败: ' + (e.message || '未知错误'));
    } finally {
      setIndustriesLoading(false);
    }
  }, [industries.length]);

  const handleSelectIndustry = useCallback(async (name: string) => {
    setShowIndustryDropdown(false);
    setPoolLoading('industry');
    setPoolError('');
    try {
      const data = await backtestService.getIndustryStocks(name);
      const symbols = data.stocks.map((s: { symbol: string }) => s.symbol).join(', ');
      setSymbolsText(symbols);
      setActivePool(`industry:${name}`);
    } catch (e: any) {
      console.error('加载行业股票失败:', e);
      setPoolError('加载行业股票失败: ' + (e.message || '未知错误'));
    } finally {
      setPoolLoading('');
    }
  }, []);

  // 过滤行业列表
  const filteredIndustries = useMemo(() => {
    if (!industryFilter) return industries;
    return industries.filter(i => i.name.includes(industryFilter));
  }, [industries, industryFilter]);

  // 计算参数优化组合数
  const optimizeCombinations = useMemo(() => {
    const ops = OPTIMIZABLE_PARAMS[optimizeStrategy] || [];
    let count = 1;
    for (const op of ops) {
      const range = paramRanges[op.key];
      if (range && range.step > 0) {
        const steps = Math.floor((range.max - range.min) / range.step) + 1;
        count *= Math.max(1, steps);
      }
    }
    return count;
  }, [optimizeStrategy, paramRanges]);

  // 多策略选择数
  const multiStrategyCount = selectedStrategies.size;

  // 多股票数
  const symbolsList = useMemo(() =>
    symbolsText.split(/[,，\s]+/).map(s => s.trim()).filter(Boolean),
    [symbolsText]
  );
  const multiSymbolCount = symbolsList.length;

  const taskCount = mode === 'multi_symbol' ? multiSymbolCount
    : mode === 'multi_strategy' ? multiStrategyCount
    : optimizeCombinations;

  const handleSubmit = () => {
    const config: BatchBacktestConfig = {
      mode,
      start_date: startDate,
      end_date: endDate,
      initial_capital: initialCapital,
      market,
    };

    if (mode === 'multi_symbol') {
      config.symbols = symbolsList;
      config.strategy = strategy;
      config.params = params;
    } else if (mode === 'multi_strategy') {
      config.symbol = singleSymbol;
      config.strategies = Array.from(selectedStrategies).map(s => ({
        strategy: s,
        params: DEFAULT_PARAMS[s] || {},
      }));
    } else if (mode === 'param_optimize') {
      config.symbol = optimizeSymbol;
      config.strategy = optimizeStrategy;
      config.params = DEFAULT_PARAMS[optimizeStrategy] || {};
      const grid: Record<string, number[]> = {};
      const ops = OPTIMIZABLE_PARAMS[optimizeStrategy] || [];
      for (const op of ops) {
        const range = paramRanges[op.key];
        if (range && range.step > 0) {
          const vals: number[] = [];
          for (let v = range.min; v <= range.max + 1e-9; v += range.step) {
            vals.push(Math.round(v * 1000) / 1000);
          }
          grid[op.key] = vals;
        }
      }
      config.param_grid = grid;
    }

    onSubmit(config);
  };

  const toggleStrategy = (s: string) => {
    const next = new Set(selectedStrategies);
    if (next.has(s)) next.delete(s);
    else next.add(s);
    setSelectedStrategies(next);
  };

  return (
    <Card className="p-3 md:p-6">
      <h2 className="text-base md:text-lg font-semibold text-white mb-1">批量回测</h2>
      <p className="text-gray-500 text-xs mb-4">
        多股票扫描 | 多策略对比 | 参数优化（网格搜索）
      </p>

      {/* 模式选择 */}
      <div className="flex gap-2 mb-4">
        {([
          { value: 'multi_symbol', label: '多股票扫描' },
          { value: 'multi_strategy', label: '多策略对比' },
          { value: 'param_optimize', label: '参数优化' },
        ] as { value: BatchMode; label: string }[]).map(m => (
          <button
            key={m.value}
            onClick={() => setMode(m.value)}
            className={`px-4 py-1.5 text-sm rounded-lg transition-all ${
              mode === m.value
                ? 'bg-primary text-dark'
                : 'bg-dark-light text-gray-400 hover:bg-dark-lighter'
            }`}
          >
            {m.label}
          </button>
        ))}
      </div>

      {/* 通用配置 */}
      <div className="grid grid-cols-2 md:grid-cols-4 gap-3 mb-4">
        <div>
          <label className="block text-xs text-gray-400 mb-1">市场</label>
          <select value={market} onChange={e => setMarket(e.target.value)} className="form-input">
            <option value="a_share">A股</option>
            <option value="us">美股</option>
            <option value="hk">港股</option>
          </select>
        </div>
        <div>
          <label className="block text-xs text-gray-400 mb-1">初始资金</label>
          <input type="number" value={initialCapital} min={1000}
            onChange={e => setInitialCapital(Math.max(1000, parseInt(e.target.value) || 100000))}
            className="form-input" />
        </div>
        <div>
          <label className="block text-xs text-gray-400 mb-1">开始日期</label>
          <input type="date" value={startDate} onChange={e => setStartDate(e.target.value)} className="form-input" />
        </div>
        <div>
          <label className="block text-xs text-gray-400 mb-1">结束日期</label>
          <input type="date" value={endDate} onChange={e => setEndDate(e.target.value)} className="form-input" />
        </div>
      </div>

      {/* 模式特定配置 */}
      {mode === 'multi_symbol' && (
        <div className="space-y-3">
          {/* 股票池选择器 */}
          <div>
            <label className="block text-xs text-gray-400 mb-1.5">选择股票池</label>
            <div className="flex flex-wrap gap-2">
              {STOCK_POOLS.map(pool => (
                <button
                  key={pool.id}
                  onClick={() => handleLoadPool(pool.id)}
                  disabled={!!poolLoading}
                  className={`px-3 py-1.5 text-xs rounded-lg border transition-all ${
                    activePool === pool.id
                      ? 'bg-primary/20 border-primary text-primary'
                      : 'bg-dark-light border-border text-gray-400 hover:border-gray-500 hover:text-gray-300'
                  } disabled:opacity-50`}
                >
                  {poolLoading === pool.id ? (
                    <span className="inline-flex items-center gap-1">
                      <span className="w-3 h-3 border-2 border-gray-400 border-t-transparent rounded-full animate-spin" />
                      加载中
                    </span>
                  ) : (
                    <>{pool.label} ({pool.count})</>
                  )}
                </button>
              ))}

              {/* 行业板块按钮 */}
              <div className="relative" ref={industryDropdownRef}>
                <button
                  onClick={handleLoadIndustryList}
                  disabled={!!poolLoading}
                  className={`px-3 py-1.5 text-xs rounded-lg border transition-all ${
                    activePool.startsWith('industry:')
                      ? 'bg-accent-purple/20 border-accent-purple text-accent-purple'
                      : 'bg-dark-light border-border text-gray-400 hover:border-gray-500 hover:text-gray-300'
                  } disabled:opacity-50`}
                >
                  {industriesLoading || poolLoading === 'industry' ? (
                    <span className="inline-flex items-center gap-1">
                      <span className="w-3 h-3 border-2 border-gray-400 border-t-transparent rounded-full animate-spin" />
                      加载中
                    </span>
                  ) : (
                    <>行业板块 {showIndustryDropdown ? '▲' : '▼'}</>
                  )}
                </button>

                {/* 行业下拉列表 */}
                {showIndustryDropdown && (
                  <div className="absolute z-50 top-full left-0 mt-1 w-64 max-h-72 bg-dark-card border border-border rounded-lg shadow-xl overflow-hidden">
                    <div className="p-2 border-b border-border">
                      <input
                        type="text"
                        value={industryFilter}
                        onChange={e => setIndustryFilter(e.target.value)}
                        placeholder="搜索行业..."
                        className="form-input text-xs w-full"
                        autoFocus
                      />
                    </div>
                    <div className="overflow-y-auto max-h-56">
                      {filteredIndustries.map(ind => (
                        <button
                          key={ind.name}
                          onClick={() => handleSelectIndustry(ind.name)}
                          className="w-full text-left px-3 py-1.5 text-xs text-gray-300 hover:bg-primary/10 hover:text-white transition-colors"
                        >
                          {ind.name}
                        </button>
                      ))}
                      {filteredIndustries.length === 0 && (
                        <div className="px-3 py-2 text-xs text-gray-500 text-center">
                          {industries.length === 0 ? '加载中...' : '无匹配行业'}
                        </div>
                      )}
                    </div>
                  </div>
                )}
              </div>
            </div>

            {activePool && (
              <div className="mt-1 text-[10px] text-gray-500">
                当前: {activePool.startsWith('industry:') ? `行业 - ${activePool.replace('industry:', '')}` : STOCK_POOLS.find(p => p.id === activePool)?.label || activePool}
              </div>
            )}

            {poolError && (
              <p className="text-red-400 text-xs mt-1.5 bg-red-400/10 px-2 py-1 rounded">{poolError}</p>
            )}
          </div>

          <div>
            <label className="block text-xs text-gray-400 mb-1">
              股票列表（逗号分隔，共 {multiSymbolCount} 只）
            </label>
            <textarea
              value={symbolsText}
              onChange={e => { setSymbolsText(e.target.value); setActivePool(''); }}
              placeholder="000001.SZ, 600519.SH, 000858.SZ"
              className="form-input w-full min-h-[5rem] max-h-64 resize-y text-xs"
            />
          </div>
          <div className="grid grid-cols-2 md:grid-cols-4 gap-3">
            <div>
              <label className="block text-xs text-gray-400 mb-1">策略</label>
              <select value={strategy} onChange={e => handleStrategyChange(e.target.value)} className="form-input">
                {STRATEGIES.map(s => <option key={s.value} value={s.value}>{s.label}</option>)}
              </select>
            </div>
            {Object.entries(params).map(([key, val]) => (
              <div key={key}>
                <label className="block text-xs text-gray-400 mb-1">{key}</label>
                <input type="number" step="any" value={val}
                  onChange={e => setParams({ ...params, [key]: parseFloat(e.target.value) || 0 })}
                  className="form-input" />
              </div>
            ))}
          </div>
        </div>
      )}

      {mode === 'multi_strategy' && (
        <div className="space-y-3">
          <div className="max-w-xs">
            <label className="block text-xs text-gray-400 mb-1">股票代码</label>
            <StockSymbolInput value={singleSymbol} onChange={setSingleSymbol} placeholder="000001.SZ" />
          </div>
          <div>
            <label className="block text-xs text-gray-400 mb-1">选择策略（多选）</label>
            <div className="flex flex-wrap gap-2">
              {STRATEGIES.map(s => (
                <button
                  key={s.value}
                  onClick={() => toggleStrategy(s.value)}
                  className={`px-3 py-1.5 text-xs rounded-lg border transition-all ${
                    selectedStrategies.has(s.value)
                      ? 'bg-primary/20 border-primary text-primary'
                      : 'bg-dark-light border-border text-gray-400 hover:border-gray-500'
                  }`}
                >
                  {s.label}
                </button>
              ))}
            </div>
          </div>
        </div>
      )}

      {mode === 'param_optimize' && (
        <div className="space-y-3">
          <div className="grid grid-cols-2 gap-3">
            <div>
              <label className="block text-xs text-gray-400 mb-1">股票代码</label>
              <StockSymbolInput value={optimizeSymbol} onChange={setOptimizeSymbol} placeholder="000001.SZ" />
            </div>
            <div>
              <label className="block text-xs text-gray-400 mb-1">策略</label>
              <select value={optimizeStrategy} onChange={e => handleOptimizeStrategyChange(e.target.value)} className="form-input">
                {STRATEGIES.map(s => <option key={s.value} value={s.value}>{s.label}</option>)}
              </select>
            </div>
          </div>
          <div className="space-y-2">
            <label className="block text-xs text-gray-400">参数搜索范围</label>
            {(OPTIMIZABLE_PARAMS[optimizeStrategy] || []).map(op => {
              const range = paramRanges[op.key] || { min: op.defaultMin, max: op.defaultMax, step: op.defaultStep };
              return (
                <div key={op.key} className="grid grid-cols-4 gap-2 items-center">
                  <span className="text-xs text-gray-300">{op.label}</span>
                  <div>
                    <label className="block text-[10px] text-gray-500">最小</label>
                    <input type="number" step="any" value={range.min}
                      onChange={e => setParamRanges({ ...paramRanges, [op.key]: { ...range, min: parseFloat(e.target.value) || 0 } })}
                      className="form-input text-xs" />
                  </div>
                  <div>
                    <label className="block text-[10px] text-gray-500">最大</label>
                    <input type="number" step="any" value={range.max}
                      onChange={e => setParamRanges({ ...paramRanges, [op.key]: { ...range, max: parseFloat(e.target.value) || 0 } })}
                      className="form-input text-xs" />
                  </div>
                  <div>
                    <label className="block text-[10px] text-gray-500">步长</label>
                    <input type="number" step="any" value={range.step}
                      onChange={e => setParamRanges({ ...paramRanges, [op.key]: { ...range, step: parseFloat(e.target.value) || 1 } })}
                      className="form-input text-xs" />
                  </div>
                </div>
              );
            })}
          </div>
        </div>
      )}

      {/* 底部提示 + 提交 */}
      <div className="mt-4 flex items-center justify-between">
        <span className={`text-xs ${taskCount > 500 ? 'text-red-400' : 'text-gray-400'}`}>
          将执行 <strong className="text-white">{taskCount}</strong> 次回测
          {taskCount > 500 && ' (超过上限 500)'}
        </span>
      </div>

      <button
        onClick={handleSubmit}
        disabled={loading || taskCount === 0 || taskCount > 500}
        className="mt-3 w-full py-3 bg-accent-purple hover:bg-accent-purple/80 text-white rounded-xl font-semibold disabled:opacity-50 transition-all"
      >
        {loading ? '批量回测运行中...' : '启动批量回测'}
      </button>

      {error && <p className="text-red-400 text-sm mt-2 bg-red-400/10 px-3 py-2 rounded">{error}</p>}
    </Card>
  );
};

import React, { useState } from 'react';
import { Card } from '../common/Card';
import { StockSymbolInput } from '../common/StockSymbolInput';
import { ComboStrategyBuilder } from './ComboStrategyBuilder';
import type { SubStrategy } from './ComboStrategyBuilder';
import { PairsStrategyForm } from './PairsStrategyForm';

const STRATEGIES = [
  { value: 'MA_CROSS', label: '均线交叉' },
  { value: 'MOMENTUM', label: '动量策略' },
  { value: 'MACD', label: 'MACD' },
  { value: 'RSI', label: 'RSI' },
  { value: 'KDJ', label: 'KDJ' },
  { value: 'BOLLINGER', label: '布林带' },
  { value: 'COMBO', label: '组合策略' },
  { value: 'PAIRS', label: '统计套利' },
];

export interface CppFormState {
  symbol: string;
  strategy: string;
  start_date: string;
  end_date: string;
  initial_capital: number;
  market: string;
  // Slippage
  slippage_pct: number;  // -1 = use market default
  // Risk control
  risk_enabled: boolean;
  stop_loss_pct: number;
  trailing_stop: boolean;
  trailing_stop_pct: number;
  max_position_pct: number;
  // MA_CROSS
  fast_period: number;
  slow_period: number;
  // MOMENTUM
  lookback: number;
  buy_threshold: number;
  sell_threshold: number;
  // MACD
  macd_fast: number;
  macd_slow: number;
  signal_period: number;
  // RSI
  rsi_period: number;
  oversold: number;
  overbought: number;
  // KDJ
  kdj_n: number;
  kdj_m1: number;
  kdj_m2: number;
  kdj_oversold: number;
  kdj_overbought: number;
  // BOLLINGER
  boll_period: number;
  num_std: number;
  // COMBO
  subStrategies: SubStrategy[];
  combo_threshold: number;
  // PAIRS
  symbol2: string;
  pairs_lookback: number;
  entry_z: number;
  exit_z: number;
}

interface CppBacktestFormProps {
  form: CppFormState;
  onChange: (form: CppFormState) => void;
  onSubmit: () => void;
  loading: boolean;
  error: string;
}

export const CppBacktestForm: React.FC<CppBacktestFormProps> = ({
  form, onChange, onSubmit, loading, error,
}) => {
  const update = (field: string, value: any) => onChange({ ...form, [field]: value });

  const renderStrategyParams = () => {
    switch (form.strategy) {
      case 'MA_CROSS':
        return (
          <>
            <FormField label="快线周期">
              <input type="number" value={form.fast_period}
                onChange={e => update('fast_period', parseInt(e.target.value) || 5)}
                className="form-input" />
            </FormField>
            <FormField label="慢线周期">
              <input type="number" value={form.slow_period}
                onChange={e => update('slow_period', parseInt(e.target.value) || 20)}
                className="form-input" />
            </FormField>
          </>
        );
      case 'MOMENTUM':
        return (
          <>
            <FormField label="回看天数">
              <input type="number" value={form.lookback}
                onChange={e => update('lookback', parseInt(e.target.value) || 20)}
                className="form-input" />
            </FormField>
            <FormField label="买入阈值">
              <input type="number" step="0.01" value={form.buy_threshold}
                onChange={e => update('buy_threshold', parseFloat(e.target.value) || 0.05)}
                className="form-input" />
            </FormField>
            <FormField label="卖出阈值">
              <input type="number" step="0.01" value={form.sell_threshold}
                onChange={e => update('sell_threshold', parseFloat(e.target.value) || -0.03)}
                className="form-input" />
            </FormField>
          </>
        );
      case 'MACD':
        return (
          <>
            <FormField label="快线周期">
              <input type="number" value={form.macd_fast}
                onChange={e => update('macd_fast', parseInt(e.target.value) || 12)}
                className="form-input" />
            </FormField>
            <FormField label="慢线周期">
              <input type="number" value={form.macd_slow}
                onChange={e => update('macd_slow', parseInt(e.target.value) || 26)}
                className="form-input" />
            </FormField>
            <FormField label="信号线周期">
              <input type="number" value={form.signal_period}
                onChange={e => update('signal_period', parseInt(e.target.value) || 9)}
                className="form-input" />
            </FormField>
          </>
        );
      case 'RSI':
        return (
          <>
            <FormField label="RSI周期">
              <input type="number" value={form.rsi_period}
                onChange={e => update('rsi_period', parseInt(e.target.value) || 14)}
                className="form-input" />
            </FormField>
            <FormField label="超卖线">
              <input type="number" value={form.oversold}
                onChange={e => update('oversold', parseFloat(e.target.value) || 30)}
                className="form-input" />
            </FormField>
            <FormField label="超买线">
              <input type="number" value={form.overbought}
                onChange={e => update('overbought', parseFloat(e.target.value) || 70)}
                className="form-input" />
            </FormField>
          </>
        );
      case 'KDJ':
        return (
          <>
            <FormField label="KDJ周期(N)">
              <input type="number" value={form.kdj_n}
                onChange={e => update('kdj_n', parseInt(e.target.value) || 9)}
                className="form-input" />
            </FormField>
            <FormField label="M1平滑">
              <input type="number" value={form.kdj_m1}
                onChange={e => update('kdj_m1', parseInt(e.target.value) || 3)}
                className="form-input" />
            </FormField>
            <FormField label="M2平滑">
              <input type="number" value={form.kdj_m2}
                onChange={e => update('kdj_m2', parseInt(e.target.value) || 3)}
                className="form-input" />
            </FormField>
            <FormField label="超卖">
              <input type="number" value={form.kdj_oversold}
                onChange={e => update('kdj_oversold', parseFloat(e.target.value) || 20)}
                className="form-input" />
            </FormField>
            <FormField label="超买">
              <input type="number" value={form.kdj_overbought}
                onChange={e => update('kdj_overbought', parseFloat(e.target.value) || 80)}
                className="form-input" />
            </FormField>
          </>
        );
      case 'BOLLINGER':
        return (
          <>
            <FormField label="周期">
              <input type="number" value={form.boll_period}
                onChange={e => update('boll_period', parseInt(e.target.value) || 20)}
                className="form-input" />
            </FormField>
            <FormField label="标准差倍数">
              <input type="number" step="0.1" value={form.num_std}
                onChange={e => update('num_std', parseFloat(e.target.value) || 2.0)}
                className="form-input" />
            </FormField>
          </>
        );
      case 'COMBO':
        return (
          <div className="col-span-2 md:col-span-4">
            <ComboStrategyBuilder
              subStrategies={form.subStrategies}
              threshold={form.combo_threshold}
              onSubStrategiesChange={subs => update('subStrategies', subs)}
              onThresholdChange={t => update('combo_threshold', t)}
            />
          </div>
        );
      case 'PAIRS':
        return (
          <div className="col-span-2 md:col-span-4">
            <PairsStrategyForm
              symbol2={form.symbol2}
              lookback={form.pairs_lookback}
              entryZ={form.entry_z}
              exitZ={form.exit_z}
              onSymbol2Change={v => update('symbol2', v)}
              onLookbackChange={v => update('pairs_lookback', v)}
              onEntryZChange={v => update('entry_z', v)}
              onExitZChange={v => update('exit_z', v)}
            />
          </div>
        );
      default:
        return null;
    }
  };

  const [showAdvanced, setShowAdvanced] = useState(false);

  return (
    <Card className="p-3 md:p-6">
      <h2 className="text-base md:text-lg font-semibold text-white mb-1">配置回测</h2>
      <p className="text-gray-500 text-xs mb-4">
        C++ 高性能引擎 | 支持 {STRATEGIES.length} 种策略 | 结果自动保存
      </p>

      <div className="grid grid-cols-2 md:grid-cols-4 gap-3">
        <FormField label="股票代码">
          <StockSymbolInput
            value={form.symbol}
            onChange={v => update('symbol', v)}
            placeholder="000001.SZ"
          />
        </FormField>
        <FormField label="策略">
          <select value={form.strategy} onChange={e => update('strategy', e.target.value)}
            className="form-input">
            {STRATEGIES.map(s => (
              <option key={s.value} value={s.value}>{s.label}</option>
            ))}
          </select>
        </FormField>
        <FormField label="市场">
          <select value={form.market} onChange={e => update('market', e.target.value)}
            className="form-input">
            <option value="a_share">A股</option>
            <option value="us_stock">美股</option>
            <option value="hk_stock">港股</option>
          </select>
        </FormField>
        <FormField label="初始资金">
          <input type="number" value={form.initial_capital} min={1000}
            onChange={e => update('initial_capital', Math.max(1000, parseInt(e.target.value) || 100000))}
            className="form-input" />
        </FormField>
        <FormField label="开始日期">
          <input type="date" value={form.start_date}
            onChange={e => update('start_date', e.target.value)}
            className="form-input" />
        </FormField>
        <FormField label="结束日期">
          <input type="date" value={form.end_date}
            onChange={e => update('end_date', e.target.value)}
            className="form-input" />
        </FormField>

        {renderStrategyParams()}
      </div>

      {/* Advanced: Slippage & Risk Control */}
      <button
        type="button"
        onClick={() => setShowAdvanced(!showAdvanced)}
        className="mt-3 flex items-center gap-1 text-xs text-gray-400 hover:text-gray-200 transition-colors"
      >
        <span className={`transform transition-transform ${showAdvanced ? 'rotate-90' : ''}`}>&#9654;</span>
        滑点 & 风控设置
        {(form.risk_enabled || form.slippage_pct >= 0) && (
          <span className="ml-1 px-1.5 py-0.5 bg-accent-orange/20 text-accent-orange rounded text-[10px]">已配置</span>
        )}
      </button>

      {showAdvanced && (
        <div className="mt-2 p-3 bg-dark-light/50 rounded-lg border border-border/50 space-y-3">
          {/* Slippage */}
          <div className="grid grid-cols-2 md:grid-cols-4 gap-3">
            <FormField label="滑点(%)">
              <div className="flex items-center gap-2">
                <input
                  type="number" step="0.01" min={0}
                  value={form.slippage_pct < 0 ? '' : (form.slippage_pct * 100).toFixed(2)}
                  placeholder="市场默认"
                  onChange={e => {
                    const v = e.target.value;
                    update('slippage_pct', v === '' ? -1 : parseFloat(v) / 100);
                  }}
                  className="form-input"
                />
              </div>
            </FormField>
          </div>

          {/* Risk Control */}
          <div className="flex items-center gap-2">
            <input
              type="checkbox"
              checked={form.risk_enabled}
              onChange={e => update('risk_enabled', e.target.checked)}
              className="w-4 h-4 rounded border-border bg-dark-light text-primary focus:ring-primary/20"
            />
            <span className="text-xs text-gray-300">启用风控（止损 & 仓位限制）</span>
          </div>

          {form.risk_enabled && (
            <div className="grid grid-cols-2 md:grid-cols-4 gap-3">
              <FormField label="固定止损(%)">
                <input type="number" step="0.5" min={0.5} max={50}
                  value={(form.stop_loss_pct * 100).toFixed(1)}
                  onChange={e => update('stop_loss_pct', (parseFloat(e.target.value) || 5) / 100)}
                  className="form-input" />
              </FormField>
              <FormField label="单股最大仓位(%)">
                <input type="number" step="5" min={5} max={100}
                  value={Math.round(form.max_position_pct * 100)}
                  onChange={e => update('max_position_pct', (parseInt(e.target.value) || 100) / 100)}
                  className="form-input" />
              </FormField>
              <div className="flex flex-col justify-end">
                <div className="flex items-center gap-2 mb-1">
                  <input
                    type="checkbox"
                    checked={form.trailing_stop}
                    onChange={e => update('trailing_stop', e.target.checked)}
                    className="w-4 h-4 rounded border-border bg-dark-light text-primary focus:ring-primary/20"
                  />
                  <span className="text-xs text-gray-300">追踪止损</span>
                </div>
              </div>
              {form.trailing_stop && (
                <FormField label="追踪回撤(%)">
                  <input type="number" step="0.5" min={1} max={50}
                    value={(form.trailing_stop_pct * 100).toFixed(1)}
                    onChange={e => update('trailing_stop_pct', (parseFloat(e.target.value) || 8) / 100)}
                    className="form-input" />
                </FormField>
              )}
            </div>
          )}
        </div>
      )}

      <button onClick={onSubmit} disabled={loading}
        className="mt-4 w-full py-3 bg-primary hover:bg-primary/80 text-dark rounded-xl font-semibold disabled:opacity-50 transition-all">
        {loading ? '回测运行中...' : '启动 C++ 回测'}
      </button>

      {error && <p className="text-red-400 text-sm mt-2 bg-red-400/10 px-3 py-2 rounded">{error}</p>}
    </Card>
  );
};

const FormField: React.FC<{ label: string; children: React.ReactNode }> = ({ label, children }) => (
  <div>
    <label className="block text-xs text-gray-400 mb-1">{label}</label>
    {children}
  </div>
);

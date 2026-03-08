import React from 'react';

const AVAILABLE_STRATEGIES = [
  { value: 'MA_CROSS', label: '均线交叉' },
  { value: 'MOMENTUM', label: '动量策略' },
  { value: 'MACD', label: 'MACD' },
  { value: 'RSI', label: 'RSI' },
  { value: 'KDJ', label: 'KDJ' },
  { value: 'BOLLINGER', label: '布林带' },
];

export interface SubStrategy {
  name: string;
  weight: number;
  params: Record<string, any>;
}

interface ComboStrategyBuilderProps {
  subStrategies: SubStrategy[];
  threshold: number;
  onSubStrategiesChange: (subs: SubStrategy[]) => void;
  onThresholdChange: (t: number) => void;
}

export const ComboStrategyBuilder: React.FC<ComboStrategyBuilderProps> = ({
  subStrategies, threshold, onSubStrategiesChange, onThresholdChange,
}) => {
  const totalWeight = subStrategies.reduce((s, sub) => s + sub.weight, 0);

  const addSub = () => {
    onSubStrategiesChange([...subStrategies, { name: 'MA_CROSS', weight: 1, params: {} }]);
  };

  const removeSub = (idx: number) => {
    onSubStrategiesChange(subStrategies.filter((_, i) => i !== idx));
  };

  const updateSub = (idx: number, field: string, value: any) => {
    const updated = subStrategies.map((sub, i) =>
      i === idx ? { ...sub, [field]: value } : sub
    );
    onSubStrategiesChange(updated);
  };

  return (
    <div className="space-y-3 p-3 bg-dark-light/50 rounded-xl border border-border">
      <div className="flex justify-between items-center">
        <h4 className="text-sm font-medium text-white">子策略配置</h4>
        <button onClick={addSub}
          className="px-3 py-1 text-xs bg-primary/20 text-primary-light rounded-lg hover:bg-primary/30 transition-colors">
          + 添加子策略
        </button>
      </div>

      {subStrategies.map((sub, idx) => (
        <div key={idx} className="flex items-center gap-2 p-2 bg-dark-card rounded-lg border border-border">
          <select
            value={sub.name}
            onChange={e => updateSub(idx, 'name', e.target.value)}
            className="flex-1 p-1.5 text-sm bg-dark-light text-white rounded border border-border"
          >
            {AVAILABLE_STRATEGIES.map(s => (
              <option key={s.value} value={s.value}>{s.label}</option>
            ))}
          </select>

          <div className="flex items-center gap-1">
            <input
              type="range"
              min="0" max="100" step="5"
              value={sub.weight * 100}
              onChange={e => updateSub(idx, 'weight', parseInt(e.target.value) / 100)}
              className="w-20 accent-primary"
            />
            <span className="text-xs text-gray-400 w-10 text-right">
              {(sub.weight * 100).toFixed(0)}%
            </span>
          </div>

          <button onClick={() => removeSub(idx)}
            className="text-gray-500 hover:text-bear transition-colors text-sm px-1">
            x
          </button>
        </div>
      ))}

      {subStrategies.length > 0 && (
        <div className="flex items-center justify-between text-xs">
          <span className="text-gray-400">
            权重总和: <span className={totalWeight > 0 ? 'text-white' : 'text-bear'}>{(totalWeight * 100).toFixed(0)}%</span>
            {Math.abs(totalWeight - 1) > 0.01 && <span className="text-accent-orange ml-1">(自动归一化)</span>}
          </span>
        </div>
      )}

      <div className="flex items-center gap-3">
        <span className="text-xs text-gray-400">信号阈值:</span>
        <input
          type="range"
          min="10" max="90" step="5"
          value={threshold * 100}
          onChange={e => onThresholdChange(parseInt(e.target.value) / 100)}
          className="flex-1 accent-primary"
        />
        <span className="text-xs text-white w-8">{threshold.toFixed(2)}</span>
      </div>
    </div>
  );
};

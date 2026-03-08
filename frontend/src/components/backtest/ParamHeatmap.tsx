import React, { useState, useMemo } from 'react';
import type { BatchRankingItem } from '../../types';

type MetricKey = 'sharpe_ratio' | 'total_return_pct' | 'max_drawdown_pct';

interface Props {
  ranking: BatchRankingItem[];
  paramGrid: Record<string, number[]>;
}

export const ParamHeatmap: React.FC<Props> = ({ ranking, paramGrid }) => {
  const [metricKey, setMetricKey] = useState<MetricKey>('sharpe_ratio');

  const paramKeys = Object.keys(paramGrid);

  // 1 个参数 → 柱状图
  // 2 个参数 → 热力图
  // 3+ 个参数 → 退化为提示

  if (paramKeys.length === 0 || ranking.length === 0) {
    return null;
  }

  const metricLabel: Record<MetricKey, string> = {
    sharpe_ratio: '夏普比率',
    total_return_pct: '收益率 %',
    max_drawdown_pct: '最大回撤 %',
  };

  if (paramKeys.length === 1) {
    return <SingleParamChart ranking={ranking} paramKey={paramKeys[0]} metricKey={metricKey} metricLabel={metricLabel} onMetricChange={setMetricKey} />;
  }

  if (paramKeys.length === 2) {
    return <TwoParamHeatmap ranking={ranking} paramGrid={paramGrid} paramKeys={paramKeys} metricKey={metricKey} metricLabel={metricLabel} onMetricChange={setMetricKey} />;
  }

  // 3+ 参数
  return (
    <div className="bg-gradient-card border border-border shadow-card p-4 rounded-xl text-center text-gray-500 text-sm">
      参数维度 &gt; 2，热力图不适用。请参考上方排行榜查看结果。
    </div>
  );
};

// 单参数柱状图
const SingleParamChart: React.FC<{
  ranking: BatchRankingItem[];
  paramKey: string;
  metricKey: MetricKey;
  metricLabel: Record<MetricKey, string>;
  onMetricChange: (k: MetricKey) => void;
}> = ({ ranking, paramKey, metricKey, metricLabel, onMetricChange }) => {
  const data = useMemo(() => {
    return ranking.map(item => ({
      paramValue: item.params[paramKey] ?? '?',
      value: item[metricKey] ?? 0,
    })).sort((a, b) => Number(a.paramValue) - Number(b.paramValue));
  }, [ranking, paramKey, metricKey]);

  const maxAbs = Math.max(...data.map(d => Math.abs(d.value)), 0.01);

  return (
    <div className="bg-gradient-card border border-border shadow-card p-4 rounded-xl">
      <div className="flex items-center justify-between mb-3">
        <h3 className="text-sm font-semibold text-white">参数 vs 指标</h3>
        <MetricSelector value={metricKey} onChange={onMetricChange} labels={metricLabel} />
      </div>
      <div className="flex items-end gap-1 h-32">
        {data.map((d, i) => {
          const h = (Math.abs(d.value) / maxAbs) * 100;
          const isPositive = metricKey === 'max_drawdown_pct' ? d.value <= 0 : d.value >= 0;
          return (
            <div key={i} className="flex flex-col items-center flex-1 min-w-0">
              <span className="text-[10px] text-gray-400 mb-1">{d.value.toFixed(2)}</span>
              <div
                className={`w-full rounded-t transition-all ${isPositive ? 'bg-primary' : 'bg-bull/70'}`}
                style={{ height: `${Math.max(h, 2)}%` }}
                title={`${paramKey}=${d.paramValue}: ${metricLabel[metricKey]}=${d.value.toFixed(4)}`}
              />
              <span className="text-[10px] text-gray-500 mt-1 truncate w-full text-center">{d.paramValue}</span>
            </div>
          );
        })}
      </div>
      <div className="text-center text-[10px] text-gray-500 mt-1">{paramKey}</div>
    </div>
  );
};

// 双参数热力图
const TwoParamHeatmap: React.FC<{
  ranking: BatchRankingItem[];
  paramGrid: Record<string, number[]>;
  paramKeys: string[];
  metricKey: MetricKey;
  metricLabel: Record<MetricKey, string>;
  onMetricChange: (k: MetricKey) => void;
}> = ({ ranking, paramGrid, paramKeys, metricKey, metricLabel, onMetricChange }) => {
  const xKey = paramKeys[0];
  const yKey = paramKeys[1];
  const xVals = [...new Set(paramGrid[xKey])].sort((a, b) => a - b);
  const yVals = [...new Set(paramGrid[yKey])].sort((a, b) => a - b);

  // 构建查找表
  const lookup = useMemo(() => {
    const m = new Map<string, number>();
    for (const item of ranking) {
      const xv = item.params[xKey];
      const yv = item.params[yKey];
      const key = `${xv}_${yv}`;
      m.set(key, item[metricKey] ?? 0);
    }
    return m;
  }, [ranking, xKey, yKey, metricKey]);

  const allValues = Array.from(lookup.values());
  const minVal = Math.min(...allValues);
  const maxVal = Math.max(...allValues);
  const range = maxVal - minVal || 1;

  const getColor = (val: number) => {
    const ratio = (val - minVal) / range;
    // 蓝 → 紫 → 红（低 → 高）
    if (metricKey === 'max_drawdown_pct') {
      // 回撤：小（更绿）→大（更红）
      const r = Math.round(220 * ratio);
      const g = Math.round(180 * (1 - ratio));
      return `rgb(${r}, ${g}, 80)`;
    }
    // 收益/夏普：低（蓝）→高（绿）
    const r = Math.round(60 + 30 * (1 - ratio));
    const g = Math.round(80 + 140 * ratio);
    const b = Math.round(200 * (1 - ratio));
    return `rgb(${r}, ${g}, ${b})`;
  };

  return (
    <div className="bg-gradient-card border border-border shadow-card p-4 rounded-xl">
      <div className="flex items-center justify-between mb-3">
        <h3 className="text-sm font-semibold text-white">参数热力图</h3>
        <MetricSelector value={metricKey} onChange={onMetricChange} labels={metricLabel} />
      </div>

      <div className="overflow-x-auto">
        <div className="inline-block">
          {/* Y 轴标签 + 网格 */}
          <div className="flex">
            <div className="w-12" /> {/* 左上角空白 */}
            {xVals.map(xv => (
              <div key={xv} className="w-14 text-center text-[10px] text-gray-400 pb-1">{xv}</div>
            ))}
          </div>
          {yVals.map(yv => (
            <div key={yv} className="flex items-center">
              <div className="w-12 text-right text-[10px] text-gray-400 pr-2">{yv}</div>
              {xVals.map(xv => {
                const key = `${xv}_${yv}`;
                const val = lookup.get(key);
                return (
                  <div
                    key={key}
                    className="w-14 h-10 flex items-center justify-center text-[10px] font-mono text-white border border-dark/30 rounded-sm"
                    style={{ backgroundColor: val !== undefined ? getColor(val) : '#1A1F37' }}
                    title={`${xKey}=${xv}, ${yKey}=${yv}: ${val?.toFixed(4) ?? 'N/A'}`}
                  >
                    {val !== undefined ? val.toFixed(2) : '-'}
                  </div>
                );
              })}
            </div>
          ))}
          <div className="flex mt-1">
            <div className="w-12" />
            <div className="text-[10px] text-gray-500 text-center flex-1">{xKey}</div>
          </div>
        </div>
      </div>
      <div className="text-[10px] text-gray-500 mt-1 flex items-center justify-between">
        <span>Y: {yKey}</span>
        <span>
          {metricLabel[metricKey]}: {minVal.toFixed(2)} ~ {maxVal.toFixed(2)}
        </span>
      </div>
    </div>
  );
};

// 指标选择下拉
const MetricSelector: React.FC<{
  value: MetricKey;
  onChange: (k: MetricKey) => void;
  labels: Record<MetricKey, string>;
}> = ({ value, onChange, labels }) => (
  <select
    value={value}
    onChange={e => onChange(e.target.value as MetricKey)}
    className="text-xs bg-dark-light border border-border rounded px-2 py-1 text-gray-300"
  >
    {(Object.entries(labels) as [MetricKey, string][]).map(([k, l]) => (
      <option key={k} value={k}>{l}</option>
    ))}
  </select>
);

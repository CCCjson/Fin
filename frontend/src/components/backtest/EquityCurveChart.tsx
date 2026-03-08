import React from 'react';
import {
  ComposedChart, Area, XAxis, YAxis, CartesianGrid, Tooltip,
  ResponsiveContainer, Legend,
} from 'recharts';

interface EquityPoint {
  date: string;
  total_value: number;
  cash?: number;
  market_value?: number;
  daily_return?: number;
}

interface BenchmarkPoint {
  date: string;
  benchmark_value: number;
}

interface BenchmarkData {
  benchmark_name: string;
  benchmark_symbol: string;
  benchmark_return_pct: number;
  benchmark_curve: BenchmarkPoint[];
}

interface ComparisonCurve {
  name: string;
  color: string;
  data: EquityPoint[];
}

interface EquityCurveChartProps {
  data?: EquityPoint[];
  comparisons?: ComparisonCurve[];
  benchmark?: BenchmarkData;
}

const COLORS = ['#10B981', '#3B82F6', '#EF4444', '#F59E0B', '#8B5CF6'];

export const EquityCurveChart: React.FC<EquityCurveChartProps> = ({ data, comparisons, benchmark }) => {
  // 对比模式
  if (comparisons && comparisons.length > 0) {
    // 合并所有数据到同一时间轴
    const dateMap = new Map<string, Record<string, number>>();
    comparisons.forEach((c, idx) => {
      c.data.forEach(d => {
        if (!dateMap.has(d.date)) dateMap.set(d.date, {});
        dateMap.get(d.date)![`value_${idx}`] = d.total_value;
      });
    });
    const merged = Array.from(dateMap.entries())
      .sort(([a], [b]) => a.localeCompare(b))
      .map(([date, values]) => ({ date, ...values }));

    return (
      <div className="bg-gradient-card border border-border shadow-card p-3 md:p-6 rounded-xl">
        <h3 className="text-lg font-semibold text-white mb-4">资金曲线对比</h3>
        <ResponsiveContainer width="100%" height={400}>
          <ComposedChart data={merged} margin={{ top: 5, right: 30, left: 20, bottom: 5 }}>
            <CartesianGrid strokeDasharray="3 3" stroke="#374151" />
            <XAxis dataKey="date" stroke="#9ca3af" tick={{ fontSize: 11 }}
              tickFormatter={(v: string) => v?.slice(5) || ''} />
            <YAxis stroke="#9ca3af"
              tickFormatter={(v: number) => v >= 1000 ? `${(v / 1000).toFixed(0)}k` : String(v)} />
            <Tooltip
              contentStyle={{ backgroundColor: '#1e1e1e', border: '1px solid #374151', borderRadius: 8 }}
              formatter={(v, name) => {
                const idx = parseInt(String(name).split('_')[1]) || 0;
                return [`${Number(v).toLocaleString('zh-CN', { maximumFractionDigits: 0 })}`, comparisons[idx]?.name || String(name)];
              }}
            />
            <Legend formatter={(value: string) => {
              const idx = parseInt(value.split('_')[1]) || 0;
              return comparisons[idx]?.name || value;
            }} />
            {comparisons.map((c, idx) => (
              <Area
                key={idx}
                type="monotone"
                dataKey={`value_${idx}`}
                stroke={c.color || COLORS[idx % COLORS.length]}
                strokeWidth={2}
                fill="none"
                dot={false}
              />
            ))}
          </ComposedChart>
        </ResponsiveContainer>
      </div>
    );
  }

  // 单条曲线模式（带回撤 + 可选基准线）
  if (!data || data.length === 0) {
    return (
      <div className="bg-gradient-card border border-border shadow-card p-3 md:p-6 rounded-xl">
        <h3 className="text-lg font-semibold text-white mb-4">资产曲线</h3>
        <div className="text-center text-gray-500 py-8">暂无数据</div>
      </div>
    );
  }

  // 合并基准数据到 chartData
  const benchmarkMap = new Map<string, number>();
  if (benchmark?.benchmark_curve) {
    benchmark.benchmark_curve.forEach(b => {
      benchmarkMap.set(b.date, b.benchmark_value);
    });
  }

  // 计算回撤
  let peak = 0;
  const chartData = data.map(d => {
    peak = Math.max(peak, d.total_value);
    const drawdown = peak > 0 ? ((d.total_value - peak) / peak) * 100 : 0;
    return {
      ...d,
      drawdown,
      benchmark_value: benchmarkMap.get(d.date) ?? undefined,
    };
  });

  const hasBenchmark = benchmark && chartData.some(d => d.benchmark_value != null);

  return (
    <div className="bg-gradient-card border border-border shadow-card p-3 md:p-6 rounded-xl">
      <h3 className="text-lg font-semibold text-white mb-4">资产曲线</h3>
      <ResponsiveContainer width="100%" height={350}>
        <ComposedChart data={chartData} margin={{ top: 5, right: 30, left: 20, bottom: 5 }}>
          <defs>
            <linearGradient id="equityFill" x1="0" y1="0" x2="0" y2="1">
              <stop offset="5%" stopColor="#10B981" stopOpacity={0.3} />
              <stop offset="95%" stopColor="#10B981" stopOpacity={0} />
            </linearGradient>
            <linearGradient id="drawdownFill" x1="0" y1="0" x2="0" y2="1">
              <stop offset="5%" stopColor="#EF4444" stopOpacity={0} />
              <stop offset="95%" stopColor="#EF4444" stopOpacity={0.2} />
            </linearGradient>
          </defs>
          <CartesianGrid strokeDasharray="3 3" stroke="#374151" />
          <XAxis dataKey="date" stroke="#9ca3af" tick={{ fontSize: 11 }}
            tickFormatter={(v: string) => v?.slice(5) || ''} />
          <YAxis yAxisId="left" stroke="#9ca3af" domain={['dataMin', 'dataMax']}
            tickFormatter={(v: number) => v >= 1000 ? `${(v / 1000).toFixed(0)}k` : String(v)} />
          <YAxis yAxisId="right" orientation="right" stroke="#EF4444" opacity={0.5}
            tickFormatter={(v: number) => `${v.toFixed(0)}%`} />
          <Tooltip
            contentStyle={{ backgroundColor: '#1e1e1e', border: '1px solid #374151', borderRadius: 8 }}
            formatter={(v: number, name: string) => {
              if (name === 'drawdown') return [`${v.toFixed(2)}%`, '回撤'];
              if (name === 'benchmark_value') return [
                `${v.toLocaleString('zh-CN', { maximumFractionDigits: 0 })}`,
                benchmark?.benchmark_name || '基准',
              ];
              return [`${v.toLocaleString('zh-CN', { maximumFractionDigits: 0 })}`, '策略净值'];
            }}
          />
          {hasBenchmark && (
            <Legend
              formatter={(value: string) => {
                if (value === 'total_value') return '策略净值';
                if (value === 'benchmark_value') return benchmark?.benchmark_name || '基准';
                if (value === 'drawdown') return '回撤';
                return value;
              }}
            />
          )}
          <Area yAxisId="left" type="monotone" dataKey="total_value"
            name="total_value"
            stroke="#10B981" strokeWidth={2}
            fillOpacity={1} fill="url(#equityFill)" />
          {hasBenchmark && (
            <Area yAxisId="left" type="monotone" dataKey="benchmark_value"
              name="benchmark_value"
              stroke="#8B5CF6" strokeWidth={2} strokeDasharray="5 3"
              fill="none" dot={false} />
          )}
          <Area yAxisId="right" type="monotone" dataKey="drawdown"
            name="drawdown"
            stroke="#EF4444" strokeWidth={1}
            fillOpacity={1} fill="url(#drawdownFill)" strokeOpacity={0.5} />
        </ComposedChart>
      </ResponsiveContainer>
    </div>
  );
};

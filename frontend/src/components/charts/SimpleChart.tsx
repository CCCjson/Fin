import React from 'react';
import {
  LineChart,
  Line,
  XAxis,
  YAxis,
  CartesianGrid,
  Tooltip,
  ResponsiveContainer,
} from 'recharts';
import type { StockData } from '../../types';

interface SimpleChartProps {
  data: StockData[];
  height?: number;
}

export const SimpleChart: React.FC<SimpleChartProps> = ({ data, height = 400 }) => {
  const chartData = data.map((item) => ({
    date: new Date(item.date).toLocaleDateString(),
    close: item.close,
    volume: item.volume,
  }));

  return (
    <ResponsiveContainer width="100%" height={height}>
      <LineChart data={chartData}>
        <CartesianGrid strokeDasharray="3 3" stroke="#374151" />
        <XAxis dataKey="date" stroke="#9ca3af" />
        <YAxis stroke="#9ca3af" />
        <Tooltip
          contentStyle={{
            backgroundColor: '#1e1e1e',
            border: '1px solid #374151',
            borderRadius: '4px',
          }}
        />
        <Line
          type="monotone"
          dataKey="close"
          stroke="#26a69a"
          strokeWidth={2}
          dot={false}
        />
      </LineChart>
    </ResponsiveContainer>
  );
};

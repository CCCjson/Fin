import React from 'react';

interface MetricCardProps {
  label: string;
  value: string;
  type?: 'return' | 'risk' | 'quality' | 'neutral';
  positive?: boolean;
}

const gradients: Record<string, string> = {
  return: 'from-emerald-500/10 to-emerald-500/0',
  'return-negative': 'from-red-500/10 to-red-500/0',
  risk: 'from-red-500/10 to-red-500/0',
  quality: 'from-primary/10 to-primary/0',
  neutral: 'from-gray-500/5 to-gray-500/0',
};

export const MetricCard: React.FC<MetricCardProps> = ({
  label, value, type = 'neutral', positive,
}) => {
  const isNeg = positive === false || (positive === undefined && value.startsWith('-'));
  const grad = type === 'return'
    ? (isNeg ? gradients['return-negative'] : gradients.return)
    : gradients[type] || gradients.neutral;

  const valueColor = type === 'return'
    ? (isNeg ? 'text-bear' : 'text-bull')
    : type === 'risk' ? 'text-bear'
    : type === 'quality' ? 'text-primary-light'
    : 'text-white';

  return (
    <div className={`relative overflow-hidden text-center p-3 md:p-4 rounded-xl border border-border bg-gradient-to-br ${grad} min-h-[80px] flex flex-col justify-center`}>
      <div className="absolute top-2 right-2 opacity-10 text-2xl">
        {type === 'return' ? (isNeg ? '\u{1F4C9}' : '\u{1F4C8}') : type === 'risk' ? '\u{1F6E1}' : type === 'quality' ? '\u{2B50}' : '\u{1F4CA}'}
      </div>
      <div className="text-gray-400 text-xs md:text-sm mb-1">{label}</div>
      <div className={`text-lg md:text-2xl font-bold ${valueColor} truncate`}>{value}</div>
    </div>
  );
};

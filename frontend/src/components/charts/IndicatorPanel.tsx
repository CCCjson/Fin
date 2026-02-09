import React from 'react';
import type { IndicatorConfig } from '../../types/chart';

interface IndicatorPanelProps {
  config: IndicatorConfig;
  onChange: (config: IndicatorConfig) => void;
}

export const IndicatorPanel: React.FC<IndicatorPanelProps> = ({ config, onChange }) => {
  const handleMAToggle = () => {
    onChange({
      ...config,
      ma: { ...config.ma, enabled: !config.ma.enabled },
    });
  };

  const handleMAPeriodToggle = (period: number) => {
    const periods = config.ma.periods.includes(period)
      ? config.ma.periods.filter((p) => p !== period)
      : [...config.ma.periods, period].sort((a, b) => a - b);

    onChange({
      ...config,
      ma: { ...config.ma, periods },
    });
  };

  const handleBollToggle = () => {
    onChange({
      ...config,
      boll: { ...config.boll, enabled: !config.boll.enabled },
    });
  };

  const handleMACDToggle = () => {
    onChange({
      ...config,
      macd: { ...config.macd, enabled: !config.macd.enabled },
    });
  };

  const handleRSIToggle = () => {
    onChange({
      ...config,
      rsi: { ...config.rsi, enabled: !config.rsi.enabled },
    });
  };

  return (
    <div className="flex flex-wrap items-center gap-4 p-3 bg-dark-light rounded-lg border border-border">
      {/* 均线设置 */}
      <div className="flex items-center gap-2">
        <label className="flex items-center gap-1 cursor-pointer">
          <input
            type="checkbox"
            checked={config.ma.enabled}
            onChange={handleMAToggle}
            className="w-4 h-4 rounded border-border bg-dark text-primary focus:ring-primary/20"
          />
          <span className="text-sm text-gray-300">均线</span>
        </label>

        {config.ma.enabled && (
          <div className="flex items-center gap-1 ml-2">
            {[5, 10, 20, 60].map((period) => (
              <button
                key={period}
                onClick={() => handleMAPeriodToggle(period)}
                className={`px-2 py-0.5 text-xs rounded transition ${
                  config.ma.periods.includes(period)
                    ? period === 5
                      ? 'bg-yellow-500/20 text-yellow-400 border border-yellow-500/50'
                      : period === 10
                      ? 'bg-cyan-500/20 text-cyan-400 border border-cyan-500/50'
                      : period === 20
                      ? 'bg-pink-500/20 text-pink-400 border border-pink-500/50'
                      : 'bg-purple-500/20 text-purple-400 border border-purple-500/50'
                    : 'bg-dark text-gray-500 border border-border hover:border-gray-500'
                }`}
              >
                MA{period}
              </button>
            ))}
          </div>
        )}
      </div>

      <div className="w-px h-6 bg-border" />

      {/* 布林带设置 */}
      <label className="flex items-center gap-1 cursor-pointer">
        <input
          type="checkbox"
          checked={config.boll.enabled}
          onChange={handleBollToggle}
          className="w-4 h-4 rounded border-border bg-dark text-primary focus:ring-primary/20"
        />
        <span className="text-sm text-gray-300">BOLL</span>
      </label>

      <div className="w-px h-6 bg-border" />

      {/* MACD 设置 */}
      <label className="flex items-center gap-1 cursor-pointer">
        <input
          type="checkbox"
          checked={config.macd.enabled}
          onChange={handleMACDToggle}
          className="w-4 h-4 rounded border-border bg-dark text-primary focus:ring-primary/20"
        />
        <span className="text-sm text-gray-300">MACD</span>
        {config.macd.enabled && (
          <span className="text-xs text-gray-500 ml-1">
            ({config.macd.fast},{config.macd.slow},{config.macd.signal})
          </span>
        )}
      </label>

      <div className="w-px h-6 bg-border" />

      {/* RSI 设置 */}
      <label className="flex items-center gap-1 cursor-pointer">
        <input
          type="checkbox"
          checked={config.rsi.enabled}
          onChange={handleRSIToggle}
          className="w-4 h-4 rounded border-border bg-dark text-primary focus:ring-primary/20"
        />
        <span className="text-sm text-gray-300">RSI</span>
        {config.rsi.enabled && (
          <span className="text-xs text-gray-500 ml-1">({config.rsi.period})</span>
        )}
      </label>
    </div>
  );
};

import React from 'react';
import { StockSymbolInput } from '../common/StockSymbolInput';

interface PairsStrategyFormProps {
  symbol2: string;
  lookback: number;
  entryZ: number;
  exitZ: number;
  onSymbol2Change: (v: string) => void;
  onLookbackChange: (v: number) => void;
  onEntryZChange: (v: number) => void;
  onExitZChange: (v: number) => void;
}

export const PairsStrategyForm: React.FC<PairsStrategyFormProps> = ({
  symbol2, lookback, entryZ, exitZ,
  onSymbol2Change, onLookbackChange, onEntryZChange, onExitZChange,
}) => {
  return (
    <div className="space-y-3 p-3 bg-dark-light/50 rounded-xl border border-border">
      <h4 className="text-sm font-medium text-white">配对交易参数</h4>
      <div className="grid grid-cols-2 gap-3">
        <div className="col-span-2">
          <label className="block text-xs text-gray-400 mb-1">配对股票代码</label>
          <StockSymbolInput
            value={symbol2}
            onChange={onSymbol2Change}
            placeholder="输入配对股票, 如 600036.SH"
          />
        </div>
        <div>
          <label className="block text-xs text-gray-400 mb-1">回看周期</label>
          <input type="number" value={lookback}
            onChange={e => onLookbackChange(parseInt(e.target.value) || 60)}
            className="w-full p-2 text-sm bg-dark-light text-white rounded-lg border border-border" />
        </div>
        <div>
          <label className="block text-xs text-gray-400 mb-1">入场 Z-Score</label>
          <input type="number" step="0.1" value={entryZ}
            onChange={e => onEntryZChange(parseFloat(e.target.value) || 2.0)}
            className="w-full p-2 text-sm bg-dark-light text-white rounded-lg border border-border" />
        </div>
        <div>
          <label className="block text-xs text-gray-400 mb-1">出场 Z-Score</label>
          <input type="number" step="0.1" value={exitZ}
            onChange={e => onExitZChange(parseFloat(e.target.value) || 0.5)}
            className="w-full p-2 text-sm bg-dark-light text-white rounded-lg border border-border" />
        </div>
      </div>
    </div>
  );
};

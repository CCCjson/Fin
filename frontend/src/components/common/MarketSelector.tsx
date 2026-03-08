import React from 'react';
import { create } from 'zustand';

/* ================================================================
   Market Store — 全局市场选择状态
   ================================================================ */

export type MarketId = 'a-stock' | 'hk-stock' | 'us-stock' | 'futures';

interface MarketInfo {
  id: MarketId;
  name: string;
  icon: string;
  available: boolean;
}

export const MARKETS: MarketInfo[] = [
  { id: 'a-stock',  name: 'A 股', icon: '\u{1F1E8}\u{1F1F3}', available: true },
  { id: 'hk-stock', name: '港 股', icon: '\u{1F1ED}\u{1F1F0}', available: false },
  { id: 'us-stock', name: '美 股', icon: '\u{1F1FA}\u{1F1F8}', available: false },
  { id: 'futures',  name: '期 货', icon: '\u{1F4C8}',           available: false },
];

interface MarketStore {
  market: MarketId;
  setMarket: (m: MarketId) => void;
}

export const useMarketStore = create<MarketStore>((set) => ({
  market: 'a-stock',
  setMarket: (market) => set({ market }),
}));

/* ================================================================
   MarketSelector 组件
   ================================================================ */

export const MarketSelector: React.FC = () => {
  const { market, setMarket } = useMarketStore();

  return (
    <div className="flex items-center gap-1">
      {MARKETS.map((m) => {
        const active = market === m.id;
        return (
          <button
            key={m.id}
            onClick={() => m.available && setMarket(m.id)}
            disabled={!m.available}
            className={`flex items-center gap-1.5 px-2.5 py-1 rounded-lg text-xs font-medium transition-all ${
              active
                ? 'bg-primary/15 text-primary-light border border-primary/30'
                : m.available
                  ? 'text-gray-400 hover:text-white hover:bg-dark-light border border-transparent'
                  : 'text-gray-600 cursor-not-allowed border border-transparent opacity-50'
            }`}
          >
            <span className="text-sm">{m.icon}</span>
            <span>{m.name}</span>
            {!m.available && (
              <span className="text-[9px] text-gray-600">soon</span>
            )}
          </button>
        );
      })}
    </div>
  );
};

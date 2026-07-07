import React, { useState, useRef, useEffect, useCallback } from 'react';
import api from '../../services/api';
import { normalizeMarket, MARKET_LABEL } from '../../utils/marketDetect';

interface StockSearchResult {
  symbol: string;
  name: string;
  market: string;
  industry: string | null;
}

interface StockSymbolInputProps {
  value: string;
  onChange: (symbol: string, name?: string) => void;
  placeholder?: string;
  className?: string;
  disabled?: boolean;
}

export const StockSymbolInput: React.FC<StockSymbolInputProps> = ({
  value,
  onChange,
  placeholder = '输入代码或名称搜索',
  className = '',
  disabled = false,
}) => {
  const [query, setQuery] = useState(value);
  const [results, setResults] = useState<StockSearchResult[]>([]);
  const [open, setOpen] = useState(false);
  const [activeIndex, setActiveIndex] = useState(-1);
  const [loading, setLoading] = useState(false);
  const wrapperRef = useRef<HTMLDivElement>(null);
  const inputRef = useRef<HTMLInputElement>(null);
  const debounceRef = useRef<ReturnType<typeof setTimeout> | undefined>(undefined);

  // Sync external value changes
  useEffect(() => {
    setQuery(value);
  }, [value]);

  const doSearch = useCallback(async (q: string) => {
    if (q.length < 1) {
      setResults([]);
      setOpen(false);
      return;
    }
    setLoading(true);
    try {
      const data: StockSearchResult[] = await api.get('/data/stocks/search', { params: { q, limit: 10 } });
      setResults(data);
      setOpen(data.length > 0);
      setActiveIndex(-1);
    } catch {
      setResults([]);
    } finally {
      setLoading(false);
    }
  }, []);

  const handleInputChange = (e: React.ChangeEvent<HTMLInputElement>) => {
    const v = e.target.value;
    setQuery(v);
    onChange(v);

    if (debounceRef.current) clearTimeout(debounceRef.current);
    debounceRef.current = setTimeout(() => doSearch(v), 200);
  };

  const handleSelect = (item: StockSearchResult) => {
    setQuery(item.symbol);
    onChange(item.symbol, item.name);
    setOpen(false);
    setResults([]);
    inputRef.current?.blur();
  };

  const handleKeyDown = (e: React.KeyboardEvent) => {
    if (!open || results.length === 0) return;

    if (e.key === 'ArrowDown') {
      e.preventDefault();
      setActiveIndex(prev => (prev + 1) % results.length);
    } else if (e.key === 'ArrowUp') {
      e.preventDefault();
      setActiveIndex(prev => (prev <= 0 ? results.length - 1 : prev - 1));
    } else if (e.key === 'Enter' && activeIndex >= 0) {
      e.preventDefault();
      handleSelect(results[activeIndex]);
    } else if (e.key === 'Escape') {
      setOpen(false);
    }
  };

  // Close dropdown on outside click
  useEffect(() => {
    const handleClickOutside = (e: MouseEvent) => {
      if (wrapperRef.current && !wrapperRef.current.contains(e.target as Node)) {
        setOpen(false);
      }
    };
    document.addEventListener('mousedown', handleClickOutside);
    return () => document.removeEventListener('mousedown', handleClickOutside);
  }, []);

  // Cleanup debounce
  useEffect(() => {
    return () => {
      if (debounceRef.current) clearTimeout(debounceRef.current);
    };
  }, []);

  const baseInputClass = 'w-full px-3 py-2 bg-dark-light text-white rounded-lg border border-border focus:border-primary focus:ring-2 focus:ring-primary/20 outline-none transition-all';

  return (
    <div ref={wrapperRef} className="relative">
      <input
        ref={inputRef}
        type="text"
        value={query}
        onChange={handleInputChange}
        onKeyDown={handleKeyDown}
        onFocus={() => { if (results.length > 0) setOpen(true); }}
        placeholder={placeholder}
        disabled={disabled}
        className={className || baseInputClass}
      />
      {loading && (
        <div className="absolute right-3 top-1/2 -translate-y-1/2">
          <svg className="animate-spin h-4 w-4 text-gray-400" viewBox="0 0 24 24">
            <circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4" fill="none" />
            <path className="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4zm2 5.291A7.962 7.962 0 014 12H0c0 3.042 1.135 5.824 3 7.938l3-2.647z" />
          </svg>
        </div>
      )}

      {open && results.length > 0 && (
        <ul className="absolute z-50 mt-1 w-full max-h-60 overflow-y-auto bg-dark-card border border-border rounded-lg shadow-2xl">
          {results.map((item, idx) => (
            <li
              key={item.symbol}
              onMouseDown={() => handleSelect(item)}
              onMouseEnter={() => setActiveIndex(idx)}
              className={`px-3 py-2 cursor-pointer flex items-center justify-between transition-colors ${
                idx === activeIndex ? 'bg-primary/20 text-white' : 'text-gray-300 hover:bg-dark-light'
              }`}
            >
              <div className="flex items-center gap-2 min-w-0">
                <span className="font-mono text-sm text-primary-light">{item.symbol}</span>
                <span className="text-sm truncate">{item.name}</span>
                <span className="text-[9px] px-1 py-0.5 rounded bg-dark-light text-gray-400 flex-shrink-0">
                  {MARKET_LABEL[normalizeMarket(item.market)]}
                </span>
              </div>
              {item.industry && (
                <span className="text-xs text-gray-500 ml-2 flex-shrink-0">{item.industry}</span>
              )}
            </li>
          ))}
        </ul>
      )}
    </div>
  );
};

import { useEffect, useState } from 'react';
import api from '../services/api';

// 跨组件长期缓存：symbol→name 基本不变，避免重复请求
const _cache = new Map<string, string>();

/**
 * 按需批量查询 symbol→name，用于"记录表本身没存 name"的场景（如预测/训练记录）。
 */
export function useStockNames(symbols: string[]): Record<string, string> {
  const key = Array.from(new Set(symbols.filter(Boolean))).sort().join(',');
  const [names, setNames] = useState<Record<string, string>>({});

  useEffect(() => {
    if (!key) return;
    const missing = key.split(',').filter(s => !_cache.has(s));
    if (missing.length === 0) {
      setNames(Object.fromEntries(key.split(',').map(s => [s, _cache.get(s) || ''])));
      return;
    }
    api.get<Record<string, string>>('/data/stocks/names', { params: { symbols: missing.join(',') } })
      .then(res => {
        Object.entries(res || {}).forEach(([sym, name]) => _cache.set(sym, name as string));
        setNames(Object.fromEntries(key.split(',').map(s => [s, _cache.get(s) || ''])));
      })
      .catch(() => {
        setNames(Object.fromEntries(key.split(',').map(s => [s, _cache.get(s) || ''])));
      });
  }, [key]);

  return names;
}

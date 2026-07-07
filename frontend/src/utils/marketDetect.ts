/**
 * 从股票代码推断所属市场 + 展示货币。
 *
 * 与后端 data_engine/engine.py 的市场判定启发式保持一致（.SH/.SZ → a_share，
 * .HK → hk_stock，其余 → us_stock 兜底——美股代码本身不带后缀，如 AAPL）：
 * 后端 /data/daily 已经是市场无关的（按代码后缀内部路由到对应 fetcher/表），
 * 前端只需要在展示层知道「这是哪个市场」以便选对货币符号，不需要额外接口。
 */

export type MarketId = 'a_share' | 'hk_stock' | 'us_stock';

export const MARKET_LABEL: Record<MarketId, string> = {
  a_share: 'A股',
  hk_stock: '港股',
  us_stock: '美股',
};

export const MARKET_CURRENCY_SYMBOL: Record<MarketId, string> = {
  a_share: '¥',
  hk_stock: 'HK$',
  us_stock: 'US$',
};

export function detectMarket(symbol: string): MarketId {
  const s = (symbol || '').trim().toUpperCase();
  if (s.endsWith('.SH') || s.endsWith('.SZ') || s.endsWith('.BJ')) return 'a_share';
  if (s.endsWith('.HK')) return 'hk_stock';
  return 'us_stock';
}

export function currencySymbolFor(symbol: string): string {
  return MARKET_CURRENCY_SYMBOL[detectMarket(symbol)];
}

/** 把任意市场写法（A/HK/US、us/hk、a-stock、沪深京 等）归一到 canonical，未知兜底 a_share。
 *  与后端 common/market.py 的 normalize_market 口径一致。 */
const MARKET_ALIASES: Record<string, MarketId> = {
  a_share: 'a_share', 'a-stock': 'a_share', a: 'a_share', ashare: 'a_share', cn: 'a_share', '沪深京': 'a_share', '沪深': 'a_share',
  hk_stock: 'hk_stock', 'hk-stock': 'hk_stock', hk: 'hk_stock', hkstock: 'hk_stock', '港股': 'hk_stock',
  us_stock: 'us_stock', 'us-stock': 'us_stock', us: 'us_stock', usstock: 'us_stock', '美股': 'us_stock',
};

export function normalizeMarket(raw: string | null | undefined): MarketId {
  if (!raw) return 'a_share';
  return MARKET_ALIASES[String(raw).trim().toLowerCase()] ?? 'a_share';
}

/** 成交量按市场格式化：A股/港股用「万」，美股用裸股数（带千分位，避免「万」这种 A股口径）。 */
export function formatVolume(volume: number, symbol: string): string {
  const v = volume || 0;
  if (detectMarket(symbol) === 'us_stock') {
    if (v >= 1_000_000) return `${(v / 1_000_000).toFixed(2)}M`;
    if (v >= 1_000) return `${(v / 1_000).toFixed(1)}K`;
    return v.toLocaleString('en-US');
  }
  return `${(v / 10000).toFixed(2)}万`;
}

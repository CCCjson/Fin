/* 数据监控页各 section 共用的小工具与配色映射。
   收在一处，避免 7 个组件各写一遍 fmtTime / 健康度配色。 */
import type { AssetHealth } from '../../../services/dataMonitorService';

export const MARKET_META: Record<string, { label: string; icon: string }> = {
  a_share: { label: 'A股', icon: '🇨🇳' },
  hk_stock: { label: '港股', icon: '🇭🇰' },
  us_stock: { label: '美股', icon: '🇺🇸' },
  crypto: { label: 'Crypto', icon: '🪙' },
};

export const MARKET_ORDER = ['a_share', 'hk_stock', 'us_stock', 'crypto'];

export const GROUP_META: Record<string, { label: string; icon: string }> = {
  calendar: { label: '交易日历', icon: '📅' },
  quote: { label: '行情', icon: '📈' },
  fundamental: { label: '基本面', icon: '📊' },
  sentiment: { label: '情绪', icon: '📰' },
  derived: { label: '衍生', icon: '🧮' },
};

export const GROUP_ORDER = ['calendar', 'quote', 'fundamental', 'sentiment', 'derived'];

/** A 股配色：bear=绿(正常)，bull=红(告警) —— 与全站一致，别反过来 */
export const HEALTH_STYLE: Record<AssetHealth, string> = {
  ok: 'bg-bear/15 text-bear border-bear/30',
  warn: 'bg-yellow-400/15 text-yellow-400 border-yellow-400/30',
  stale: 'bg-bull/15 text-bull border-bull/30',
  unknown: 'bg-gray-500/15 text-gray-400 border-gray-500/30',
};

export const HEALTH_DOT: Record<AssetHealth, string> = {
  ok: 'bg-bear',
  warn: 'bg-yellow-400',
  stale: 'bg-bull',
  unknown: 'bg-gray-500',
};

export const HEALTH_LABEL: Record<AssetHealth, string> = {
  ok: '正常',
  warn: '偏旧',
  stale: '异常',
  unknown: '未知',
};

export const fmtTime = (iso: string | null | undefined): string => {
  if (!iso) return '-';
  try {
    return new Date(iso).toLocaleString('zh-CN', {
      month: '2-digit',
      day: '2-digit',
      hour: '2-digit',
      minute: '2-digit',
    });
  } catch {
    return iso;
  }
};

export const fmtNum = (v: number | null | undefined): string =>
  v === null || v === undefined ? '-' : v.toLocaleString('zh-CN');

export const fmtDur = (seconds: number | null | undefined): string => {
  if (seconds === null || seconds === undefined) return '-';
  if (seconds < 60) return `${seconds.toFixed(0)}s`;
  const m = Math.floor(seconds / 60);
  const s = Math.round(seconds % 60);
  return `${m}:${String(s).padStart(2, '0')}`;
};

/** 把散落的日期压成人读的区间：["07-10","07-13","07-14"] → "07-10、07-13~07-14" */
export const fmtDateRanges = (dates: string[], max = 3): string => {
  if (!dates.length) return '-';
  const sorted = [...dates].sort();
  const groups: string[][] = [];
  for (const d of sorted) {
    const last = groups[groups.length - 1];
    if (!last) {
      groups.push([d]);
      continue;
    }
    const prev = new Date(last[last.length - 1]);
    const cur = new Date(d);
    // 连续（含跨周末）就并进同一段
    const gap = (cur.getTime() - prev.getTime()) / 86_400_000;
    if (gap <= 3) last.push(d);
    else groups.push([d]);
  }
  const short = (s: string) => s.slice(5);
  const parts = groups.map((g) =>
    g.length === 1 ? short(g[0]) : `${short(g[0])}~${short(g[g.length - 1])}`,
  );
  if (parts.length <= max) return parts.join('、');
  return `${parts.slice(0, max).join('、')} 等 ${groups.length} 段`;
};

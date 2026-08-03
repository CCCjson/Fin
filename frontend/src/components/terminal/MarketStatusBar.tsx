import React, { useEffect, useState } from 'react';
import { arenaService, type MarketStatus } from '../../services/arenaService';

/**
 * 市场状态条（S7，裁决 18）——「这个市场现在是开着还是睡着」。
 *
 * ⭐ crypto 恒「交易中」（7×24）。⛔ 别让它跟着 A 股一起显示休市 ——
 * 那会让人以为「现在不用管币」，而它其实一直在动。
 *
 * ⚠️ 判据来自后端 `common/market_session`（S6），**前端不自己算时段** ——
 * 那需要一份时区表 + DST 规则，算错了还看不出来。
 */

export const MarketStatusBar: React.FC = () => {
  const [markets, setMarkets] = useState<MarketStatus[]>([]);

  useEffect(() => {
    const load = () => arenaService.marketStatus()
      .then((r) => setMarkets(r.markets))
      .catch(() => { /* 状态条拿不到不该影响终端主体 */ });
    load();
    // 一分钟一次足够：这条只在开/收盘那一刻会变。
    const t = setInterval(load, 60_000);
    return () => clearInterval(t);
  }, []);

  if (!markets.length) return null;

  return (
    <div className="flex items-center gap-3 flex-wrap text-xs">
      {markets.map((m) => (
        <span key={m.market} className="flex items-center gap-1.5">
          <span className={`w-1.5 h-1.5 rounded-full ${
            m.is_open ? 'bg-primary animate-pulse' : 'bg-gray-600'}`} />
          <span className="text-gray-400">{m.market_label || m.market}</span>
          <span className={m.is_open ? 'text-primary' : 'text-gray-600'}>{m.label}</span>
          {/* ⚠️ 当地墙上时间由**后端算好**直接显示。
              ⛔ 别拿 `local_clock.iso` 丢进 `new Date()` 再格式化 —— 那会转成浏览器
              本地时区，四个市场就全显示成同一个钟点，「当地时间」就成了骗人的。 */}
          <span className="text-gray-600 tabular-nums"
            title={`当地 ${m.local_clock.date} ${m.local_clock.wall_clock} (UTC${m.local_clock.utc_offset})`}>
            {m.local_clock.wall_clock}
          </span>
        </span>
      ))}
    </div>
  );
};

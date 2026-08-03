import React from 'react';

/**
 * 竞技场的市场切换器（S5 任务 03b）。
 *
 * 裁决 7 的新读法是「**每个市场**同期只有一条 live」（Jason 2026-08-03 拍板），
 * 于是「卫冕者 / 判定 / 排行榜」三块都变成了**按市场**的。这个切换器就是那个「哪个市场」。
 *
 * 🔴 **不显示 = 有一个市场看不见**。分族之前终端只有一个竞技场，分族之后不给切换器
 * 的话，Jason 永远只看得见 crypto，而 A 股那条卫冕者在屏幕上根本不存在 ——
 * 它却在真的下单。
 *
 * ⚠️ 候选市场来自后端的 `markets_with_strategies`（口径与排行榜一致：未归档的都算），
 * ⛔ 别在前端写死四个市场：写死的话，一个还没建过策略的市场会显示成「空竞技场」，
 * 而那和「这个市场的策略全退役了」是两件事。
 *
 * ⛔ 中文名也来自后端（`market_labels`）—— 前端存一份的话，改名时两处会分叉。
 */

interface Props {
  /** 当前选中的市场 */
  value: string;
  /** 有策略的市场（后端给），少于两个时整个切换器不渲染 */
  options: string[] | undefined;
  /** canonical → 中文名（后端给）。还没拿到时退回显示 canonical 名。 */
  labels?: Record<string, string>;
  onChange: (market: string) => void;
}

export const MarketSwitcher: React.FC<Props> = ({ value, options, labels, onChange }) => {
  // ⚠️ 当前市场必须在候选里：切到 A 股之后它的最后一条策略被退役时，
  // 后端不再返回 a_share，而屏幕上仍停在 A 股 —— 不补进去的话高亮会整个消失，
  // 看起来像「四个都没选中」。
  const markets = Array.from(new Set([...(options ?? []), value]));
  if (markets.length < 2) return null;

  return (
    <div className="flex items-center gap-0.5 p-0.5 rounded-lg bg-dark-light border border-border"
         title="竞技场按市场分族：每个市场各有一条卫冕者，跨市场不排名">
      {markets.map((m) => (
        <button
          key={m}
          onClick={() => onChange(m)}
          className={`px-2.5 py-1 rounded-md text-xs font-medium transition-all ${
            m === value
              ? 'bg-primary/20 text-primary'
              : 'text-gray-500 hover:text-gray-300'
          }`}>
          {labels?.[m] || m}
        </button>
      ))}
    </div>
  );
};

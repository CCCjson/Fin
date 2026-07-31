import React from 'react';
import type { ArenaVerdict, ChampionView } from '../../../services/arenaService';
import { PnlValue } from '../PnlValue';

/**
 * ① 卫冕者面板（S7）—— 当前跑实盘的那条。
 *
 * ⚠️ 这里几乎所有文字都**直接来自后端**（`verdict` / `means` / `weakening.reason`）。
 * 裁决 16 的「每个数字必须自解释」是在引擎层实现的，前端**原样展示** ——
 * 在这儿重写一套解释逻辑会立刻和后端口径分叉。
 *
 * 🔴 **三个状态，三条分支，一个都不能省**：
 *   1. `strategy` 为空 —— 没有在跑实盘的策略；
 *   2. `strategy` 有但 `ok=false` —— 有卫冕者、算不出成绩，**此时 `health` 里
 *      连 `runs` 键都没有**（后端提前返回）。早先这儿只判了状态 1，于是
 *      `h?.runs.total` 在状态 2 直接 TypeError —— `?.` 挡的是 `h` 为空，
 *      挡不住 `h.runs` 为 undefined。而 ErrorBoundary 包的是整个 App，
 *      结果不是「一块面板挂了」，是**整个桌面 App 变错误页**。
 *      触发场景很日常：刚 arm 一条新策略还没 tick；引擎停了超过窗口天数。
 *   3. `ok=true` —— 正常。
 *
 * ⚠️ `weakening` 从 `/verdict` 来，不从 `/champion` 来 —— 它的判定依赖窗口长度，
 * 两个端点各算一份会在同一块屏上给出相反结论。
 */
export const ChampionPanel: React.FC<{
  data: ChampionView | null;
  verdict: ArenaVerdict | null;
}> = ({ data, verdict }) => {
  if (!data) return <div className="text-sm text-gray-500">加载中…</div>;

  const s = data.strategy;
  if (!s) {
    // 「没有卫冕者」不是错误，是一个有内容的状态 —— 理由原样说出来
    return <div className="text-sm text-gray-400">{data.reason}</div>;
  }

  const h = data.health;
  const weakening = verdict?.champion_weakening;
  // ⚠️ 只有 ok 时这些字段才存在
  const reasons = h?.ok ? Object.entries(h.no_order_reasons || {}) : [];
  const top = reasons[0];

  return (
    <div className="space-y-3">
      {data.halted && (
        <div className="rounded-lg bg-red-500/10 border border-red-500/30 px-3 py-2 text-sm text-red-300">
          ⛔ 已被护栏熔断停机 —— 现在实盘上没有策略在跑，下面是停机前的数据。
        </div>
      )}
      {weakening?.weakening && (
        <div className="rounded-lg bg-amber-500/10 border border-amber-500/30 px-3 py-2 text-sm text-amber-200">
          🔴 正在变弱：{weakening.reason}
        </div>
      )}

      <div className="flex items-baseline gap-2 flex-wrap">
        <span className="text-white text-lg font-semibold">{s.name}</span>
        <span className="text-xs px-1.5 py-0.5 rounded bg-dark text-gray-300">v{s.version}</span>
        {data.is_benchmark && (
          <span className="text-xs px-1.5 py-0.5 rounded bg-accent-purple/20 text-accent-purple">
            基准线
          </span>
        )}
      </div>

      {/* ⭐ 有卫冕者但算不出成绩 —— 这句话正是最该说的，早先它从头到尾没被渲染过 */}
      {!h?.ok && (
        <p className="text-sm text-gray-400 leading-relaxed border-l-2 border-amber-500/40 pl-3">
          {data.reason || h?.reason || '暂时算不出这条策略的成绩。'}
        </p>
      )}

      {h?.ok && (
        <>
          <div className="grid grid-cols-2 md:grid-cols-4 gap-3 text-sm">
            <Metric label="运行" value={`${h.runs?.total ?? 0} 次`} />
            <Metric label="待确认单" value={`${h.orders_staged ?? 0} 张`} />
            <Metric label="没开单主因" value={top ? top[1].means : '—'} />
            <div>
              <div className="text-xs text-gray-500">盈亏</div>
              <PnlValue pnl={h.pnl} windowDays={h.window?.days} />
            </div>
          </div>

          {/* ⭐ 一句人话结论 —— 这块面板真正的价值所在 */}
          {h.verdict && (
            <p className="text-sm text-gray-300 leading-relaxed border-l-2 border-primary/40 pl-3">
              {h.verdict}
            </p>
          )}
        </>
      )}
    </div>
  );
};

const Metric: React.FC<{ label: string; value: React.ReactNode }> = ({ label, value }) => (
  <div>
    <div className="text-xs text-gray-500">{label}</div>
    <div className="text-white">{value}</div>
  </div>
);

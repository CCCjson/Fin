import React from 'react';
import type { ArenaVerdict, StandingRow } from '../../../services/arenaService';
import { PnlValue } from '../PnlValue';

/**
 * ② 挑战者排行榜 + 竞技场判定（S7）。
 *
 * ⛔ **刻意不排序、不评优劣**（裁决 9 坑 3）：paper 与 live 不可比、样本量差异巨大，
 * 按收益排序等于诱导「追逐近期最优」。⭐ 唯一的例外是**基准线置顶**（裁决 8）——
 * 没有基准线的胜率是自说自话。排序由后端做，前端原样渲染。
 *
 * ⚠️ 「该不该换」是**规则的判定**，不是 AI 的（裁决 10）。这块面板本身只读 ——
 * 它给的是判据，不是按钮。
 */
export const StandingsPanel: React.FC<{
  rows: StandingRow[] | null;
  verdict: ArenaVerdict | null;
  /**
   * 后端给的一句话（`ranking_note`）。⚠️ 空列表时**必须显示它** ——
   * fail-closed 的理由（比如「认不出市场 xxx」）就挂在这个字段上，
   * 不渲染的话那道 fail-closed 又退回成了静默：屏幕上只会写「还没有策略」。
   */
  note?: string;
}> = ({ rows, verdict, note }) => {
  if (!rows) return <div className="text-sm text-gray-500">加载中…</div>;
  if (!rows.length) {
    return <div className="text-sm text-gray-400">{note || '还没有策略。'}</div>;
  }

  const byId = new Map((verdict?.results || []).map((r) => [r.challenger.strategy_id, r]));

  return (
    <div className="space-y-3">
      {verdict?.verdict && (
        <p className="text-sm text-gray-300 leading-relaxed border-l-2 border-primary/40 pl-3">
          {verdict.verdict}
        </p>
      )}

      <div className="space-y-2">
        {rows.map((r) => {
          const res = byId.get(r.strategy_id);
          return (
            <div key={r.strategy_id}
              className="rounded-lg border border-border bg-dark-light/20 px-3 py-2">
              <div className="flex items-center gap-2 flex-wrap">
                <span className="text-white text-sm">{r.name}</span>
                <span className="text-xs px-1.5 py-0.5 rounded bg-dark text-gray-300">
                  v{r.version}
                </span>
                {/* ⭐ 裁决 8：只置顶不标名字等于没说 —— 这块屏的价值就在
                    「一眼看到 AI 有没有输给你手写那条」 */}
                {r.is_benchmark && (
                  <span className="text-xs px-1.5 py-0.5 rounded bg-accent-purple/20 text-accent-purple"
                    title="你手写的那条，永久当尺子，不参与切换">
                    基准线
                  </span>
                )}
                <span className={`text-xs px-1.5 py-0.5 rounded ${
                  r.mode === 'live'
                    ? 'bg-green-500/15 text-green-400'
                    : 'bg-blue-500/15 text-blue-400'}`}>
                  {r.mode === 'live' ? '实盘' : '纸面'}
                </span>
                <span className="text-xs text-gray-500">{r.runs} 次运行</span>
                <span className="ml-auto text-sm">
                  <PnlValue pnl={r.pnl} windowDays={r.window_days} />
                </span>
              </div>

              {/* 四道门槛：过了哪些、卡在哪 —— label 和 detail 全部来自后端。
                  ⛔ 早先这儿抄了一份中文名，措辞立刻就分叉了
                  （后端「观察期够长」→ 前端「观察期」）。口径只准有一处。 */}
              {res && (
                <div className="mt-2 flex flex-wrap gap-1.5">
                  {Object.entries(res.gates).map(([k, g]) => (
                    <span key={k} title={g.detail}
                      className={`text-xs px-1.5 py-0.5 rounded border cursor-help ${
                        g.passed
                          ? 'border-primary/30 text-primary/80'
                          : 'border-amber-500/40 text-amber-300'}`}>
                      {g.passed ? '✓' : '✗'} {g.label || k}
                    </span>
                  ))}
                </div>
              )}
              {res?.verdict && (
                <p className="mt-1.5 text-xs text-gray-400 leading-relaxed">{res.verdict}</p>
              )}
            </div>
          );
        })}
      </div>

      <p className="text-xs text-gray-600">
        未排名（基准线除外）—— 换不换由规则判，要换请在 MoneyBill 里说。
      </p>
    </div>
  );
};


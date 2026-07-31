import React from 'react';
import type { ProposalRow, ProposalScorecard } from '../../../services/arenaService';

/**
 * ③ AI 提案待审（S7）—— 有则醒目，无则收起。
 *
 * ⭐ 每份提案带「预期收益 / 预期胜率 / 多少天内兑现」= **可证伪断言**（裁决 13）。
 * 这是整个计划最漂亮的一环：拿账户余额给 AI 打分。
 *
 * ⭐ **diff 是后端逐字段算出来的，不是 AI 自己描述的**（S2）——
 * 让 AI 说自己改了什么，它可能说漏说错，而 Jason 在这儿要审的正是「到底改了哪几个数」。
 * 所以 `diff_text` 排在 AI 的说法**前面**。
 *
 * ⛔ 战绩总账**只有计数没有胜率百分比**：一年 12-24 条提案，统计显著要 ≥30 样本，
 * 报百分比等于给噪声盖权威章。**别在前端自己算一个**。
 */
export const ProposalsPanel: React.FC<{
  rows: ProposalRow[] | null;
  scorecard: ProposalScorecard | null;
}> = ({ rows, scorecard }) => {
  if (!rows) return <div className="text-sm text-gray-500">加载中…</div>;

  const pending = rows.filter((r) => r.status === 'proposed');

  return (
    <div className="space-y-3">
      {/* ⭐ 总账那句话**原样用后端的 `note`**。
          ⛔ 早先这儿自己拼了一句，只列 hit/miss/pending —— 而 `unable`（没上线过，
          不算 AI 判断失误）和 `unevaluated`（提案中/已拒/被取代）两类**不计入却计在
          总数里**，于是屏幕上会出现「15 份提案：兑现 1、落空 0、待观察 2」这种
          明显对不上的句子。口径只准有一处。 */}
      {scorecard && scorecard.proposals > 0 && (
        <div className="text-xs text-gray-400 leading-relaxed">
          共 {scorecard.proposals} 份提案。{scorecard.note}
        </div>
      )}

      {!pending.length && (
        <div className="text-sm text-gray-400">
          没有待审提案。想让 AI 提一个，在 MoneyBill 里说「帮我看看这条策略该怎么改」。
        </div>
      )}

      {pending.map((p) => (
        <div key={p.proposal_id}
          className="rounded-lg border border-accent-purple/30 bg-accent-purple/5 px-3 py-2.5 space-y-2">
          {/* 事实在前：系统逐字段比出来的改动 */}
          <div>
            <div className="text-xs text-gray-500 mb-1">实际改动（系统比对）</div>
            <pre className="text-xs text-gray-200 whitespace-pre-wrap font-mono leading-relaxed">
              {p.diff_text}
            </pre>
          </div>

          {/* ⭐ 可证伪断言 —— 将来会被真实盈亏打分 */}
          <div className="text-xs text-accent-cyan">
            AI 的断言：{p.horizon_days} 天内预期收益 {pct(p.expected_return_pct)}、
            胜率 {(p.expected_win_rate * 100).toFixed(0)}%
          </div>

          <div className="text-xs text-gray-400 space-y-0.5">
            <div>不足：{p.shortfall}</div>
            <div>理由：{p.rationale}</div>
          </div>

          <div className="text-xs text-gray-600">
            要批准请在 MoneyBill 里说 —— 那一步会让你再确认一次改动。
          </div>
        </div>
      ))}

      {/* 已定论的：兑现了没 */}
      {rows.filter((r) => r.outcome === 'hit' || r.outcome === 'miss').map((p) => (
        <div key={p.proposal_id} className="text-xs text-gray-500 flex items-center gap-2">
          <span className={p.outcome === 'hit' ? 'text-primary' : 'text-amber-400'}>
            {p.outcome === 'hit' ? '兑现' : '落空'}
          </span>
          <span>{p.change_summary}</span>
          {p.actual_return_pct !== null && (
            <span>实际 {pct(p.actual_return_pct)}（预期 {pct(p.expected_return_pct)}）</span>
          )}
          {/* ⚠️ basis 必须跟着说：纸面兑现 ≠ 真钱兑现 */}
          {p.outcome_basis === 'paper_simulated' && <span>· 纸面</span>}
        </div>
      ))}
    </div>
  );
};

const pct = (v: number) => `${v >= 0 ? '+' : ''}${(v * 100).toFixed(1)}%`;

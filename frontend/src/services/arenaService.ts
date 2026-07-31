import api from './api';

/**
 * 交易终端的**只读**数据（S7）—— 封装 /arena/*。
 *
 * 🔒 这个文件**只有 GET**。⛔ 别往里加 post/put/delete —— 新增的查询能力没有理由
 * 顺手开写口，写动作有它们自己的 service（`cryptoStrategyService`）。
 *
 * ⚠️ 人话结论（verdict / detail / label / means / note）由**后端引擎层**产出，前端
 * **原样展示**。在前端重写一套解释逻辑会立刻和后端口径分叉（裁决 16 那条
 * 「每个数字必须自解释」的实现在后端，不在这里）。
 */

export type MarketPhase = 'intraday' | 'pre_market' | 'after_close' | 'closed_day';

export interface MarketStatus {
  market: 'a_share' | 'hk_stock' | 'us_stock' | 'crypto';
  phase: MarketPhase;
  label: string;          // 「交易中 / 尚未开盘 / 已收盘 / 休市」
  is_open: boolean;
  /**
   * ⚠️ **后端已经算好的交易所当地时间**，直接显示 `wall_clock`。
   * ⛔ 别把 `iso` 丢进 `new Date()` 再格式化 —— 那会转成浏览器本地时区，
   * 四个市场就全显示成同一个钟点，"当地时间" 这个说法就成了骗人的。
   */
  local_clock: { wall_clock: string; date: string; utc_offset: string; iso: string };
}

/** 盈亏口径。⚠️ **必须跟着数字一起读**：paper 是理想撮合，不等于真钱。 */
export type PnlBasis = 'live_fills' | 'paper_simulated' | 'mixed' | 'none';

export interface StrategyPnl {
  basis: PnlBasis;
  realized_pnl?: number;
  closed_legs?: number;
  reason?: string;
  note?: string;
  live?: { realized_pnl: number; closed_legs: number };
  paper?: { realized_pnl: number; closed_legs: number };
  coverage?: { attributed_trades: number; unknown_source_trades: number };
}

/**
 * 体检报告。
 *
 * 🔴 **`ok=false` 时下面这些字段一个都不存在**（后端提前返回，只给 reason）。
 * 所以除 `ok`/`reason` 外全部标成可选 —— 早先它们是必填，于是
 * `h?.runs.total` 这种写法 tsc 查不出来，而窗口内没有运行记录时就是一个
 * TypeError，ErrorBoundary 包的是整个 App，**整个桌面 App 会变成错误页**。
 * 触发场景很日常：刚 arm 一条新策略还没 tick，或引擎停了超过窗口天数。
 */
export interface StrategyHealth {
  ok: boolean;
  reason?: string;
  strategy_id?: string;
  name?: string;
  mode?: string;
  status?: string;
  enabled?: boolean;
  window?: { days: number; since: string; until: string };
  window_days?: number;
  runs?: { total: number; by_status: Record<string, number> };
  no_order_reasons?: Record<string, { count: number; means: string; example?: string }>;
  orders_staged?: number;
  pnl?: StrategyPnl;
  guardrails?: { consecutive_trips: number; halted_reason: string | null };
  verdict?: string;       // ⭐ 人话结论，原样显示
}

/**
 * ⚠️ 这里**没有 `weakening`**，是刻意的：「卫冕者正在变弱」的判定依赖窗口长度
 * （`min_history=30`），30 天窗口和 90 天窗口大概率给出相反结论。
 * 它只从 `/arena/verdict` 出一份，见 `api/routes/arena.py` 模块头。
 */
export interface ChampionView {
  ok: boolean;
  reason?: string;
  strategy: { strategy_id: string; name: string; version: number; family_id: string;
              mode: string; status: string; is_benchmark: boolean; halted: boolean } | null;
  halted?: boolean;
  is_benchmark?: boolean;
  health?: StrategyHealth;
}

export interface StandingRow {
  strategy_id: string;
  name: string;
  version: number;
  family_id: string;
  mode: string;
  status: string;
  enabled: boolean;
  is_benchmark: boolean;  // ⭐ 裁决 8：哪条是尺子，得标出来
  runs: number;
  orders_staged: number;
  pnl: StrategyPnl | null;
  window_days: number;    // ⚠️ 盈亏是这个窗口内的，不是「开仓以来」
  verdict: string;
}

export interface Gate {
  passed: boolean;
  detail: string;         // ⭐ 每道门槛的人话，原样显示
  label: string;          // ⭐ 中文名也由后端给 —— 前端抄一份立刻分叉
}

export interface ChallengerResult {
  challenger: { strategy_id: string; name: string; version: number; mode: string;
                basis: PnlBasis; days: number; trade_count: number };
  should_switch: boolean;
  gates: Record<string, Gate>;
  blocked_by: string[];
  blocked_labels: string[];
  verdict: string;
}

export interface ArenaVerdict {
  champion: { strategy_id: string; name: string; version: number } | null;
  champion_halted?: boolean;
  champion_is_benchmark?: boolean;
  champion_weakening?: { weakening: boolean; reason: string };
  challengers: number;    // ⚠️ 恒为 number（后端保证），别写 `.length`
  challenger_details?: unknown[];
  days_since_last_switch: number | null;
  results: ChallengerResult[];
  verdict: string;
  note: string;
}

export interface ProposalRow {
  proposal_id: string;
  base_strategy_id: string;
  shortfall: string;
  change_summary: string;
  rationale: string;
  expected_return_pct: number;
  expected_win_rate: number;
  horizon_days: number;
  diff_text: string;      // ⭐ 由后端确定性算出的人话 diff，**不是 AI 自己描述的**
  status: string;
  outcome: string | null;
  outcome_basis: string | null;
  actual_return_pct: number | null;
  actual_win_rate: number | null;
  created_at: string;
}

export interface ProposalScorecard {
  proposals: number;
  by_outcome: Record<string, number>;
  by_outcome_and_basis: Record<string, number>;
  decided: number;
  decided_with_real_money: number;
  note: string;
  // ⛔ 这里**永远不会有** win_rate / hit_rate：一年 12-24 条提案，
  // 报百分比等于给噪声盖权威章。别在前端自己算一个。
}

export const arenaService = {
  marketStatus: (): Promise<{ markets: MarketStatus[] }> =>
    api.get('/arena/market-status'),

  champion: (days = 30): Promise<ChampionView> =>
    api.get('/arena/champion', { params: { days } }),

  standings: (days = 30, includeArchived = false):
    Promise<{ count: number; days: number; strategies: StandingRow[];
              ranking_note: string }> =>
    api.get('/arena/standings', { params: { days, include_archived: includeArchived } }),

  verdict: (days = 90): Promise<ArenaVerdict> =>
    api.get('/arena/verdict', { params: { days } }),

  proposals: (limit = 10, status?: string):
    Promise<{ count: number; proposals: ProposalRow[];
              scorecard: ProposalScorecard | null }> =>
    api.get('/arena/proposals', { params: { limit, status } }),
};

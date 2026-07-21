import React from 'react';
import { motion, useReducedMotion } from 'framer-motion';
import {
  AreaChart, Area, ResponsiveContainer, YAxis, Tooltip,
} from 'recharts';
import type { WidgetSpec } from '../../services/agentService';
import { MetricCard } from '../backtest/MetricCard';
import { AnimatedNumber } from '../common/AnimatedNumber';
import { fadeUp, pickVariants } from '../common/motion';
import { openExternal } from '../../utils/openExternal';

/* ================================================================
   Widget 分发器：按 type 渲染成图表/卡片（复用现成组件）
   ================================================================ */

const DIM_LABELS: Record<string, string> = {
  technical: '技术面',
  fundamental: '基本面',
  sentiment: '新闻情感',
  ml: 'ML 预测',
  position: '持仓风险',
  // crypto 择时三维
  derivatives: '衍生品情绪',
  regime: 'BTC 大势',
};

// 排雷 verdict → 徽章样式
const SCREEN_VERDICT_STYLE: Record<string, { label: string; cls: string }> = {
  pass: { label: '排雷通过', cls: 'bg-green-500/20 text-green-300 border-green-500/40' },
  caution: { label: '排雷警示', cls: 'bg-yellow-500/20 text-yellow-300 border-yellow-500/40' },
  avoid: { label: '排雷否决', cls: 'bg-red-500/20 text-red-300 border-red-500/40' },
  unknown: { label: '排雷未知', cls: 'bg-gray-500/20 text-gray-300 border-gray-500/40' },
};

const REC_STYLE: Record<string, { label: string; cls: string }> = {
  BUY: { label: '买入', cls: 'bg-green-500/20 text-green-300 border-green-500/40' },
  HOLD: { label: '持有/观望', cls: 'bg-yellow-500/20 text-yellow-300 border-yellow-500/40' },
  SELL: { label: '卖出/回避', cls: 'bg-red-500/20 text-red-300 border-red-500/40' },
  'N/A': { label: '数据不足', cls: 'bg-gray-500/20 text-gray-300 border-gray-500/40' },
};

const scoreColor = (s: number | null) => {
  if (s === null || s === undefined) return 'bg-gray-600';
  if (s >= 65) return 'bg-green-500';
  if (s >= 45) return 'bg-yellow-500';
  return 'bg-red-500';
};

/** 卡片外壳：统一入场 + hover 抬升 */
const WidgetShell: React.FC<{ children: React.ReactNode; className?: string }> = ({ children, className = '' }) => {
  const reduce = useReducedMotion();
  return (
    <motion.div
      variants={pickVariants(reduce, fadeUp)}
      initial="hidden"
      animate="show"
      whileHover={reduce ? undefined : { y: -2 }}
      className={`bg-dark-card border border-border rounded-xl my-2 ${className}`}
    >
      {children}
    </motion.div>
  );
};

const DimBar: React.FC<{ name: string; score: number | null }> = ({ name, score }) => {
  const reduce = useReducedMotion();
  return (
    <div className="mb-2.5">
      <div className="flex justify-between text-xs mb-1">
        <span className="text-gray-300">{DIM_LABELS[name] || name}</span>
        <span className="text-gray-400">{score === null || score === undefined ? '— 数据不足' : score.toFixed(1)}</span>
      </div>
      <div className="h-2 bg-dark rounded-full overflow-hidden">
        <motion.div
          className={`h-full ${scoreColor(score)}`}
          initial={reduce ? false : { width: 0 }}
          animate={{ width: `${score ?? 0}%` }}
          transition={reduce ? undefined : { type: 'spring', stiffness: 120, damping: 22, delay: 0.1 }}
        />
      </div>
    </div>
  );
};

const CockpitScoreWidget: React.FC<{ data: any }> = ({ data }) => {
  const rec = REC_STYLE[data.recommendation] || REC_STYLE['N/A'];
  const dims = data.dimensions || {};
  const sug = data.suggested;
  return (
    <WidgetShell className="p-4">
      {/* 头部 */}
      <div className="flex items-center justify-between flex-wrap gap-2 mb-3">
        <div className="text-base font-bold text-white">
          {data.name} <span className="text-gray-500 text-xs">{data.symbol}</span>
        </div>
        <div className="flex items-center gap-3">
          <div className="text-center">
            <div className="text-2xl font-bold text-white leading-none">
              <AnimatedNumber value={data.composite} decimals={1} placeholder="—" />
            </div>
            <div className="text-[10px] text-gray-500">综合评分</div>
          </div>
          <span className={`px-3 py-1.5 rounded-lg border text-xs font-semibold ${rec.cls}`}>{rec.label}</span>
        </div>
      </div>

      {/* 关键指标 */}
      <div className="grid grid-cols-2 md:grid-cols-4 gap-2 mb-3 text-sm">
        <div className="bg-dark-light rounded-lg p-2.5">
          <div className="text-gray-500 text-xs">现价</div>
          <div className="text-white font-medium">{data.price ?? '—'}</div>
        </div>
        <div className="bg-dark-light rounded-lg p-2.5">
          <div className="text-gray-500 text-xs">建议仓位</div>
          <div className="text-white font-medium">{data.suggested_position_pct ?? 0}%</div>
        </div>
        <div className="bg-dark-light rounded-lg p-2.5">
          <div className="text-gray-500 text-xs">止损位</div>
          <div className="text-white font-medium">{data.stop_loss ?? '—'}</div>
        </div>
        <div className="bg-dark-light rounded-lg p-2.5">
          <div className="text-gray-500 text-xs">PE / PB</div>
          <div className="text-white font-medium">
            {data.valuation?.pe_ttm?.toFixed?.(1) ?? data.valuation?.pe?.toFixed?.(1) ?? '—'} / {data.valuation?.pb?.toFixed?.(2) ?? '—'}
          </div>
        </div>
      </div>

      {/* 五维评分 */}
      <div className="bg-dark-light rounded-lg p-3 mb-3">
        <div className="text-xs font-semibold text-gray-400 mb-2">五维评分</div>
        {(['technical', 'fundamental', 'sentiment', 'ml', 'position'] as const).map((k) => (
          <DimBar key={k} name={k} score={dims[k]?.score} />
        ))}
      </div>

      {/* 按资金建议：只有真的判定 BUY 才显示「建议买入」，避免跟 HOLD/SELL 的
          结论矛盾——sug.affordable 只代表买得起，不代表这次就该买。 */}
      {data.recommendation === 'BUY' && sug && sug.affordable && (
        <div className="bg-dark-light rounded-lg p-3 text-sm flex flex-wrap gap-x-6 gap-y-1">
          <span className="text-gray-500 text-xs">💰 建议买入 <span className="text-green-300 font-semibold">{sug.shares} 股（{sug.lots} 手）</span></span>
          <span className="text-gray-500 text-xs">约 <span className="text-white">¥{sug.amount?.toLocaleString?.()}</span></span>
        </div>
      )}
    </WidgetShell>
  );
};

/* ---------------- 预测仪表盘 ---------------- */
const ConfidenceRing: React.FC<{ value: number; up: boolean }> = ({ value, up }) => {
  const reduce = useReducedMotion();
  const r = 30;
  const c = 2 * Math.PI * r;
  const pct = Math.max(0, Math.min(1, value));
  const color = up ? '#EF4444' : '#10B981'; // 红涨绿跌
  return (
    <svg width="80" height="80" viewBox="0 0 80 80" className="shrink-0">
      <circle cx="40" cy="40" r={r} fill="none" stroke="#1E293B" strokeWidth="7" />
      <motion.circle
        cx="40" cy="40" r={r} fill="none" stroke={color} strokeWidth="7" strokeLinecap="round"
        transform="rotate(-90 40 40)"
        strokeDasharray={c}
        initial={reduce ? false : { strokeDashoffset: c }}
        animate={{ strokeDashoffset: c * (1 - pct) }}
        transition={reduce ? undefined : { type: 'spring', stiffness: 90, damping: 20, delay: 0.1 }}
        style={{ filter: `drop-shadow(0 0 6px ${color}88)` }}
      />
      <text x="40" y="45" textAnchor="middle" className="fill-white font-bold" fontSize="18">
        {Math.round(pct * 100)}
      </text>
    </svg>
  );
};

const PredictionWidget: React.FC<{ data: any }> = ({ data }) => {
  const up = String(data.direction).toUpperCase() === 'UP';
  const ret = data.predicted_return;
  const retPct = typeof ret === 'number' ? ret * 100 : null;
  const dirCls = up ? 'text-bull' : 'text-bear';
  return (
    <WidgetShell className="p-4">
      <div className="flex items-center gap-4">
        <ConfidenceRing value={typeof data.confidence === 'number' ? data.confidence : 0} up={up} />
        <div className="flex-1 min-w-0">
          <div className="text-base font-bold text-white mb-1">
            {data.name || data.symbol} <span className="text-gray-500 text-xs">{data.symbol}</span>
          </div>
          <div className="flex items-center gap-2 mb-1.5">
            <span className={`text-xl font-bold ${dirCls}`}>{up ? '↑ 看涨' : '↓ 看跌'}</span>
            <span className="text-xs text-gray-500">未来 {data.forward_days ?? '—'} 天</span>
          </div>
          <div className="flex flex-wrap gap-x-5 gap-y-1 text-xs">
            <span className="text-gray-500">置信度 <span className="text-gray-200 font-medium">
              <AnimatedNumber value={typeof data.confidence === 'number' ? data.confidence * 100 : null} decimals={1} suffix="%" />
            </span></span>
            <span className="text-gray-500">预测收益 <span className={`font-medium ${retPct !== null && retPct >= 0 ? 'text-bull' : 'text-bear'}`}>
              {retPct === null ? '—' : <AnimatedNumber value={retPct} decimals={2} prefix={retPct >= 0 ? '+' : ''} suffix="%" />}
            </span></span>
          </div>
        </div>
      </div>
      <div className="mt-3 text-[10px] text-gray-600 leading-snug">
        ⚠️ 模型预测仅供参考，不构成投资建议。
      </div>
    </WidgetShell>
  );
};

/* ---------------- 实时报价卡 ---------------- */
const QuoteCard: React.FC<{ q: any }> = ({ q }) => {
  const pct = typeof q.change_pct === 'number' ? q.change_pct : null;
  const up = (pct ?? 0) >= 0;
  const cls = up ? 'text-bull' : 'text-bear';
  return (
    <motion.div
      key={`${q.symbol}-${q.price}`}
      initial={{ backgroundColor: 'rgba(0,255,156,0)' }}
      animate={{ backgroundColor: ['rgba(0,255,156,0.12)', 'rgba(0,255,156,0)'] }}
      transition={{ duration: 0.8 }}
      className="bg-dark-light rounded-lg p-3"
    >
      <div className="flex items-center justify-between gap-2 mb-1">
        <span className="text-sm text-gray-200 font-medium truncate">{q.name || q.symbol}</span>
        <span className="text-[10px] text-gray-600">{q.symbol}</span>
      </div>
      <div className="flex items-end justify-between gap-2">
        <span className={`text-xl font-bold ${cls}`}>
          <AnimatedNumber value={typeof q.price === 'number' ? q.price : null} decimals={2} />
        </span>
        <span className={`text-sm font-medium ${cls}`}>
          {pct === null ? '—' : <AnimatedNumber value={pct} decimals={2} prefix={up ? '+' : ''} suffix="%" />}
        </span>
      </div>
    </motion.div>
  );
};

const QuoteWidget: React.FC<{ data: any; title?: string }> = ({ data, title }) => {
  const quotes = data.quotes || [];
  return (
    <WidgetShell className="p-3">
      {title && <div className="text-xs font-semibold text-gray-400 mb-2 px-1">{title}</div>}
      <div className="grid grid-cols-1 sm:grid-cols-2 gap-2">
        {quotes.map((q: any, i: number) => <QuoteCard key={i} q={q} />)}
      </div>
    </WidgetShell>
  );
};

/* ---------------- 迷你走势 sparkline ---------------- */
const SparklineWidget: React.FC<{ data: any; title?: string }> = ({ data, title }) => {
  const series = (data.series || []).map((p: any) => ({ date: p.date, close: p.close }));
  const pct = typeof data.change_pct === 'number' ? data.change_pct : null;
  const up = (pct ?? 0) >= 0;
  const color = up ? '#EF4444' : '#10B981'; // 红涨绿跌
  const gradId = `spark-${data.symbol || 'x'}`;
  return (
    <WidgetShell className="p-4">
      <div className="flex items-center justify-between flex-wrap gap-2 mb-2">
        <div className="text-sm font-bold text-white">{title || data.symbol}</div>
        <div className="flex items-baseline gap-3">
          <span className="text-lg font-bold text-white">
            <AnimatedNumber value={typeof data.latest_close === 'number' ? data.latest_close : null} decimals={2} />
          </span>
          <span className={`text-sm font-medium ${up ? 'text-bull' : 'text-bear'}`}>
            {pct === null ? '—' : <AnimatedNumber value={pct} decimals={2} prefix={up ? '+' : ''} suffix="%" />}
          </span>
        </div>
      </div>
      <div className="h-24 -mx-1">
        <ResponsiveContainer width="100%" height="100%">
          <AreaChart data={series} margin={{ top: 4, right: 4, bottom: 0, left: 4 }}>
            <defs>
              <linearGradient id={gradId} x1="0" y1="0" x2="0" y2="1">
                <stop offset="0%" stopColor={color} stopOpacity={0.35} />
                <stop offset="100%" stopColor={color} stopOpacity={0} />
              </linearGradient>
            </defs>
            <YAxis domain={['dataMin', 'dataMax']} hide />
            <Tooltip
              contentStyle={{ backgroundColor: '#151B2E', border: '1px solid #1E293B', borderRadius: 8, fontSize: 12 }}
              labelStyle={{ color: '#9ca3af' }}
              formatter={(v: any) => [v, '收盘']}
            />
            <Area type="monotone" dataKey="close" stroke={color} strokeWidth={2} fill={`url(#${gradId})`} dot={false} />
          </AreaChart>
        </ResponsiveContainer>
      </div>
      <div className="flex flex-wrap gap-x-5 gap-y-1 text-[11px] text-gray-500 mt-1">
        <span>最高 <span className="text-gray-300">{data.high ?? '—'}</span></span>
        <span>最低 <span className="text-gray-300">{data.low ?? '—'}</span></span>
      </div>
    </WidgetShell>
  );
};

const MetricCardsWidget: React.FC<{ data: any; title?: string }> = ({ data, title }) => (
  <WidgetShell className="p-3 bg-transparent border-0">
    {title && <div className="text-xs font-semibold text-gray-400 mb-2">{title}</div>}
    <div className="grid grid-cols-2 md:grid-cols-4 gap-2">
      {(data.cards || []).map((c: any, i: number) => (
        <MetricCard key={i} label={c.label} value={c.value} type={c.type} positive={c.positive} />
      ))}
    </div>
  </WidgetShell>
);

const PositionTableWidget: React.FC<{ data: any; title?: string }> = ({ data, title }) => {
  const rows = data.positions || [];
  return (
    <WidgetShell className="overflow-hidden p-0">
      {title && <div className="bg-dark-light px-3 py-2 text-xs font-semibold text-gray-400">{title}</div>}
      <table className="w-full text-sm">
        <thead className="bg-dark-light border-b border-border">
          <tr>
            <th className="px-3 py-2 text-left text-xs text-gray-500">代码</th>
            <th className="px-3 py-2 text-left text-xs text-gray-500">名称</th>
            <th className="px-3 py-2 text-right text-xs text-gray-500">数量</th>
            <th className="px-3 py-2 text-right text-xs text-gray-500">成本均价</th>
            <th className="px-3 py-2 text-right text-xs text-gray-500">已实现盈亏</th>
          </tr>
        </thead>
        <tbody className="divide-y divide-border">
          {rows.map((r: any, i: number) => (
            <tr key={i} className="hover:bg-dark-light/50">
              <td className="px-3 py-2 text-gray-300">{r.symbol}</td>
              <td className="px-3 py-2 text-gray-300">{r.name}</td>
              <td className="px-3 py-2 text-right text-gray-300">{r.quantity}</td>
              <td className="px-3 py-2 text-right text-gray-300">{r.avg_cost?.toFixed?.(3) ?? r.avg_cost}</td>
              <td className={`px-3 py-2 text-right ${(r.realized_pnl ?? 0) >= 0 ? 'text-bull' : 'text-bear'}`}>
                {r.realized_pnl?.toFixed?.(2) ?? r.realized_pnl}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </WidgetShell>
  );
};

/* ---------------- 知识库引用卡 ---------------- */
const SRC_LABELS: Record<string, string> = {
  paper: '论文',
  research: '研报',
  financial: '财报',
  internal: '内部',
  news: '新闻',
};

const KnowledgeSourcesWidget: React.FC<{ data: any; title?: string }> = ({ data, title }) => {
  const sources = data.sources || [];
  return (
    <WidgetShell className="p-3">
      <div className="text-xs font-semibold text-gray-400 mb-2 px-1">📚 {title || '知识库引用'}</div>
      <div className="space-y-1.5">
        {sources.map((s: any) => (
          <div key={s.index} className="bg-dark-light rounded-lg p-2.5 flex items-start gap-2">
            <span className="text-xs font-bold text-blue-300 shrink-0 mt-0.5">[{s.index}]</span>
            <div className="min-w-0 flex-1">
              <div className="flex items-center gap-2 flex-wrap">
                {s.url ? (
                  <a href={s.url} target="_blank" rel="noreferrer"
                     onClick={(e) => { e.preventDefault(); openExternal(s.url); }}
                     className="text-sm text-gray-200 hover:text-blue-300 truncate font-medium cursor-pointer">{s.title}</a>
                ) : (
                  <span className="text-sm text-gray-200 truncate font-medium">{s.title}</span>
                )}
                <span className="text-[10px] px-1.5 py-0.5 rounded bg-blue-500/15 text-blue-300 border border-blue-500/30 shrink-0">
                  {SRC_LABELS[s.source_type] || s.source_type}
                </span>
              </div>
              <div className="flex flex-wrap gap-x-3 text-[10px] text-gray-500 mt-0.5">
                {s.published_at && <span>{s.published_at}</span>}
                {typeof s.distance === 'number' && <span>相关度距离 {s.distance.toFixed(3)}</span>}
              </div>
            </div>
          </div>
        ))}
      </div>
    </WidgetShell>
  );
};

const PHASE_LABELS: Record<string, string> = {
  intraday: '盘中',
  pre_market: '盘前',
  after_close: '盘后',
  closed_day: '休市',
};

const STATE_STYLE: Record<string, { label: string; cls: string }> = {
  active: { label: '有可买标的', cls: 'bg-green-500/20 text-green-300 border-green-500/40' },
  weak: { label: '弱市观望', cls: 'bg-yellow-500/20 text-yellow-300 border-yellow-500/40' },
  empty_signals: { label: '空仓观望', cls: 'bg-gray-500/20 text-gray-300 border-gray-500/40' },
};

const yuan = (v: any) => {
  const n = Number(v);
  return Number.isFinite(n) ? `¥${n.toLocaleString()}` : '—';
};

/** 选股推荐榜：新买入 / 持仓处置 / 买不起（剔除） 三区 */
const RecommendationBoardWidget: React.FC<{ data: any; title?: string }> = ({ data, title }) => {
  const buys: any[] = data.buys || [];
  const holdings: any[] = data.holdings_advice || [];
  const skipped: any[] = data.skipped_unaffordable || [];
  const st = STATE_STYLE[data.market_state] || STATE_STYLE.weak;

  return (
    <WidgetShell className="p-4">
      {/* 头部 */}
      <div className="flex items-center justify-between flex-wrap gap-2 mb-3">
        <div className="text-base font-bold text-white flex items-center gap-2">
          🎯 {title || '选股推荐'}
          <span className="text-[10px] text-gray-500 font-normal">
            {PHASE_LABELS[data.session_phase] || data.session_phase}
            {data.provisional && ' · 盘中实时'}
          </span>
        </div>
        <span className={`px-2.5 py-1 rounded-lg border text-xs font-semibold ${st.cls}`}>{st.label}</span>
      </div>

      {/* 账户约束条 */}
      <div className="flex flex-wrap gap-x-4 gap-y-1 text-[11px] text-gray-500 mb-3">
        <span>总资金 <span className="text-gray-300">{yuan(data.total_capital)}</span></span>
        <span>可用现金 <span className="text-gray-300">{yuan(data.available_cash)}</span></span>
        <span>单股上限 <span className="text-gray-300">{yuan(data.max_single_amount)}</span></span>
        {data.signal_date && <span>信号日 <span className="text-gray-300">{data.signal_date}</span></span>}
      </div>

      {/* 新买入推荐 */}
      <div className="mb-3">
        <div className="text-xs font-semibold text-gray-400 mb-1.5">新买入推荐（{buys.length}）</div>
        {buys.length === 0 ? (
          <div className="text-xs text-gray-500 bg-dark-light rounded-lg p-2.5">
            暂无达标标的（需综合评级 BUY + 按账户买得起 + 过风控）
          </div>
        ) : (
          <div className="space-y-1.5">
            {buys.map((b) => (
              <div key={b.symbol} className="bg-dark-light rounded-lg p-2.5 flex items-center justify-between gap-2 flex-wrap">
                <div className="flex items-center gap-2 min-w-0">
                  <span className="text-white font-medium truncate">{b.name}</span>
                  <span className="text-gray-500 text-[10px]">{b.symbol}</span>
                  <span className="px-1.5 py-0.5 rounded border text-[10px] bg-green-500/20 text-green-300 border-green-500/40">买入</span>
                </div>
                <div className="flex items-center gap-3 text-[11px] text-gray-400">
                  <span>综合 <span className="text-white font-semibold">{b.composite?.toFixed?.(0) ?? '—'}</span></span>
                  <span>现价 <span className="text-gray-200">{b.price ?? '—'}</span></span>
                  <span className="text-green-300">{b.suggested?.lots}手/{yuan(b.suggested?.amount)}</span>
                  {b.stop_loss && <span>止损 {b.stop_loss}</span>}
                </div>
              </div>
            ))}
          </div>
        )}
      </div>

      {/* 持仓处置建议 */}
      {holdings.length > 0 && (
        <div className="mb-3">
          <div className="text-xs font-semibold text-gray-400 mb-1.5">持仓处置建议（{holdings.length}）</div>
          <div className="space-y-1.5">
            {holdings.map((h) => {
              const rec = REC_STYLE[h.recommendation] || REC_STYLE['N/A'];
              const pnl = h.unrealized_pnl_pct;
              return (
                <div key={h.symbol} className="bg-dark-light rounded-lg p-2.5 flex items-center justify-between gap-2 flex-wrap">
                  <div className="flex items-center gap-2 min-w-0">
                    <span className="text-white font-medium truncate">{h.name}</span>
                    <span className="text-gray-500 text-[10px]">{h.symbol}</span>
                    <span className={`px-1.5 py-0.5 rounded border text-[10px] ${rec.cls}`}>{rec.label}</span>
                  </div>
                  <div className="flex items-center gap-3 text-[11px] text-gray-400">
                    <span>综合 <span className="text-white font-semibold">{h.composite?.toFixed?.(0) ?? '—'}</span></span>
                    {typeof pnl === 'number' && (
                      <span className={pnl >= 0 ? 'text-green-300' : 'text-red-300'}>
                        浮盈 {pnl >= 0 ? '+' : ''}{pnl.toFixed(1)}%
                      </span>
                    )}
                    {h.stop_loss && <span>止损 {h.stop_loss}</span>}
                  </div>
                </div>
              );
            })}
          </div>
        </div>
      )}

      {/* 买不起（透明剔除） */}
      {skipped.length > 0 && (
        <details className="mb-2">
          <summary className="text-xs text-gray-500 cursor-pointer">按账户买不起，已剔除（{skipped.length}）</summary>
          <div className="mt-1.5 space-y-1">
            {skipped.map((s) => (
              <div key={s.symbol} className="text-[11px] text-gray-500 flex justify-between gap-2">
                <span>{s.name} <span className="text-gray-600">{s.symbol}</span></span>
                <span>现价 {s.price} · 一手 {yuan(s.one_lot_cost)}</span>
              </div>
            ))}
          </div>
        </details>
      )}

      {/* 结论 */}
      {data.note && (
        <div className="text-[11px] text-gray-400 bg-dark rounded-lg p-2.5 leading-relaxed">{data.note}</div>
      )}
    </WidgetShell>
  );
};

const SOURCE_LABELS: Record<string, string> = {
  strong: '强势股池',
  continuation: '昨涨停仍强势',
  quasi: '准涨停扫描',
};

/** 涨停池复盘：家数/连板梯队/炸板率/赚钱效应 + 连板榜（参考数据，非买入候选） */
const LimitUpPoolWidget: React.FC<{ data: any; title?: string }> = ({ data, title }) => {
  const ladder: Record<string, number> = data.ladder_distribution || {};
  const ladderKeys = Object.keys(ladder).sort((a, b) => parseInt(a) - parseInt(b));
  const profit = data.profit_effect || {};
  const boards: any[] = data.top_boards || [];

  return (
    <WidgetShell className="p-4">
      <div className="flex items-center justify-between flex-wrap gap-2 mb-3">
        <div className="text-base font-bold text-white flex items-center gap-2">
          🔥 {title || '涨停池全览'}
          {data.trade_date && <span className="text-[10px] text-gray-500 font-normal">{data.trade_date}</span>}
        </div>
      </div>

      <div className="flex flex-wrap gap-x-4 gap-y-1 text-[11px] text-gray-500 mb-3">
        <span>涨停 <span className="text-bull font-semibold">{data.limit_up_count ?? '—'}</span> 家</span>
        <span>炸板 <span className="text-gray-300">{data.break_count ?? '—'}</span> 家</span>
        {typeof data.break_rate === 'number' && (
          <span>炸板率 <span className="text-gray-300">{(data.break_rate * 100).toFixed(0)}%</span></span>
        )}
        {typeof profit.avg_change_pct === 'number' && (
          <span>
            昨涨停股今日平均{' '}
            <span className={profit.avg_change_pct >= 0 ? 'text-bull' : 'text-bear'}>
              {profit.avg_change_pct >= 0 ? '+' : ''}{profit.avg_change_pct.toFixed(1)}%
            </span>
          </span>
        )}
        {typeof profit.promotion_count === 'number' && <span>晋级(再涨停) {profit.promotion_count} 家</span>}
      </div>

      {ladderKeys.length > 0 && (
        <div className="mb-3">
          <div className="text-xs font-semibold text-gray-400 mb-1.5">连板梯队分布</div>
          <div className="flex flex-wrap gap-2">
            {ladderKeys.map((k) => (
              <span key={k} className="px-2 py-1 rounded-lg bg-dark-light text-[11px] text-gray-300">
                {k} <span className="text-white font-semibold">{ladder[k]}</span>
              </span>
            ))}
          </div>
        </div>
      )}

      {boards.length > 0 && (
        <div>
          <div className="text-xs font-semibold text-gray-400 mb-1.5">连板榜（{boards.length}）</div>
          <div className="space-y-1.5">
            {boards.slice(0, 10).map((b) => (
              <div key={b.symbol} className="bg-dark-light rounded-lg p-2.5 flex items-center justify-between gap-2 flex-wrap">
                <div className="flex items-center gap-2 min-w-0">
                  <span className="text-white font-medium truncate">{b.name}</span>
                  <span className="text-gray-500 text-[10px]">{b.symbol}</span>
                  {b.industry && <span className="text-[10px] text-gray-500">{b.industry}</span>}
                </div>
                <div className="flex items-center gap-3 text-[11px] text-gray-400">
                  <span className="text-bull font-semibold">{b.consecutive_boards ?? '—'}板</span>
                  {b.zt_stat && <span>{b.zt_stat}</span>}
                </div>
              </div>
            ))}
          </div>
        </div>
      )}

      <div className="mt-3 text-[10px] text-gray-500">已封板涨停股买不进，仅作复盘参考，不是买入候选</div>
    </WidgetShell>
  );
};

/** 次日涨停候选池：打分排名 + 归因，候选只包含当前能买的票 */
const LimitUpCandidatesWidget: React.FC<{ data: any; title?: string }> = ({ data, title }) => {
  const candidates: any[] = data.candidates || [];

  return (
    <WidgetShell className="p-4">
      <div className="flex items-center justify-between flex-wrap gap-2 mb-3">
        <div className="text-base font-bold text-white flex items-center gap-2">
          🎯 {title || '次日涨停候选池'}
          {data.target_date && <span className="text-[10px] text-gray-500 font-normal">目标日 {data.target_date}</span>}
        </div>
      </div>

      {candidates.length === 0 ? (
        <div className="text-xs text-gray-500 bg-dark-light rounded-lg p-2.5">今日无达标候选，建议观望</div>
      ) : (
        <div className="space-y-1.5 mb-2">
          {candidates.map((c) => (
            <details key={c.symbol} className="bg-dark-light rounded-lg p-2.5">
              <summary className="cursor-pointer flex items-center justify-between gap-2 flex-wrap list-none">
                <div className="flex items-center gap-2 min-w-0">
                  <span className="text-gray-500 text-[10px] shrink-0">#{c.rank}</span>
                  <span className="text-white font-medium truncate">{c.name}</span>
                  <span className="text-gray-500 text-[10px]">{c.symbol}</span>
                  <span className="px-1.5 py-0.5 rounded border text-[10px] bg-blue-500/15 text-blue-300 border-blue-500/30">
                    {SOURCE_LABELS[c.source] || c.source}
                  </span>
                </div>
                <span className="text-[11px] text-gray-400">
                  打分 <span className="text-white font-semibold">{(c.score * 100).toFixed(0)}</span>
                </span>
              </summary>
              <div className="mt-2 space-y-1">
                {(c.reasons || []).map((r: any, i: number) => (
                  <div key={i} className="text-[11px] text-gray-400">
                    <span className="text-gray-300">{r.indicator}</span>：{r.detail}
                  </div>
                ))}
              </div>
            </details>
          ))}
        </div>
      )}

      <div className="text-[11px] text-gray-400 bg-dark rounded-lg p-2.5 leading-relaxed">
        ⚠️ {data.disclaimer || '预测基于历史规律统计，非100%准确，仅供参考，不构成投资建议'}
      </div>
    </WidgetShell>
  );
};

/* ================================================================
   加密货币专属 widget（crypto 计价 USDT，用 $ 格式化）
   ================================================================ */

/** 紧凑美元格式：$1.23T / $45.6B / $789M / $12.3K / $5.20 */
const usd = (v: any, decimals = 2): string => {
  const n = Number(v);
  if (!Number.isFinite(n)) return '—';
  const abs = Math.abs(n);
  if (abs >= 1e12) return `$${(n / 1e12).toFixed(2)}T`;
  if (abs >= 1e9) return `$${(n / 1e9).toFixed(2)}B`;
  if (abs >= 1e6) return `$${(n / 1e6).toFixed(2)}M`;
  if (abs >= 1e3) return `$${(n / 1e3).toFixed(2)}K`;
  // 小币价格自适应小数位
  const dp = abs >= 1 ? decimals : abs >= 0.01 ? 6 : 8;
  return `$${n.toFixed(dp)}`;
};

/** 恐慌贪婪半环仪表（0=极度恐慌红，100=极度贪婪绿） */
const FearGreedGauge: React.FC<{ value: number; label?: string }> = ({ value, label }) => {
  const reduce = useReducedMotion();
  const v = Math.max(0, Math.min(100, value));
  const r = 46;
  const cx = 60;
  const cy = 56;
  const semi = Math.PI * r; // 半圆弧长
  // 颜色：<25 深红 / <45 橙 / <55 黄 / <75 浅绿 / 其余 绿
  const color = v < 25 ? '#EF4444' : v < 45 ? '#F59E0B' : v < 55 ? '#EAB308' : v < 75 ? '#84CC16' : '#10B981';
  return (
    <svg width="120" height="72" viewBox="0 0 120 72" className="shrink-0">
      {/* 底弧 */}
      <path d={`M ${cx - r} ${cy} A ${r} ${r} 0 0 1 ${cx + r} ${cy}`} fill="none" stroke="#1E293B" strokeWidth="9" strokeLinecap="round" />
      {/* 前景弧 */}
      <motion.path
        d={`M ${cx - r} ${cy} A ${r} ${r} 0 0 1 ${cx + r} ${cy}`}
        fill="none" stroke={color} strokeWidth="9" strokeLinecap="round"
        strokeDasharray={semi}
        initial={reduce ? false : { strokeDashoffset: semi }}
        animate={{ strokeDashoffset: semi * (1 - v / 100) }}
        transition={reduce ? undefined : { type: 'spring', stiffness: 90, damping: 20, delay: 0.1 }}
        style={{ filter: `drop-shadow(0 0 5px ${color}88)` }}
      />
      <text x={cx} y={cy - 8} textAnchor="middle" className="fill-white font-bold" fontSize="22">{Math.round(v)}</text>
      {label && <text x={cx} y={cy + 10} textAnchor="middle" className="fill-gray-400" fontSize="10">{label}</text>}
    </svg>
  );
};

const REGIME_STYLE: Record<string, { label: string; cls: string }> = {
  bull: { label: '🐂 牛市', cls: 'bg-green-500/20 text-green-300 border-green-500/40' },
  bear: { label: '🐻 熊市', cls: 'bg-red-500/20 text-red-300 border-red-500/40' },
  unknown: { label: '大势未知', cls: 'bg-gray-500/20 text-gray-300 border-gray-500/40' },
};

const CryptoMarketWidget: React.FC<{ data: any; title?: string }> = ({ data, title }) => {
  const fg = data.fear_greed;
  const reg = REGIME_STYLE[data.regime] || REGIME_STYLE.unknown;
  const pctFromMa = typeof data.pct_from_ma === 'number' ? data.pct_from_ma : null;
  return (
    <WidgetShell className="p-4">
      <div className="text-base font-bold text-white mb-3">🪙 {title || '加密市场大势'}</div>
      <div className="flex items-center gap-4 flex-wrap">
        {fg && typeof fg.value === 'number' ? (
          <div className="text-center">
            <FearGreedGauge value={fg.value} label={fg.label} />
            <div className="text-[10px] text-gray-500 mt-0.5">恐慌贪婪指数</div>
          </div>
        ) : (
          <div className="text-xs text-gray-500">恐慌贪婪指数 —</div>
        )}
        <div className="flex-1 min-w-0 grid grid-cols-2 gap-2 text-sm">
          <div className="bg-dark-light rounded-lg p-2.5">
            <div className="text-gray-500 text-xs">BTC 主导率</div>
            <div className="text-white font-medium">
              {typeof data.btc_dominance === 'number' ? `${data.btc_dominance.toFixed(1)}%` : '—'}
            </div>
          </div>
          <div className="bg-dark-light rounded-lg p-2.5">
            <div className="text-gray-500 text-xs">总市值</div>
            <div className="text-white font-medium">{usd(data.total_market_cap_usd)}</div>
          </div>
        </div>
      </div>
      {/* BTC 大势 */}
      <div className="mt-3 flex items-center justify-between flex-wrap gap-2 bg-dark-light rounded-lg p-2.5">
        <span className={`px-2.5 py-1 rounded-lg border text-xs font-semibold ${reg.cls}`}>{reg.label}</span>
        <div className="flex items-center gap-3 text-[11px] text-gray-400">
          <span>BTC {usd(data.btc_price)}</span>
          <span>MA200 {usd(data.btc_ma200)}</span>
          {pctFromMa !== null && (
            <span className={pctFromMa >= 0 ? 'text-green-300' : 'text-red-300'}>
              距MA200 {pctFromMa >= 0 ? '+' : ''}{pctFromMa.toFixed(1)}%
            </span>
          )}
        </div>
      </div>
    </WidgetShell>
  );
};

const CRYPTO_VERDICT: Record<string, { label: string; cls: string }> = {
  pass: { label: '可碰', cls: 'bg-green-500/20 text-green-300 border-green-500/40' },
  caution: { label: '谨慎', cls: 'bg-yellow-500/20 text-yellow-300 border-yellow-500/40' },
  avoid: { label: '回避', cls: 'bg-red-500/20 text-red-300 border-red-500/40' },
  unknown: { label: '数据不足', cls: 'bg-gray-500/20 text-gray-300 border-gray-500/40' },
};

const CryptoScreenWidget: React.FC<{ data: any; title?: string }> = ({ data, title }) => {
  const vd = CRYPTO_VERDICT[data.verdict] || CRYPTO_VERDICT.unknown;
  const dims = data.dimensions || {};
  const flags: string[] = data.flags || [];
  const scoreCls = data.score >= 65 ? 'text-green-300' : data.score >= 45 ? 'text-yellow-300' : 'text-red-300';
  return (
    <WidgetShell className="p-4">
      <div className="flex items-center justify-between flex-wrap gap-2 mb-3">
        <div className="text-base font-bold text-white">
          🛡️ {title || '排雷体检'} <span className="text-gray-500 text-xs">{data.symbol}</span>
        </div>
        <div className="flex items-center gap-3">
          <div className="text-center">
            <div className={`text-2xl font-bold leading-none ${scoreCls}`}>
              <AnimatedNumber value={typeof data.score === 'number' ? data.score : null} decimals={0} placeholder="—" />
            </div>
            <div className="text-[10px] text-gray-500">排雷分</div>
          </div>
          <span className={`px-3 py-1.5 rounded-lg border text-xs font-semibold ${vd.cls}`}>{vd.label}</span>
        </div>
      </div>

      {flags.length > 0 && (
        <div className="mb-3 space-y-1">
          {flags.map((f, i) => (
            <div key={i} className="text-[11px] text-red-300 bg-red-500/10 border border-red-500/20 rounded-lg px-2.5 py-1.5">🚩 {f}</div>
          ))}
        </div>
      )}

      <div className="grid grid-cols-2 md:grid-cols-4 gap-2 text-sm">
        <div className="bg-dark-light rounded-lg p-2.5">
          <div className="text-gray-500 text-xs">市值</div>
          <div className="text-white font-medium">{usd(dims.market_cap_usd)}</div>
        </div>
        <div className="bg-dark-light rounded-lg p-2.5">
          <div className="text-gray-500 text-xs">FDV/市值</div>
          <div className="text-white font-medium">
            {typeof dims.fdv_mcap_ratio === 'number' ? `${dims.fdv_mcap_ratio.toFixed(2)}x` : '—'}
          </div>
        </div>
        <div className="bg-dark-light rounded-lg p-2.5">
          <div className="text-gray-500 text-xs">4周提交</div>
          <div className="text-white font-medium">{dims.commits_4w ?? '—'}</div>
        </div>
        <div className="bg-dark-light rounded-lg p-2.5">
          <div className="text-gray-500 text-xs">供应上限</div>
          <div className="text-white font-medium">{dims.max_supply ? Number(dims.max_supply).toLocaleString() : '无上限'}</div>
        </div>
      </div>
      {data.ambiguous && (
        <div className="mt-2 text-[10px] text-yellow-400">⚠️ 代币标识经回退解析，可能有歧义，仅供参考</div>
      )}
    </WidgetShell>
  );
};

const CryptoDerivativesWidget: React.FC<{ data: any; title?: string }> = ({ data, title }) => {
  const f = data.funding;
  const oi = data.open_interest;
  const ls = data.long_short;
  const fr = f && typeof f.funding_rate === 'number' ? f.funding_rate : null;
  const frPct = fr !== null ? fr * 100 : null;
  // long_pct/short_pct 是小数（0.58），转成百分数展示
  const longPct = ls && typeof ls.long_pct === 'number' ? ls.long_pct * 100 : null;
  const shortPct = ls && typeof ls.short_pct === 'number' ? ls.short_pct * 100 : null;
  return (
    <WidgetShell className="p-4">
      <div className="text-base font-bold text-white mb-3">
        📡 {title || '衍生品情绪'} <span className="text-gray-500 text-xs">{data.symbol}</span>
      </div>
      <div className="grid grid-cols-2 gap-2 text-sm mb-3">
        <div className="bg-dark-light rounded-lg p-2.5">
          <div className="text-gray-500 text-xs">资金费率</div>
          {/* 正=多头付空头（偏多拥挤），染红；负染绿 */}
          <div className={`font-semibold ${frPct === null ? 'text-gray-300' : frPct >= 0 ? 'text-bull' : 'text-bear'}`}>
            {frPct === null ? '—' : `${frPct >= 0 ? '+' : ''}${frPct.toFixed(4)}%`}
          </div>
        </div>
        <div className="bg-dark-light rounded-lg p-2.5">
          <div className="text-gray-500 text-xs">未平仓 OI</div>
          <div className="text-white font-medium">
            {oi && oi.oi != null ? Number(oi.oi).toLocaleString(undefined, { maximumFractionDigits: 0 }) : '—'}
            {oi && oi.oi_value != null && <span className="text-gray-500 text-xs"> · {usd(oi.oi_value)}</span>}
          </div>
        </div>
      </div>
      {/* 多空持仓比 */}
      <div className="bg-dark-light rounded-lg p-2.5">
        <div className="flex justify-between text-xs mb-1.5">
          <span className="text-green-300">多 {longPct !== null ? `${longPct.toFixed(1)}%` : '—'}</span>
          <span className="text-gray-400">多空比 {ls && typeof ls.ratio === 'number' ? ls.ratio.toFixed(2) : '—'}</span>
          <span className="text-red-300">空 {shortPct !== null ? `${shortPct.toFixed(1)}%` : '—'}</span>
        </div>
        <div className="h-2.5 rounded-full overflow-hidden flex bg-dark">
          <div className="h-full bg-green-500" style={{ width: `${longPct ?? 50}%` }} />
          <div className="h-full bg-red-500" style={{ width: `${shortPct ?? 50}%` }} />
        </div>
      </div>
    </WidgetShell>
  );
};

const CryptoAccountWidget: React.FC<{ data: any; title?: string }> = ({ data, title }) => {
  const rows = data.positions || [];
  const wallets = Array.isArray(data.wallets) ? data.wallets : [];
  return (
    <WidgetShell className="p-4">
      <div className="text-base font-bold text-white mb-3">💰 {title || '币安账户'}</div>
      {/* 三档买力：可动用买力（现货可用 + 理财活期可赎 + 资金可划） + 总资产 */}
      <div className="grid grid-cols-2 gap-2 text-sm mb-3">
        <div className="bg-dark-light rounded-lg p-2.5">
          <div className="text-gray-500 text-xs">可动用买力</div>
          <div className="text-white font-medium">{usd(data.cash)}</div>
          <div className="text-[10px] text-gray-600 mt-0.5">
            现货 {usd(data.spot_cash)} · 活期可赎 {usd(data.redeemable_cash)} · 资金可划 {usd(data.transferable_cash)}
          </div>
        </div>
        <div className="bg-dark-light rounded-lg p-2.5">
          <div className="text-gray-500 text-xs">总资产</div>
          <div className="text-white font-medium">{usd(data.total_value)}</div>
          <div className="text-[10px] text-gray-600 mt-0.5">含持仓市值 {usd(data.market_value)}</div>
        </div>
      </div>
      {/* 四钱包分列 */}
      {wallets.length > 0 && (
        <div className="grid grid-cols-2 md:grid-cols-4 gap-2 text-xs mb-3">
          {wallets.map((w: any, i: number) => (
            <div key={i} className="bg-dark-light rounded-lg p-2">
              <div className="text-gray-500">{w.name}</div>
              <div className="text-white font-medium">{usd(w.stable)}</div>
              {w.coins_value > 0 && <div className="text-[10px] text-gray-600">币值 {usd(w.coins_value)}</div>}
            </div>
          ))}
        </div>
      )}
      {rows.length === 0 ? (
        <div className="text-xs text-gray-500 bg-dark-light rounded-lg p-2.5">当前无持仓</div>
      ) : (
        <table className="w-full text-sm">
          <thead className="bg-dark-light border-b border-border">
            <tr>
              <th className="px-3 py-2 text-left text-xs text-gray-500">币种</th>
              <th className="px-3 py-2 text-right text-xs text-gray-500">数量</th>
              <th className="px-3 py-2 text-right text-xs text-gray-500">现价</th>
              <th className="px-3 py-2 text-right text-xs text-gray-500">市值</th>
            </tr>
          </thead>
          <tbody className="divide-y divide-border">
            {rows.map((r: any, i: number) => (
              <tr key={i} className="hover:bg-dark-light/50">
                <td className="px-3 py-2 text-gray-300">{r.symbol}</td>
                <td className="px-3 py-2 text-right text-gray-300">{Number(r.quantity).toLocaleString(undefined, { maximumFractionDigits: 8 })}</td>
                <td className="px-3 py-2 text-right text-gray-300">{usd(r.current_price)}</td>
                <td className="px-3 py-2 text-right text-gray-300">{usd(r.market_value)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </WidgetShell>
  );
};

const CryptoAnalysisWidget: React.FC<{ data: any; title?: string }> = ({ data, title }) => {
  const rec = REC_STYLE[data.recommendation] || REC_STYLE['N/A'];
  const dims = data.dimensions || {};
  const sug = data.suggested;
  const price = data.price || {};
  const lv = data.dynamic_levels || {};
  const screen = data.screen || {};
  const sv = SCREEN_VERDICT_STYLE[screen.verdict] || SCREEN_VERDICT_STYLE['unknown'];
  const chg = typeof price.change_20d_pct === 'number' ? price.change_20d_pct : null;
  return (
    <WidgetShell className="p-4">
      {/* 头部：币种 + 综合分 + 买卖持有 + 排雷徽章 */}
      <div className="flex items-center justify-between flex-wrap gap-2 mb-3">
        <div className="text-base font-bold text-white">
          {title || data.base_asset} <span className="text-gray-500 text-xs">{data.symbol}</span>
        </div>
        <div className="flex items-center gap-3">
          <div className="text-center">
            <div className="text-2xl font-bold text-white leading-none">
              <AnimatedNumber value={data.composite} decimals={1} placeholder="—" />
            </div>
            <div className="text-[10px] text-gray-500">综合评分</div>
          </div>
          <span className={`px-3 py-1.5 rounded-lg border text-xs font-semibold ${rec.cls}`}>{rec.label}</span>
          <span className={`px-2 py-1 rounded-lg border text-[10px] font-semibold ${sv.cls}`}>{sv.label}</span>
        </div>
      </div>

      {/* 关键指标：现价 / 建议仓位 / 止损 / 止盈 */}
      <div className="grid grid-cols-2 md:grid-cols-4 gap-2 mb-3 text-sm">
        <div className="bg-dark-light rounded-lg p-2.5">
          <div className="text-gray-500 text-xs">现价{chg !== null && <span className={chg >= 0 ? 'text-bull' : 'text-bear'}> {chg >= 0 ? '+' : ''}{chg}%(20d)</span>}</div>
          <div className="text-white font-medium">{usd(price.latest)}</div>
        </div>
        <div className="bg-dark-light rounded-lg p-2.5">
          <div className="text-gray-500 text-xs">建议仓位</div>
          <div className="text-white font-medium">{data.suggested_position_pct ?? 0}%</div>
        </div>
        <div className="bg-dark-light rounded-lg p-2.5">
          <div className="text-gray-500 text-xs">止损位{lv.sl_atr_mult ? <span className="text-gray-600"> {lv.sl_atr_mult}×ATR</span> : null}</div>
          <div className="text-white font-medium">{usd(data.stop_loss)}</div>
        </div>
        <div className="bg-dark-light rounded-lg p-2.5">
          <div className="text-gray-500 text-xs">止盈位{typeof lv.risk_reward_ratio === 'number' ? <span className="text-gray-600"> 盈亏比{lv.risk_reward_ratio}</span> : null}</div>
          <div className="text-white font-medium">{usd(data.take_profit)}</div>
        </div>
      </div>

      {/* 择时三维评分 */}
      <div className="bg-dark-light rounded-lg p-3 mb-3">
        <div className="text-xs font-semibold text-gray-400 mb-2">择时三维（排雷作否决闸）</div>
        {(['technical', 'derivatives', 'regime'] as const).map((k) => (
          <DimBar key={k} name={k} score={dims[k]?.score} />
        ))}
      </div>

      {/* 一句解释原因 */}
      {Array.isArray(data.reasons) && data.reasons.length > 0 && (
        <div className="bg-dark-light rounded-lg p-3 mb-3 text-xs text-gray-300 space-y-1">
          {data.reasons.map((rsn: string, i: number) => (
            <div key={i}>· {rsn}</div>
          ))}
        </div>
      )}

      {/* 排雷红旗 */}
      {Array.isArray(screen.flags) && screen.flags.length > 0 && screen.verdict !== 'pass' && (
        <div className="text-[11px] text-yellow-300/80 mb-2">🚩 {screen.flags.slice(0, 3).join('；')}</div>
      )}

      {/* 只有真判 BUY 且买得起才显示建议买入量 */}
      {data.recommendation === 'BUY' && sug && sug.affordable && (
        <div className="bg-dark-light rounded-lg p-3 text-sm flex flex-wrap gap-x-6 gap-y-1">
          <span className="text-gray-500 text-xs">💰 建议买入 <span className="text-green-300 font-semibold">{Number(sug.quantity).toLocaleString(undefined, { maximumFractionDigits: 8 })} {data.base_asset}</span></span>
          <span className="text-gray-500 text-xs">约 <span className="text-white">{usd(sug.amount_usdt)}</span></span>
          {!sug.risk_passed && <span className="text-red-300 text-xs">⚠️ 风控未过</span>}
        </div>
      )}
    </WidgetShell>
  );
};

const WIDGET_MAP: Record<string, React.FC<{ data: any; title?: string }>> = {
  cockpit_score: CockpitScoreWidget,
  crypto_market: CryptoMarketWidget,
  crypto_screen: CryptoScreenWidget,
  crypto_derivatives: CryptoDerivativesWidget,
  crypto_analysis: CryptoAnalysisWidget,
  crypto_account: CryptoAccountWidget,
  metric_cards: MetricCardsWidget,
  position_table: PositionTableWidget,
  prediction: PredictionWidget,
  price_quote: QuoteWidget,
  price_sparkline: SparklineWidget,
  knowledge_sources: KnowledgeSourcesWidget,
  recommendation_board: RecommendationBoardWidget,
  limit_up_pool: LimitUpPoolWidget,
  limit_up_candidates: LimitUpCandidatesWidget,
};

export const WidgetRenderer: React.FC<{ widget: WidgetSpec }> = ({ widget }) => {
  const Comp = WIDGET_MAP[widget.type];
  if (!Comp) {
    return (
      <details className="text-xs bg-dark rounded-lg border border-border p-2 my-2">
        <summary className="cursor-pointer text-gray-400">📊 {widget.title || widget.type}</summary>
        <pre className="mt-2 overflow-x-auto text-gray-500">{JSON.stringify(widget.data, null, 2)}</pre>
      </details>
    );
  }
  return <Comp data={widget.data} title={widget.title} />;
};

export default WidgetRenderer;

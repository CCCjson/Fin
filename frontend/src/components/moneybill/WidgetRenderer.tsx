import React from 'react';
import { motion, useReducedMotion } from 'framer-motion';
import {
  AreaChart, Area, ResponsiveContainer, YAxis, Tooltip,
} from 'recharts';
import type { WidgetSpec } from '../../services/agentService';
import { MetricCard } from '../backtest/MetricCard';
import { AnimatedNumber } from '../common/AnimatedNumber';
import { fadeUp, pickVariants } from '../common/motion';

/* ================================================================
   Widget 分发器：按 type 渲染成图表/卡片（复用现成组件）
   ================================================================ */

const DIM_LABELS: Record<string, string> = {
  technical: '技术面',
  fundamental: '基本面',
  sentiment: '新闻情感',
  ml: 'ML 预测',
  position: '持仓风险',
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
              <AnimatedNumber value={data.composite} decimals={0} placeholder="—" />
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

      {/* 按资金建议 */}
      {sug && sug.affordable && (
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
                     className="text-sm text-gray-200 hover:text-blue-300 truncate font-medium">{s.title}</a>
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

const WIDGET_MAP: Record<string, React.FC<{ data: any; title?: string }>> = {
  cockpit_score: CockpitScoreWidget,
  metric_cards: MetricCardsWidget,
  position_table: PositionTableWidget,
  prediction: PredictionWidget,
  price_quote: QuoteWidget,
  price_sparkline: SparklineWidget,
  knowledge_sources: KnowledgeSourcesWidget,
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

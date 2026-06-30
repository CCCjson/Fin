import React, { useState, useEffect } from 'react';
import { useNavigate } from 'react-router-dom';
import { screenerService } from '../services/screenerService';
import type { ScreenerField, ScreenFilter, ScreenRow } from '../services/screenerService';
import { portfolioService } from '../services/portfolioService';

const OP_LABEL: Record<string, string> = {
  gt: '>', gte: '≥', lt: '<', lte: '≤', eq: '=', between: '区间',
};

const POOLS = [
  { id: '', label: '全市场' },
  { id: 'sse50', label: '上证50' },
  { id: 'csi300', label: '沪深300' },
  { id: 'csi500', label: '中证500' },
];

interface FilterRow extends ScreenFilter {}

export const Screener: React.FC = () => {
  const nav = useNavigate();
  const [fields, setFields] = useState<ScreenerField[]>([]);
  const [filters, setFilters] = useState<FilterRow[]>([{ field: 'roe', op: 'gt', value: 15 }]);
  const [poolId, setPoolId] = useState('');
  const [sortBy, setSortBy] = useState('roe');
  const [rows, setRows] = useState<ScreenRow[]>([]);
  const [count, setCount] = useState<number | null>(null);
  const [affordableOnly, setAffordableOnly] = useState(true);
  const [excludeSt, setExcludeSt] = useState(true);
  const [mainBoardOnly, setMainBoardOnly] = useState(true);
  const [totalCapital, setTotalCapital] = useState<number | null>(null);
  const [appliedPct, setAppliedPct] = useState<number | null>(null); // 本次筛选生效的单股上限
  const [maxPctInput, setMaxPctInput] = useState('50'); // 单股上限 %（用户自由输入）
  const [savingPct, setSavingPct] = useState(false);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState('');
  const [refreshMsg, setRefreshMsg] = useState('');
  const [refreshing, setRefreshing] = useState(false);

  useEffect(() => {
    screenerService.getFields().then((r) => setFields(r.fields)).catch(() => {});
    portfolioService.getSettings()
      .then((s: any) => {
        const v = parseFloat(s?.max_position_pct?.value);
        if (!Number.isNaN(v)) setMaxPctInput(String(Math.round(v * 100)));
      })
      .catch(() => {});
  }, []);

  // 保存集中度（单股上限 %）后重跑筛选
  const saveMaxPct = async () => {
    const pct = parseFloat(maxPctInput);
    if (Number.isNaN(pct) || pct <= 0 || pct > 100) { setError('单股上限请填 1~100 的数字'); return; }
    setSavingPct(true); setError('');
    try {
      await portfolioService.updateSetting('max_position_pct', String(pct / 100));
      await run();
    } catch (e: any) {
      setError(e?.message || '保存集中度失败');
    } finally {
      setSavingPct(false);
    }
  };

  const fieldLabel = (f: string) => fields.find((x) => x.field === f)?.label || f;

  const addFilter = () => setFilters((fs) => [...fs, { field: fields[0]?.field || 'pe', op: 'lt', value: 0 }]);
  const updateFilter = (i: number, patch: Partial<FilterRow>) =>
    setFilters((fs) => fs.map((f, idx) => (idx === i ? { ...f, ...patch } : f)));
  const removeFilter = (i: number) => setFilters((fs) => fs.filter((_, idx) => idx !== i));

  const run = async () => {
    setLoading(true); setError(''); setCount(null);
    try {
      const r = await screenerService.run({
        filters: filters.map((f) => ({ ...f, value: Number(f.value) })),
        pool_id: poolId || undefined,
        sort_by: sortBy,
        sort_desc: true,
        limit: 100,
        affordable_only: affordableOnly,
        exclude_st: excludeSt,
        main_board_only: mainBoardOnly,
      });
      setRows(r.data); setCount(r.count); setTotalCapital(r.total_capital); setAppliedPct(r.max_position_pct);
    } catch (e: any) {
      setError(e?.response?.data?.error || e?.message || '筛选失败');
    } finally {
      setLoading(false);
    }
  };

  const refreshValuation = async () => {
    setRefreshing(true); setRefreshMsg('开始刷新全市场估值（约数分钟）…');
    try {
      await screenerService.refreshValuation((ev) => {
        if (ev.event === 'done') setRefreshMsg(`估值刷新完成：${JSON.stringify(ev.result)}`);
        else if (ev.event === 'error') setRefreshMsg(`刷新失败：${ev.message}`);
        else if (ev.message) setRefreshMsg(ev.message);
      });
    } catch (e: any) {
      setRefreshMsg(`刷新失败：${e?.message}`);
    } finally {
      setRefreshing(false);
    }
  };

  const fmt = (v: any) => (v == null ? '—' : typeof v === 'number' ? (Math.abs(v) >= 1e8 ? `${(v / 1e8).toFixed(1)}亿` : v.toFixed(2)) : v);
  const displayCols = ['roe', 'net_margin', 'revenue_yoy', 'pe', 'pb', 'total_mv'];

  return (
    <div className="p-4 md:p-6 max-w-6xl mx-auto">
      <div className="flex items-center justify-between mb-4">
        <div>
          <h1 className="text-xl font-bold text-white">🔎 基本面选股器</h1>
          <p className="text-gray-500 text-sm">按 ROE / PE / 增长率等因子筛全市场（估值数据需先刷新）</p>
        </div>
        <button onClick={refreshValuation} disabled={refreshing} className="px-3 py-2 rounded-lg bg-dark-light text-gray-300 text-xs hover:bg-dark-light/70 disabled:opacity-50">
          {refreshing ? '刷新估值中…' : '⟳ 刷新全市场估值'}
        </button>
      </div>
      {refreshMsg && <div className="mb-3 text-xs text-gray-400">{refreshMsg}</div>}

      {/* 条件构造器 */}
      <div className="bg-dark-card border border-border rounded-xl p-4 mb-4">
        <div className="flex items-center gap-3 mb-3 flex-wrap">
          <label className="text-sm text-gray-400">范围</label>
          <select value={poolId} onChange={(e) => setPoolId(e.target.value)} className="px-3 py-1.5 rounded-lg bg-dark-light border border-border text-white text-sm">
            {POOLS.map((p) => <option key={p.id} value={p.id}>{p.label}</option>)}
          </select>
          <label className="text-sm text-gray-400 ml-2">排序</label>
          <select value={sortBy} onChange={(e) => setSortBy(e.target.value)} className="px-3 py-1.5 rounded-lg bg-dark-light border border-border text-white text-sm">
            {fields.map((f) => <option key={f.field} value={f.field}>{f.label}</option>)}
          </select>
          <label className="flex items-center gap-1.5 text-sm text-gray-400 ml-2 cursor-pointer select-none">
            <input type="checkbox" checked={affordableOnly} onChange={(e) => setAffordableOnly(e.target.checked)} className="accent-primary" />
            只看买得起的
          </label>
          <label className="flex items-center gap-1.5 text-sm text-gray-400 ml-2 cursor-pointer select-none">
            <input type="checkbox" checked={excludeSt} onChange={(e) => setExcludeSt(e.target.checked)} className="accent-primary" />
            排除 ST/退市股
          </label>
          <label className="flex items-center gap-1.5 text-sm text-gray-400 ml-2 cursor-pointer select-none" title="排除科创688/创业300/北交所——小资金通常无权限">
            <input type="checkbox" checked={mainBoardOnly} onChange={(e) => setMainBoardOnly(e.target.checked)} className="accent-primary" />
            只看主板(可买)
          </label>
        </div>

        {/* 集中度（单股上限） */}
        <div className="flex items-center gap-2 mb-3 flex-wrap">
          <label className="text-sm text-gray-400">单股上限</label>
          <input
            type="number" min={1} max={100} value={maxPctInput}
            onChange={(e) => setMaxPctInput(e.target.value)}
            className="px-3 py-1.5 rounded-lg bg-dark-light border border-border text-white text-sm w-20"
          />
          <span className="text-gray-500 text-sm">% 总资金</span>
          <button onClick={saveMaxPct} disabled={savingPct} className="px-3 py-1.5 rounded-lg bg-dark-light text-accent-cyan text-xs hover:bg-dark-light/70 disabled:opacity-50">
            {savingPct ? '保存中…' : '应用并重跑'}
          </button>
          <span className="text-gray-600 text-xs">建议 20~50%；高把握单只可 100%(all-in)。会同时作用于驾驶舱建议与下单风控。</span>
        </div>

        {filters.map((f, i) => (
          <div key={i} className="flex items-center gap-2 mb-2">
            <select value={f.field} onChange={(e) => updateFilter(i, { field: e.target.value })} className="px-3 py-1.5 rounded-lg bg-dark-light border border-border text-white text-sm flex-1 max-w-[160px]">
              {fields.map((x) => <option key={x.field} value={x.field}>{x.label}</option>)}
            </select>
            <select value={f.op} onChange={(e) => updateFilter(i, { op: e.target.value as any })} className="px-2 py-1.5 rounded-lg bg-dark-light border border-border text-white text-sm">
              {['gt', 'gte', 'lt', 'lte', 'eq'].map((o) => <option key={o} value={o}>{OP_LABEL[o]}</option>)}
            </select>
            <input type="number" value={f.value as number} onChange={(e) => updateFilter(i, { value: parseFloat(e.target.value) })} className="px-3 py-1.5 rounded-lg bg-dark-light border border-border text-white text-sm w-28" />
            <button onClick={() => removeFilter(i)} className="text-red-400 text-sm px-2">✕</button>
          </div>
        ))}
        <div className="flex items-center gap-3 mt-3">
          <button onClick={addFilter} className="text-accent-cyan text-sm hover:underline">+ 添加条件</button>
          <button onClick={run} disabled={loading} className="ml-auto px-5 py-2 rounded-lg bg-primary text-dark text-sm font-medium hover:bg-primary/80 disabled:opacity-50">
            {loading ? '筛选中…' : '运行筛选'}
          </button>
        </div>
      </div>

      {error && <div className="mb-3 p-3 rounded-lg bg-red-900/40 border border-red-600/40 text-red-200 text-sm">{error}</div>}

      {/* 结果 */}
      {count !== null && (
        <div className="bg-dark-card border border-border rounded-xl overflow-hidden">
          <div className="px-4 py-2.5 text-sm text-gray-400 border-b border-border flex items-center justify-between flex-wrap gap-1">
            <span>命中 {count} 只（点击行进入决策驾驶舱）</span>
            {totalCapital != null && (
              <span className="text-xs text-gray-500">
                按总资金 ¥{totalCapital.toLocaleString()}
                {appliedPct != null ? ` · 单股上限 ${Math.round(appliedPct * 100)}%` : ''}
                {affordableOnly ? ' · 已隐藏买不起的' : ''}
              </span>
            )}
          </div>
          <div className="overflow-x-auto">
            <table className="w-full text-sm">
              <thead className="bg-dark-light text-gray-400 text-xs">
                <tr>
                  <th className="text-left px-4 py-2.5">代码/名称</th>
                  {displayCols.map((c) => <th key={c} className="text-right px-3 py-2.5">{fieldLabel(c)}</th>)}
                  <th className="text-right px-3 py-2.5">现价</th>
                  <th className="text-right px-3 py-2.5">建议买入</th>
                  <th className="text-right px-3 py-2.5">约需金额</th>
                </tr>
              </thead>
              <tbody>
                {rows.length === 0 && <tr><td colSpan={displayCols.length + 4} className="text-center text-gray-600 py-8">无匹配结果（估值类条件需先刷新估值数据；若开了「只看买得起的」，可能是资金不够买任何 1 手）</td></tr>}
                {rows.map((r) => (
                  <tr key={r.symbol} onClick={() => nav(`/app/cockpit?symbol=${encodeURIComponent(r.symbol)}`)} className="border-t border-border/50 hover:bg-dark-light/50 cursor-pointer">
                    <td className="px-4 py-2.5">
                      <div className="text-white">{r.name || '—'}</div>
                      <div className="text-gray-500 text-xs">{r.symbol}</div>
                    </td>
                    {displayCols.map((c) => <td key={c} className="px-3 py-2.5 text-right text-gray-200">{fmt(r[c])}</td>)}
                    <td className="px-3 py-2.5 text-right text-gray-200">{r.price != null ? `¥${fmt(r.price)}` : '—'}</td>
                    <td className="px-3 py-2.5 text-right">
                      {r.suggested?.affordable
                        ? <span className="text-green-300">{r.suggested.shares} 股</span>
                        : <span className="text-red-400 text-xs">买不起</span>}
                    </td>
                    <td className="px-3 py-2.5 text-right text-gray-200">{r.suggested?.affordable ? `¥${r.suggested.amount.toLocaleString()}` : '—'}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      )}
    </div>
  );
};

import React, { useState, useRef, useEffect } from 'react';
import { useLocation } from 'react-router-dom';
import { StockSymbolInput } from '../components/common/StockSymbolInput';
import { cockpitService } from '../services/cockpitService';
import type { CockpitData } from '../services/cockpitService';

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
  if (s === null) return 'bg-gray-600';
  if (s >= 65) return 'bg-green-500';
  if (s >= 45) return 'bg-yellow-500';
  return 'bg-red-500';
};

const DimensionBar: React.FC<{ name: string; dim: { score: number | null; detail: any } }> = ({ name, dim }) => (
  <div className="mb-3">
    <div className="flex justify-between text-xs mb-1">
      <span className="text-gray-300">{DIM_LABELS[name] || name}</span>
      <span className="text-gray-400">{dim.score === null ? '— 数据不足' : dim.score.toFixed(1)}</span>
    </div>
    <div className="h-2 bg-dark-light rounded-full overflow-hidden">
      <div className={`h-full ${scoreColor(dim.score)} transition-all`} style={{ width: `${dim.score ?? 0}%` }} />
    </div>
  </div>
);

export const Cockpit: React.FC = () => {
  const [symbol, setSymbol] = useState('');
  const [data, setData] = useState<CockpitData | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState('');
  const [summary, setSummary] = useState('');
  const [summarizing, setSummarizing] = useState(false);
  const abortRef = useRef<AbortController | null>(null);
  const location = useLocation();

  const analyze = async (sym?: string) => {
    const target = (sym ?? symbol).trim();
    if (!target) return;
    setLoading(true); setError(''); setData(null); setSummary('');
    try {
      const d = await cockpitService.getCockpit(target);
      setData(d);
    } catch (e: any) {
      setError(e?.message || '聚合失败');
    } finally {
      setLoading(false);
    }
  };

  // 支持从选股器跳转：/app/cockpit?symbol=XXX 自动分析
  useEffect(() => {
    const p = new URLSearchParams(location.search).get('symbol');
    if (p && p !== symbol) {
      setSymbol(p);
      analyze(p);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [location.search]);

  const genSummary = async () => {
    if (!data) return;
    setSummarizing(true); setSummary('');
    abortRef.current?.abort();
    const ctrl = new AbortController();
    abortRef.current = ctrl;
    try {
      await cockpitService.streamSummary(data.symbol, (ev) => {
        if (ev.event === 'cockpit' && ev.data) setData(ev.data);
        else if (ev.event === 'chunk' && ev.content) setSummary((s) => s + ev.content);
        else if (ev.event === 'error') setError(ev.message || 'AI 总结失败');
      }, ctrl.signal);
    } catch (e: any) {
      if (e?.name !== 'AbortError') setError(e?.message || 'AI 总结失败');
    } finally {
      setSummarizing(false);
    }
  };

  const rec = data ? REC_STYLE[data.recommendation] : null;

  return (
    <div className="p-4 md:p-6 max-w-5xl mx-auto">
      <h1 className="text-xl font-bold text-white mb-1">🎛️ 决策驾驶舱</h1>
      <p className="text-gray-500 text-sm mb-4">整合技术 / 基本面 / 情感 / ML / 持仓五维，给出综合买卖建议</p>

      {/* 输入区 */}
      <div className="flex gap-2 mb-5">
        <div className="flex-1 max-w-xs">
          <StockSymbolInput value={symbol} onChange={(s) => setSymbol(s)} placeholder="输入代码或名称，如 600519.SH" />
        </div>
        <button
          onClick={() => analyze()}
          disabled={!symbol.trim() || loading}
          className="px-5 py-2 rounded-lg bg-primary text-dark text-sm font-medium hover:bg-primary/80 disabled:opacity-50"
        >
          {loading ? '分析中…' : '分析'}
        </button>
      </div>

      {error && <div className="mb-4 p-3 rounded-lg bg-red-900/40 border border-red-600/40 text-red-200 text-sm">{error}</div>}

      {data && (
        <div className="space-y-4">
          {/* 综合建议条 */}
          <div className="bg-dark-card border border-border rounded-xl p-5">
            <div className="flex items-center justify-between flex-wrap gap-3">
              <div>
                <div className="text-lg font-bold text-white">{data.name} <span className="text-gray-500 text-sm">{data.symbol}</span></div>
                <div className="text-xs text-gray-500 mt-0.5">更新于 {data.as_of}</div>
              </div>
              <div className="flex items-center gap-4">
                <div className="text-center">
                  <div className="text-3xl font-bold text-white">{data.composite ?? '—'}</div>
                  <div className="text-[10px] text-gray-500">综合评分</div>
                </div>
                {rec && (
                  <span className={`px-4 py-2 rounded-lg border text-sm font-semibold ${rec.cls}`}>{rec.label}</span>
                )}
              </div>
            </div>
            <div className="grid grid-cols-2 md:grid-cols-4 gap-3 mt-4 text-sm">
              <div className="bg-dark-light rounded-lg p-3">
                <div className="text-gray-500 text-xs">现价</div>
                <div className="text-white font-medium">{data.price.latest ?? '—'}</div>
              </div>
              <div className="bg-dark-light rounded-lg p-3">
                <div className="text-gray-500 text-xs">建议仓位</div>
                <div className="text-white font-medium">{data.suggested_position_pct}%</div>
              </div>
              <div className="bg-dark-light rounded-lg p-3">
                <div className="text-gray-500 text-xs">止损位</div>
                <div className="text-white font-medium">{data.stop_loss ?? '—'}</div>
              </div>
              <div className="bg-dark-light rounded-lg p-3">
                <div className="text-gray-500 text-xs">PE / PB</div>
                <div className="text-white font-medium">
                  {data.valuation?.pe_ttm?.toFixed(1) ?? data.valuation?.pe?.toFixed(1) ?? '—'} / {data.valuation?.pb?.toFixed(2) ?? '—'}
                </div>
              </div>
            </div>
          </div>

          {/* 按你资金的建议 */}
          {data.suggested && (
            <div className="bg-dark-card border border-border rounded-xl p-5">
              <div className="flex items-center justify-between mb-3">
                <div className="text-sm font-semibold text-white">💰 按你的资金建议</div>
                <div className="text-xs text-gray-500">
                  总资金 ¥{data.total_capital?.toLocaleString()} · 可用现金 ¥{data.available_cash?.toLocaleString()}
                </div>
              </div>

              {data.suggested.affordable ? (
                <div className="grid grid-cols-2 md:grid-cols-4 gap-3 text-sm">
                  <div className="bg-dark-light rounded-lg p-3">
                    <div className="text-gray-500 text-xs">建议买入</div>
                    <div className="text-green-300 font-semibold">{data.suggested.shares} 股（{data.suggested.lots} 手）</div>
                  </div>
                  <div className="bg-dark-light rounded-lg p-3">
                    <div className="text-gray-500 text-xs">约需金额</div>
                    <div className="text-white font-medium">¥{data.suggested.amount?.toLocaleString()}</div>
                  </div>
                  <div className="bg-dark-light rounded-lg p-3">
                    <div className="text-gray-500 text-xs">当前持仓</div>
                    <div className="text-white font-medium">{data.current_position?.shares || 0} 股 · {data.current_position?.pct ?? 0}%</div>
                  </div>
                  <div className="bg-dark-light rounded-lg p-3">
                    <div className="text-gray-500 text-xs">
                      单股上限{data.total_capital ? `(${Math.round((data.suggested.max_single_amount / data.total_capital) * 100)}%)` : ''}
                    </div>
                    <div className="text-white font-medium">¥{data.suggested.max_single_amount?.toLocaleString()}</div>
                  </div>
                </div>
              ) : data.suggested.capped_by === 'score' ? (
                <div className="p-3 rounded-lg bg-yellow-900/20 border border-yellow-600/30 text-yellow-200 text-sm">
                  当前评分不建议买入/加仓（建议持有观望或回避，详见下方五维评分）
                </div>
              ) : (
                <div className="p-3 rounded-lg bg-red-900/30 border border-red-600/40 text-red-200 text-sm">
                  ⚠️ 以你目前的资金买不起这只（建议换更低价的标的）
                  {data.suggested.warnings?.[0] && (
                    <div className="text-red-300/80 text-xs mt-1">{data.suggested.warnings[0]}</div>
                  )}
                </div>
              )}

              {!data.suggested.risk_passed && data.suggested.affordable && (
                <div className="mt-2 text-xs text-yellow-400">⚠️ 该买入触发风控规则，请谨慎</div>
              )}
              {data.suggested.warnings?.length > 0 && (
                <ul className="mt-2 text-[11px] text-gray-500 list-disc list-inside space-y-0.5">
                  {data.suggested.warnings.map((w, i) => <li key={i}>{w}</li>)}
                </ul>
              )}
            </div>
          )}

          {/* 五维评分 */}
          <div className="bg-dark-card border border-border rounded-xl p-5">
            <div className="text-sm font-semibold text-white mb-3">五维评分</div>
            {(['technical', 'fundamental', 'sentiment', 'ml', 'position'] as const).map((k) => (
              <DimensionBar key={k} name={k} dim={data.dimensions[k]} />
            ))}
            <div className="text-[11px] text-gray-600 mt-2">
              权重（按可用维度归一化）：{Object.entries(data.weights_used).map(([k, v]) => `${DIM_LABELS[k]} ${(v * 100).toFixed(0)}%`).join(' · ') || '—'}
            </div>
          </div>

          {/* AI 解读 */}
          <div className="bg-dark-card border border-border rounded-xl p-5">
            <div className="flex items-center justify-between mb-3">
              <div className="text-sm font-semibold text-white">AI 决策解读</div>
              <button
                onClick={genSummary}
                disabled={summarizing}
                className="px-3 py-1.5 rounded-lg bg-accent-cyan/20 text-accent-cyan text-xs font-medium hover:bg-accent-cyan/30 disabled:opacity-50"
              >
                {summarizing ? '生成中…' : '生成解读'}
              </button>
            </div>
            <div className="text-sm text-gray-300 leading-relaxed whitespace-pre-wrap min-h-[3rem]">
              {summary || <span className="text-gray-600">点击「生成解读」让 AI 用最强模型解读这份驾驶舱数据</span>}
            </div>
          </div>
        </div>
      )}
    </div>
  );
};

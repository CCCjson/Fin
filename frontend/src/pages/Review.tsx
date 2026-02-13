import React, { useState, useEffect, useCallback, useRef } from 'react';
import ReactMarkdown from 'react-markdown';
import remarkGfm from 'remark-gfm';
import { reviewService } from '../services/reviewService';
import type {
  ReviewData,
  AiScoreResult,
  AiDimensionScores,
  CalendarEntry,
} from '../services/reviewService';

// ==================== 辅助函数 ====================

const todayStr = () => new Date().toISOString().slice(0, 10);

const formatMoney = (v: number | null | undefined) => {
  if (v === null || v === undefined) return '-';
  return v.toLocaleString('zh-CN', { minimumFractionDigits: 2, maximumFractionDigits: 2 });
};

const pnlColor = (v: number | null | undefined) => {
  if (v === null || v === undefined) return 'text-gray-500';
  if (v > 0) return 'text-bull';
  if (v < 0) return 'text-bear';
  return 'text-gray-400';
};

const pnlSign = (v: number | null | undefined) => {
  if (v === null || v === undefined) return '-';
  return `${v >= 0 ? '+' : ''}${formatMoney(v)}`;
};

const pctStr = (v: number | null | undefined) => {
  if (v === null || v === undefined) return '-';
  return `${v >= 0 ? '+' : ''}${v.toFixed(2)}%`;
};

/** 日期加减天 */
const addDays = (dateStr: string, days: number) => {
  const d = new Date(dateStr);
  d.setDate(d.getDate() + days);
  return d.toISOString().slice(0, 10);
};

const weekdayName = (dateStr: string) => {
  const days = ['周日', '周一', '周二', '周三', '周四', '周五', '周六'];
  return days[new Date(dateStr).getDay()];
};

const scoreLabel = (score: number | null | undefined) => {
  if (score === null || score === undefined) return '';
  if (score >= 9) return '完美';
  if (score >= 8) return '优秀';
  if (score >= 6) return '中规中矩';
  if (score >= 4) return '需改进';
  return '很差';
};

const scoreColor = (score: number | null | undefined) => {
  if (score === null || score === undefined) return 'text-gray-500';
  if (score >= 8) return 'text-bull';
  if (score >= 6) return 'text-primary-light';
  if (score >= 4) return 'text-amber-400';
  return 'text-bear';
};

// ==================== 组件 ====================

export const Review: React.FC = () => {
  const [currentDate, setCurrentDate] = useState(todayStr());
  const [data, setData] = useState<ReviewData | null>(null);
  const [loading, setLoading] = useState(false);

  // Note editing
  const [note, setNote] = useState('');
  const [selfScore, setSelfScore] = useState<number | null>(null);
  const [saving, setSaving] = useState(false);
  const [saveStatus, setSaveStatus] = useState<string>('');
  const saveTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);

  // AI scoring
  const [aiScoring, setAiScoring] = useState(false);
  const [scoreTab, setScoreTab] = useState<'overview' | 'dimensions' | 'highlights' | 'commentary'>('overview');

  // Signal pagination
  const [signalPage, setSignalPage] = useState(1);
  const signalPageSize = 15;

  // Calendar
  const [calendarEntries, setCalendarEntries] = useState<CalendarEntry[]>([]);

  // ---- Load data ----
  const loadReview = useCallback(async (dateStr: string, sigOffset: number = 0) => {
    setLoading(true);
    try {
      const result = await reviewService.getReview(dateStr, {
        signals_limit: signalPageSize,
        signals_offset: sigOffset,
      });
      setData(result);

      // 初始化笔记和评分（仅首次加载 / 切换日期时）
      if (sigOffset === 0) {
        if (result.review) {
          setNote(result.review.note || '');
          setSelfScore(result.review.self_score);
        } else {
          setNote('');
          setSelfScore(null);
        }
        setSaveStatus('');
      }
    } catch (e) {
      console.error('Failed to load review:', e);
    } finally {
      setLoading(false);
    }
  }, []);

  const loadCalendar = useCallback(async (dateStr: string) => {
    const d = new Date(dateStr);
    try {
      const result = await reviewService.getCalendar(d.getFullYear(), d.getMonth() + 1);
      setCalendarEntries(result.reviews);
    } catch (e) {
      console.error('Failed to load calendar:', e);
    }
  }, []);

  useEffect(() => {
    setSignalPage(1);
    loadReview(currentDate, 0);
    loadCalendar(currentDate);
  }, [currentDate]);

  // ---- Signal pagination ----
  const signalTotal = data?.signals_total ?? 0;
  const signalTotalPages = Math.max(1, Math.ceil(signalTotal / signalPageSize));

  const handleSignalPageChange = (p: number) => {
    if (p < 1 || p > signalTotalPages || p === signalPage) return;
    setSignalPage(p);
    loadReview(currentDate, (p - 1) * signalPageSize);
  };

  // ---- Date navigation ----
  const goDay = (offset: number) => {
    const next = addDays(currentDate, offset);
    if (next > todayStr()) return;
    setCurrentDate(next);
  };

  // ---- Auto-save note (debounce 2s) ----
  const handleNoteChange = (value: string) => {
    setNote(value);
    setSaveStatus('');
    if (saveTimerRef.current) clearTimeout(saveTimerRef.current);
    saveTimerRef.current = setTimeout(() => saveNote(value, selfScore), 2000);
  };

  const handleSelfScoreChange = (score: number) => {
    setSelfScore(score);
    if (saveTimerRef.current) clearTimeout(saveTimerRef.current);
    saveTimerRef.current = setTimeout(() => saveNote(note, score), 500);
  };

  const saveNote = async (noteContent: string, score: number | null) => {
    setSaving(true);
    try {
      const result = await reviewService.saveNote(currentDate, {
        note: noteContent,
        self_score: score || undefined,
      });
      setSaveStatus('已保存');
      // 更新本地 data
      if (data) {
        setData({
          ...data,
          review: {
            id: result.id,
            review_date: result.review_date,
            self_score: result.self_score,
            ai_score: data.review?.ai_score ?? null,
            ai_score_reason: data.review?.ai_score_reason ?? null,
            ai_dimension_scores: data.review?.ai_dimension_scores ?? null,
            composite_score: result.composite_score,
            note: noteContent,
            template_used: 'beginner',
            updated_at: result.updated_at,
          },
        });
      }
    } catch (e) {
      console.error('Save failed:', e);
      setSaveStatus('保存失败');
    } finally {
      setSaving(false);
    }
  };

  const handleManualSave = () => {
    if (saveTimerRef.current) clearTimeout(saveTimerRef.current);
    saveNote(note, selfScore);
  };

  // ---- AI scoring ----
  const handleAiScore = async () => {
    setAiScoring(true);
    try {
      const result: AiScoreResult = await reviewService.requestAiScore(currentDate);
      if (data) {
        setData({
          ...data,
          review: {
            ...(data.review || {
              id: 0,
              review_date: currentDate,
              note: note,
              template_used: 'beginner',
              updated_at: null,
            }),
            self_score: result.self_score,
            ai_score: result.ai_score,
            ai_score_reason: result.ai_score_reason,
            ai_dimension_scores: result.ai_dimension_scores,
            composite_score: result.composite_score,
          },
        });
        // Auto-switch to dimensions tab after scoring
        setScoreTab('dimensions');
      }
    } catch (e) {
      console.error('AI score failed:', e);
      alert('AI 评分失败，请确认 OPENAI_API_KEY 已配置');
    } finally {
      setAiScoring(false);
    }
  };

  // ---- Insert template ----
  const insertTemplate = () => {
    if (note.trim() && !confirm('当前笔记不为空，确认使用模板覆盖？')) return;
    setNote(data?.template || '');
    setSaveStatus('');
  };

  // ---- Calendar helpers ----
  const calendarDates = new Set(calendarEntries.map(e => e.date));
  const hasReview = (d: string) => calendarDates.has(d);

  // ---- Render stars ----
  const renderStars = (score: number | null, onChange?: (s: number) => void) => {
    const s = score || 0;
    return (
      <div className="flex items-center gap-0.5">
        {Array.from({ length: 10 }, (_, i) => (
          <button
            key={i}
            onClick={() => onChange?.(i + 1)}
            disabled={!onChange}
            className={`text-lg transition-all ${
              i < s ? 'text-amber-400' : 'text-gray-600'
            } ${onChange ? 'hover:text-amber-300 cursor-pointer' : 'cursor-default'}`}
          >
            {i < s ? '\u2605' : '\u2606'}
          </button>
        ))}
        {score !== null && score !== undefined && (
          <span className={`ml-2 font-bold text-lg ${scoreColor(score)}`}>{score}</span>
        )}
      </div>
    );
  };

  const review = data?.review;

  // ==================== RENDER ====================
  return (
    <div className="min-h-screen bg-gradient-dark p-6">
      <div className="max-w-7xl mx-auto space-y-6">
        {/* Header with date navigation */}
        <div className="flex items-center justify-between">
          <div className="flex items-center gap-4">
            <h1 className="text-3xl font-bold text-white">每日复盘</h1>
            <div className="flex items-center gap-2 bg-dark-card border border-border rounded-xl px-2 py-1">
              <button
                onClick={() => goDay(-1)}
                className="px-2 py-1 text-gray-400 hover:text-white transition-colors"
              >
                <svg className="h-5 w-5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                  <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M15 19l-7-7 7-7" />
                </svg>
              </button>
              <input
                type="date"
                value={currentDate}
                max={todayStr()}
                onChange={e => setCurrentDate(e.target.value)}
                className="bg-transparent text-white text-lg font-medium border-none outline-none px-2"
              />
              <span className="text-gray-400 text-sm">{weekdayName(currentDate)}</span>
              <button
                onClick={() => goDay(1)}
                disabled={currentDate >= todayStr()}
                className="px-2 py-1 text-gray-400 hover:text-white transition-colors disabled:opacity-30"
              >
                <svg className="h-5 w-5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                  <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M9 5l7 7-7 7" />
                </svg>
              </button>
            </div>
            {hasReview(currentDate) && (
              <span className="w-2.5 h-2.5 rounded-full bg-bull animate-pulse" title="已有复盘记录" />
            )}
          </div>
          <div className="flex items-center gap-3">
            {saving && <span className="text-sm text-gray-400">保存中...</span>}
            {saveStatus && <span className="text-sm text-bull">{saveStatus}</span>}
            <button
              onClick={handleManualSave}
              disabled={saving}
              className="px-4 py-2 bg-primary text-white rounded-xl hover:bg-primary-dark shadow-glow-blue transition-all disabled:opacity-50 flex items-center gap-2"
            >
              <svg className="h-4 w-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M5 13l4 4L19 7" />
              </svg>
              保存
            </button>
          </div>
        </div>

        {loading ? (
          <div className="text-center py-20 text-gray-500">加载中...</div>
        ) : data ? (
          <>
            {/* Index Cards (5) */}
            <div className="grid grid-cols-2 md:grid-cols-5 gap-4">
              {data.indices.map(idx => (
                <div key={idx.symbol} className="bg-gradient-card p-4 rounded-xl border border-border shadow-card">
                  <div className="text-gray-400 text-sm mb-1">{idx.name}</div>
                  <div className="text-xl font-bold text-white">
                    {idx.price !== null ? idx.price.toLocaleString() : '-'}
                  </div>
                  <div className={`text-sm font-medium mt-1 ${pnlColor(idx.change_pct)}`}>
                    {idx.change_pct !== null
                      ? `${idx.change_pct >= 0 ? '+' : ''}${idx.change_pct.toFixed(2)}% ${idx.change_pct >= 0 ? '\u25B2' : '\u25BC'}`
                      : '-'
                    }
                  </div>
                </div>
              ))}
            </div>

            {/* Daily Overview Cards (4) */}
            <div className="grid grid-cols-2 md:grid-cols-4 gap-4">
              <div className="bg-gradient-card p-5 rounded-xl border border-border shadow-card">
                <div className="text-gray-400 text-sm mb-2">当日总盈亏</div>
                <div className={`text-2xl font-bold ${pnlColor(data.daily_pnl)}`}>
                  {pnlSign(data.daily_pnl)}
                </div>
              </div>
              <div className="bg-gradient-card p-5 rounded-xl border border-border shadow-card">
                <div className="text-gray-400 text-sm mb-2">持仓数</div>
                <div className="text-2xl font-bold text-primary-light">{data.positions_count} 只</div>
              </div>
              <div className="bg-gradient-card p-5 rounded-xl border border-border shadow-card">
                <div className="text-gray-400 text-sm mb-2">当日交易</div>
                <div className="text-2xl font-bold text-accent-cyan">{data.trades_count} 笔</div>
                {data.trades_count > 0 && (
                  <div className="text-xs text-gray-500 mt-1">
                    {data.trades.filter(t => t.side === 'BUY').length}买 {data.trades.filter(t => t.side === 'SELL').length}卖
                  </div>
                )}
              </div>
              <div className="bg-gradient-card p-5 rounded-xl border border-border shadow-card">
                <div className="text-gray-400 text-sm mb-2">当日信号</div>
                <div className="text-2xl font-bold text-accent-purple">{data.signals_count} 个</div>
                {data.signals_count > 0 && (
                  <div className="text-xs text-gray-500 mt-1">
                    {data.signals.filter(s => s.signal_type === 'BUY').length}买 {data.signals.filter(s => s.signal_type === 'SELL').length}卖
                  </div>
                )}
              </div>
            </div>

            {/* Two columns: Left (positions + trades + signals), Right (scoring) */}
            <div className="grid grid-cols-1 lg:grid-cols-3 gap-6">
              {/* Left column (2/3) */}
              <div className="lg:col-span-2 flex flex-col gap-6">
                {/* Positions daily performance */}
                <div className="bg-gradient-card border border-border shadow-card p-6 rounded-xl">
                  <h2 className="text-lg font-semibold text-white mb-4">持仓当日表现</h2>
                  <div className="overflow-x-auto rounded-lg border border-border">
                    <table className="w-full text-sm">
                      <thead className="bg-dark-light text-gray-300 border-b border-border">
                        <tr>
                          <th className="px-3 py-2.5 text-left">股票</th>
                          <th className="px-3 py-2.5 text-right">现价</th>
                          <th className="px-3 py-2.5 text-right">日涨跌</th>
                          <th className="px-3 py-2.5 text-right">日盈亏</th>
                          <th className="px-3 py-2.5 text-right">持仓浮盈</th>
                        </tr>
                      </thead>
                      <tbody className="text-gray-300">
                        {data.positions.length === 0 ? (
                          <tr><td colSpan={5} className="px-4 py-6 text-center text-gray-500">暂无持仓</td></tr>
                        ) : data.positions.map(p => (
                          <tr key={p.symbol} className="border-b border-border hover:bg-dark-light transition-colors">
                            <td className="px-3 py-2.5">
                              <div className="font-medium text-white">{p.name}</div>
                              <div className="text-xs text-gray-500">{p.symbol} | {p.quantity}股</div>
                            </td>
                            <td className="px-3 py-2.5 text-right text-primary-light">
                              {p.today_close?.toFixed(2) ?? '-'}
                            </td>
                            <td className={`px-3 py-2.5 text-right font-medium ${pnlColor(p.daily_change_pct)}`}>
                              {pctStr(p.daily_change_pct)}
                            </td>
                            <td className={`px-3 py-2.5 text-right font-medium ${pnlColor(p.daily_pnl)}`}>
                              {pnlSign(p.daily_pnl)}
                            </td>
                            <td className={`px-3 py-2.5 text-right ${pnlColor(p.unrealized_pnl)}`}>
                              {pnlSign(p.unrealized_pnl)}
                            </td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </div>
                </div>

                {/* Day trades */}
                <div className="bg-gradient-card border border-border shadow-card p-6 rounded-xl">
                  <h2 className="text-lg font-semibold text-white mb-4">当日交易</h2>
                  {data.trades.length === 0 ? (
                    <div className="text-gray-500 text-center py-6">今日无交易</div>
                  ) : (
                    <div className="overflow-x-auto rounded-lg border border-border">
                      <table className="w-full text-sm">
                        <thead className="bg-dark-light text-gray-300 border-b border-border">
                          <tr>
                            <th className="px-3 py-2.5 text-center">方向</th>
                            <th className="px-3 py-2.5 text-left">股票</th>
                            <th className="px-3 py-2.5 text-right">价格</th>
                            <th className="px-3 py-2.5 text-right">数量</th>
                            <th className="px-3 py-2.5 text-right">金额</th>
                            <th className="px-3 py-2.5 text-right">AI价</th>
                            <th className="px-3 py-2.5 text-left">备注</th>
                          </tr>
                        </thead>
                        <tbody className="text-gray-300">
                          {data.trades.map(t => (
                            <tr key={t.id} className="border-b border-border hover:bg-dark-light transition-colors">
                              <td className="px-3 py-2.5 text-center">
                                <span className={`px-2 py-0.5 rounded-lg text-xs font-semibold ${
                                  t.side === 'BUY'
                                    ? 'bg-bull/20 text-bull border border-bull/30'
                                    : 'bg-bear/20 text-bear border border-bear/30'
                                }`}>
                                  {t.side === 'BUY' ? '买入' : '卖出'}
                                </span>
                              </td>
                              <td className="px-3 py-2.5 font-medium text-white">{t.name || t.symbol}</td>
                              <td className="px-3 py-2.5 text-right">{t.price.toFixed(2)}</td>
                              <td className="px-3 py-2.5 text-right">{t.quantity}</td>
                              <td className="px-3 py-2.5 text-right">{formatMoney(t.amount)}</td>
                              <td className="px-3 py-2.5 text-right text-accent-cyan">
                                {t.ai_recommended_price ? t.ai_recommended_price.toFixed(2) : '-'}
                              </td>
                              <td className="px-3 py-2.5 text-gray-400 truncate max-w-[120px]">{t.note || '-'}</td>
                            </tr>
                          ))}
                        </tbody>
                      </table>
                    </div>
                  )}
                </div>

                {/* Review Note */}
                <div className="bg-gradient-card border border-border shadow-card p-6 rounded-xl flex-1 flex flex-col">
                  <div className="flex items-center justify-between mb-4">
                    <h2 className="text-lg font-semibold text-white">复盘笔记</h2>
                    <div className="flex items-center gap-3">
                      <button
                        onClick={insertTemplate}
                        className="px-3 py-1.5 text-xs bg-dark-light text-gray-300 border border-border rounded-lg hover:bg-border transition-all flex items-center gap-1"
                      >
                        <svg className="h-3.5 w-3.5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                          <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M9 12h6m-6 4h6m2 5H7a2 2 0 01-2-2V5a2 2 0 012-2h5.586a1 1 0 01.707.293l5.414 5.414a1 1 0 01.293.707V19a2 2 0 01-2 2z" />
                        </svg>
                        使用小白模板
                      </button>
                      {review?.updated_at && (
                        <span className="text-xs text-gray-500">
                          上次保存: {new Date(review.updated_at).toLocaleString('zh-CN')}
                        </span>
                      )}
                    </div>
                  </div>
                  <textarea
                    value={note}
                    onChange={e => handleNoteChange(e.target.value)}
                    placeholder="写下今天的复盘思考...&#10;&#10;提示：点击右上角「使用小白模板」可以获取引导式模板"
                    rows={14}
                    className="w-full flex-1 min-h-[280px] px-4 py-3 bg-dark-light text-gray-200 rounded-xl border border-border focus:border-primary focus:ring-2 focus:ring-primary/20 outline-none transition-all resize-y leading-relaxed font-mono text-sm"
                  />
                  <div className="flex items-center justify-between mt-2">
                    <span className="text-xs text-gray-500">
                      支持 Markdown 格式 | 自动保存（2秒无操作后）
                    </span>
                    {saving && <span className="text-xs text-gray-400">保存中...</span>}
                    {saveStatus === '已保存' && <span className="text-xs text-bull">已保存</span>}
                    {saveStatus === '保存失败' && <span className="text-xs text-bear">保存失败</span>}
                  </div>
                </div>
              </div>

              {/* Right column (1/3) - scoring + signals */}
              <div className="flex flex-col gap-6">
                {/* Tabbed Score Panel */}
                <div className="bg-gradient-card border border-border shadow-card rounded-xl overflow-hidden">
                  {/* Tab bar */}
                  <div className="flex border-b border-border">
                    {([
                      { key: 'overview' as const, label: '总览' },
                      { key: 'dimensions' as const, label: '维度' },
                      { key: 'highlights' as const, label: '亮点' },
                      { key: 'commentary' as const, label: '评语' },
                    ]).map(tab => {
                      const disabled = tab.key !== 'overview' && !review?.ai_score;
                      const active = scoreTab === tab.key;
                      return (
                        <button
                          key={tab.key}
                          onClick={() => !disabled && setScoreTab(tab.key)}
                          disabled={disabled}
                          className={`flex-1 px-3 py-2.5 text-xs font-medium transition-all relative ${
                            active
                              ? 'text-violet-300'
                              : disabled
                              ? 'text-gray-600 cursor-not-allowed'
                              : 'text-gray-400 hover:text-gray-200'
                          }`}
                        >
                          {tab.label}
                          {active && (
                            <span className="absolute bottom-0 left-1 right-1 h-0.5 bg-gradient-to-r from-violet-500 to-purple-500 rounded-full" />
                          )}
                        </button>
                      );
                    })}
                  </div>

                  {/* Tab content */}
                  <div className="p-5 min-h-[280px]">
                    {/* === Tab: 总览 === */}
                    {scoreTab === 'overview' && (
                      <div className="space-y-5">
                        {/* Composite ring */}
                        <div className="flex justify-center">
                          <div className="relative">
                            <svg width="120" height="120" className="-rotate-90">
                              <circle cx="60" cy="60" r="50" fill="none" stroke="currentColor" strokeWidth="8" className="text-dark-light" />
                              <circle cx="60" cy="60" r="50" fill="none" strokeWidth="8"
                                strokeDasharray={`${((review?.composite_score ?? 0) / 10) * 314} 314`}
                                strokeLinecap="round"
                                className={
                                  (review?.composite_score ?? 0) >= 8 ? 'text-bull'
                                  : (review?.composite_score ?? 0) >= 6 ? 'text-primary-light'
                                  : (review?.composite_score ?? 0) >= 4 ? 'text-amber-400'
                                  : review?.composite_score ? 'text-bear' : 'text-gray-600'
                                }
                                stroke="currentColor"
                              />
                            </svg>
                            <div className="absolute inset-0 flex flex-col items-center justify-center">
                              <span className={`text-3xl font-bold ${scoreColor(review?.composite_score)}`}>
                                {review?.composite_score != null ? review.composite_score.toFixed(1) : '-'}
                              </span>
                              <span className="text-xs text-gray-500">综合分</span>
                            </div>
                          </div>
                        </div>

                        {/* Self vs AI side by side */}
                        <div className="grid grid-cols-2 gap-3">
                          <div className="bg-dark rounded-xl p-3 text-center">
                            <div className="text-xs text-gray-500 mb-1">自评分</div>
                            <div className={`text-2xl font-bold ${scoreColor(selfScore)}`}>
                              {selfScore ?? '-'}
                            </div>
                            {selfScore && (
                              <div className={`text-xs ${scoreColor(selfScore)}`}>{scoreLabel(selfScore)}</div>
                            )}
                          </div>
                          <div className="bg-dark rounded-xl p-3 text-center">
                            <div className="text-xs text-gray-500 mb-1">AI 评分</div>
                            <div className={`text-2xl font-bold ${scoreColor(review?.ai_score)}`}>
                              {review?.ai_score ?? '-'}
                            </div>
                            {review?.ai_score && (
                              <div className={`text-xs ${scoreColor(review.ai_score)}`}>{scoreLabel(review.ai_score)}</div>
                            )}
                          </div>
                        </div>

                        {/* Self score stars */}
                        <div>
                          <div className="text-xs text-gray-500 mb-1.5">点击评分</div>
                          {renderStars(selfScore, handleSelfScoreChange)}
                        </div>
                      </div>
                    )}

                    {/* === Tab: 维度 === */}
                    {scoreTab === 'dimensions' && (
                      !review?.ai_dimension_scores?.dimensions ? (
                        <div className="text-center py-10 text-gray-500">
                          <div className="text-4xl mb-3 opacity-30">📊</div>
                          <div>暂无维度评分</div>
                          <div className="text-xs mt-1">{review?.ai_score ? '旧版评分无维度数据，请重新评分' : '请先请求 AI 评分'}</div>
                        </div>
                      ) : (
                        <div className="space-y-4">
                          {([
                            { key: 'discipline' as const, label: '纪律执行', weight: 30 },
                            { key: 'position' as const, label: '仓位管理', weight: 20 },
                            { key: 'timing' as const, label: '买卖时机', weight: 20 },
                            { key: 'signal_follow' as const, label: '信号跟进', weight: 15 },
                            { key: 'reflection' as const, label: '自我反思', weight: 15 },
                          ] as const).map(({ key, label, weight }) => {
                            const dim = review.ai_dimension_scores!.dimensions[key];
                            if (!dim) return null;
                            const s = dim.score;
                            const barColor = s >= 8 ? 'bg-bull' : s >= 6 ? 'bg-primary-light' : s >= 4 ? 'bg-amber-400' : 'bg-bear';
                            return (
                              <div key={key}>
                                <div className="flex items-center justify-between mb-1">
                                  <span className="text-sm text-gray-300">{label} <span className="text-gray-600 text-xs">({weight}%)</span></span>
                                  <span className={`text-sm font-bold ${scoreColor(s)}`}>{s}/10</span>
                                </div>
                                <div className="h-2 bg-dark rounded-full overflow-hidden">
                                  <div className={`h-full rounded-full transition-all duration-500 ${barColor}`} style={{ width: `${s * 10}%` }} />
                                </div>
                                <p className="text-xs text-gray-500 mt-0.5 leading-relaxed">{dim.comment}</p>
                              </div>
                            );
                          })}
                          <div className="pt-3 border-t border-border text-xs text-gray-500">
                            加权分 = {[
                              { key: 'discipline' as const, weight: 30 },
                              { key: 'position' as const, weight: 20 },
                              { key: 'timing' as const, weight: 20 },
                              { key: 'signal_follow' as const, weight: 15 },
                              { key: 'reflection' as const, weight: 15 },
                            ].map(({ key, weight }) => {
                              const s = review.ai_dimension_scores!.dimensions[key]?.score ?? 0;
                              return `${s}×${weight}%`;
                            }).join(' + ')} = {
                              [
                                { key: 'discipline' as const, weight: 30 },
                                { key: 'position' as const, weight: 20 },
                                { key: 'timing' as const, weight: 20 },
                                { key: 'signal_follow' as const, weight: 15 },
                                { key: 'reflection' as const, weight: 15 },
                              ].reduce((acc, { key, weight }) => acc + (review.ai_dimension_scores!.dimensions[key]?.score ?? 0) * weight / 100, 0).toFixed(1)
                            }
                          </div>
                        </div>
                      )
                    )}

                    {/* === Tab: 亮点 === */}
                    {scoreTab === 'highlights' && (
                      !review?.ai_dimension_scores ? (
                        <div className="text-center py-10 text-gray-500">
                          <div className="text-4xl mb-3 opacity-30">💡</div>
                          <div>暂无亮点数据</div>
                          <div className="text-xs mt-1">{review?.ai_score ? '旧版评分无亮点数据，请重新评分' : '请先请求 AI 评分'}</div>
                        </div>
                      ) : (
                        <div className="space-y-5">
                          {(review.ai_dimension_scores.highlights?.length ?? 0) > 0 && (
                            <div>
                              <div className="flex items-center gap-2 mb-3">
                                <span className="w-1.5 h-1.5 rounded-full bg-bull" />
                                <span className="text-sm font-medium text-bull">做得好</span>
                              </div>
                              <div className="space-y-2">
                                {review.ai_dimension_scores.highlights.map((h: string, i: number) => (
                                  <div key={i} className="flex items-start gap-2 bg-bull/5 border border-bull/10 rounded-lg px-3 py-2">
                                    <svg className="h-4 w-4 text-bull shrink-0 mt-0.5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                                      <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M5 13l4 4L19 7" />
                                    </svg>
                                    <span className="text-sm text-gray-300">{h}</span>
                                  </div>
                                ))}
                              </div>
                            </div>
                          )}
                          {(review.ai_dimension_scores.improvements?.length ?? 0) > 0 && (
                            <div>
                              <div className="flex items-center gap-2 mb-3">
                                <span className="w-1.5 h-1.5 rounded-full bg-amber-400" />
                                <span className="text-sm font-medium text-amber-400">待改进</span>
                              </div>
                              <div className="space-y-2">
                                {review.ai_dimension_scores.improvements.map((imp: string, i: number) => (
                                  <div key={i} className="flex items-start gap-2 bg-amber-500/5 border border-amber-500/10 rounded-lg px-3 py-2">
                                    <svg className="h-4 w-4 text-amber-400 shrink-0 mt-0.5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                                      <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M12 9v2m0 4h.01m-6.938 4h13.856c1.54 0 2.502-1.667 1.732-2.5L13.732 4c-.77-.833-1.964-.833-2.732 0L4.082 16.5c-.77.833.192 2.5 1.732 2.5z" />
                                    </svg>
                                    <span className="text-sm text-gray-300">{imp}</span>
                                  </div>
                                ))}
                              </div>
                            </div>
                          )}
                          {(review.ai_dimension_scores.highlights?.length ?? 0) === 0 && (review.ai_dimension_scores.improvements?.length ?? 0) === 0 && (
                            <div className="text-center py-10 text-gray-500">
                              <div>AI 未返回亮点与改进建议</div>
                            </div>
                          )}
                        </div>
                      )
                    )}

                    {/* === Tab: 评语 === */}
                    {scoreTab === 'commentary' && (
                      review?.ai_score_reason ? (
                        <div className="prose-sm text-gray-300 leading-relaxed">
                          <ReactMarkdown
                            remarkPlugins={[remarkGfm]}
                            components={{
                              h1: ({ children }) => <h1 className="text-lg font-bold text-white mt-4 mb-2">{children}</h1>,
                              h2: ({ children }) => <h2 className="text-base font-bold text-white mt-4 mb-2">{children}</h2>,
                              h3: ({ children }) => <h3 className="text-sm font-semibold text-primary-light mt-3 mb-1.5">{children}</h3>,
                              p: ({ children }) => <p className="text-sm text-gray-300 leading-relaxed mb-3">{children}</p>,
                              strong: ({ children }) => <strong className="text-white font-semibold">{children}</strong>,
                              ul: ({ children }) => <ul className="space-y-1 mb-3 ml-1">{children}</ul>,
                              ol: ({ children }) => <ol className="space-y-1 mb-3 ml-1 list-decimal list-inside">{children}</ol>,
                              li: ({ children }) => (
                                <li className="text-sm text-gray-300 flex items-start gap-1.5">
                                  <span className="mt-2 w-1 h-1 bg-violet-400 rounded-full shrink-0" />
                                  <span className="flex-1">{children}</span>
                                </li>
                              ),
                              blockquote: ({ children }) => (
                                <blockquote className="border-l-2 border-violet-500/50 pl-3 my-2 text-sm text-gray-400">{children}</blockquote>
                              ),
                            }}
                          >
                            {review.ai_score_reason}
                          </ReactMarkdown>
                        </div>
                      ) : (
                        <div className="text-center py-10 text-gray-500">
                          <div className="text-4xl mb-3 opacity-30">🤖</div>
                          <div>暂无 AI 评语</div>
                          <div className="text-xs mt-1">请先请求 AI 评分</div>
                        </div>
                      )
                    )}
                  </div>

                  {/* AI score button — pinned at bottom, outside tabs */}
                  <div className="px-5 pb-5">
                    <button
                      onClick={handleAiScore}
                      disabled={aiScoring}
                      className="w-full px-4 py-2.5 bg-gradient-to-r from-violet-500 to-purple-600 text-white text-sm rounded-xl hover:from-violet-600 hover:to-purple-700 shadow-lg shadow-violet-500/25 transition-all disabled:opacity-50 disabled:cursor-not-allowed flex items-center justify-center gap-2"
                    >
                      {aiScoring ? (
                        <>
                          <svg className="animate-spin h-4 w-4" viewBox="0 0 24 24">
                            <circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4" fill="none" />
                            <path className="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4zm2 5.291A7.962 7.962 0 014 12H0c0 3.042 1.135 5.824 3 7.938l3-2.647z" />
                          </svg>
                          AI 评分中...
                        </>
                      ) : (
                        <>
                          <svg className="h-4 w-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                            <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M9.663 17h4.673M12 3v1m6.364 1.636l-.707.707M21 12h-1M4 12H3m3.343-5.657l-.707-.707m2.828 9.9a5 5 0 117.072 0l-.548.547A3.374 3.374 0 0014 18.469V19a2 2 0 11-4 0v-.531c0-.895-.356-1.754-.988-2.386l-.548-.547z" />
                          </svg>
                          {review?.ai_score ? '重新评分' : '请求 AI 评分'}
                        </>
                      )}
                    </button>
                  </div>
                </div>

                {/* Day signals */}
                <div className="bg-gradient-card border border-border shadow-card p-6 rounded-xl flex-1 flex flex-col">
                  <div className="flex items-center justify-between mb-4">
                    <h2 className="text-lg font-semibold text-white">当日信号</h2>
                    {signalTotal > 0 && (
                      <span className="text-sm text-gray-400">
                        共 <span className="text-white font-medium">{signalTotal}</span> 条
                      </span>
                    )}
                  </div>
                  {data.signals.length === 0 && signalTotal === 0 ? (
                    <div className="text-gray-500 text-center py-6 flex-1 flex items-center justify-center">今日无信号</div>
                  ) : (
                    <div className="flex-1 flex flex-col">
                      <div className="overflow-x-auto rounded-lg border border-border">
                        <table className="w-full text-sm">
                          <thead className="bg-dark-light text-gray-300 border-b border-border">
                            <tr>
                              <th className="px-3 py-2 text-left">股票</th>
                              <th className="px-3 py-2 text-center">信号</th>
                              <th className="px-3 py-2 text-right">强度</th>
                              <th className="px-3 py-2 text-center">状态</th>
                            </tr>
                          </thead>
                          <tbody className="text-gray-300">
                            {data.signals.map(s => (
                              <tr key={s.id} className="border-b border-border hover:bg-dark-light transition-colors">
                                <td className="px-3 py-2">
                                  <div className="font-medium text-white text-xs">{s.name}</div>
                                  <div className="text-xs text-gray-500">{s.symbol}</div>
                                </td>
                                <td className="px-3 py-2 text-center">
                                  <span className={`px-1.5 py-0.5 rounded text-xs font-semibold ${
                                    s.signal_type === 'BUY'
                                      ? 'bg-bull/20 text-bull border border-bull/30'
                                      : 'bg-bear/20 text-bear border border-bear/30'
                                  }`}>
                                    {s.signal_type === 'BUY' ? '买' : '卖'}
                                  </span>
                                </td>
                                <td className="px-3 py-2 text-right font-medium text-amber-400 text-xs">
                                  {s.strength.toFixed(2)}
                                </td>
                                <td className="px-3 py-2 text-center">
                                  {s.holding_status === 'held' && (
                                    <span className="px-1.5 py-0.5 rounded text-xs bg-bull/20 text-bull border border-bull/30">持有</span>
                                  )}
                                  {s.holding_status === 'sold_today' && (
                                    <span className="px-1.5 py-0.5 rounded text-xs bg-amber-500/20 text-amber-400 border border-amber-500/30">已卖</span>
                                  )}
                                  {s.holding_status === 'not_held' && (
                                    <span className="px-1.5 py-0.5 rounded text-xs bg-gray-500/20 text-gray-400 border border-gray-500/30">未持</span>
                                  )}
                                </td>
                              </tr>
                            ))}
                          </tbody>
                        </table>
                      </div>
                      {/* Signal Pagination */}
                      {signalTotal > signalPageSize && (
                        <div className="flex items-center justify-between mt-3 pt-3 border-t border-border">
                          <span className="text-xs text-gray-400">
                            {(signalPage - 1) * signalPageSize + 1}-{Math.min(signalPage * signalPageSize, signalTotal)} / {signalTotal}
                          </span>
                          <div className="flex items-center gap-1">
                            <button onClick={() => handleSignalPageChange(signalPage - 1)} disabled={signalPage === 1 || loading} className="px-2 py-1 text-xs bg-dark-light text-gray-300 rounded border border-border hover:bg-border disabled:opacity-30 disabled:cursor-not-allowed transition-all">&lt;</button>
                            <span className="text-xs text-gray-400 px-1">{signalPage}/{signalTotalPages}</span>
                            <button onClick={() => handleSignalPageChange(signalPage + 1)} disabled={signalPage === signalTotalPages || loading} className="px-2 py-1 text-xs bg-dark-light text-gray-300 rounded border border-border hover:bg-border disabled:opacity-30 disabled:cursor-not-allowed transition-all">&gt;</button>
                          </div>
                        </div>
                      )}
                    </div>
                  )}
                </div>
              </div>
            </div>

          </>
        ) : (
          <div className="text-center py-20 text-gray-500">暂无数据</div>
        )}
      </div>
    </div>
  );
};

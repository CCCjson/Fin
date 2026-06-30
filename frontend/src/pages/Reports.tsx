import React, { useState, useEffect, useRef, useCallback, useMemo } from 'react';
import ReactMarkdown from 'react-markdown';
import remarkGfm from 'remark-gfm';
import type { Components } from 'react-markdown';
import { MarketSelector } from '../components/common/MarketSelector';
import { reportService } from '../services/reportService';
import type { ReportSummary, ReportDetail, ReportStreamEvent } from '../services/reportService';

type View = 'list' | 'view' | 'generate';

/* ================================================================
   日历工具函数
   ================================================================ */
function getMonthDays(year: number, month: number) {
  return new Date(year, month + 1, 0).getDate();
}

function getFirstDayOfWeek(year: number, month: number) {
  return new Date(year, month, 1).getDay(); // 0=周日
}

function isToday(day: number, month: number, year: number) {
  const now = new Date();
  return now.getFullYear() === year && now.getMonth() === month && now.getDate() === day;
}

function isWeekend(dayOfWeek: number) {
  return dayOfWeek === 0 || dayOfWeek === 6;
}

const WEEKDAY_LABELS = ['日', '一', '二', '三', '四', '五', '六'];

/* ================================================================
   章节解析
   ================================================================ */
interface Chapter {
  number: number;
  title: string;
  content: string;
}

function parseChapters(markdown: string): Chapter[] {
  if (!markdown || !markdown.trim()) return [];

  const headerRegex = /^## (\d+)[.\s、：:]/gm;
  const matches: { number: number; index: number }[] = [];

  let m;
  while ((m = headerRegex.exec(markdown)) !== null) {
    matches.push({ number: parseInt(m[1]), index: m.index });
  }

  if (matches.length === 0) return [];

  // 按章节号合并重复章节（同一章节多批次生成时 GPT 可能重复输出标题）
  const chapterMap = new Map<number, Chapter>();

  for (let i = 0; i < matches.length; i++) {
    const start = matches[i].index;
    const end = i + 1 < matches.length ? matches[i + 1].index : markdown.length;
    const section = markdown.slice(start, end).trimEnd();
    const firstLine = section.split('\n')[0];
    const title = firstLine.replace(/^##\s*\d+[.\s、：:]\s*/, '').trim();
    const num = matches[i].number;

    if (chapterMap.has(num)) {
      // 同章节重复：把后续内容去掉重复的二级标题后追加
      const existing = chapterMap.get(num)!;
      const extra = section.replace(/^##[^\n]*\n?/, '').trimStart();
      if (extra) {
        existing.content += '\n\n' + extra;
      }
    } else {
      chapterMap.set(num, {
        number: num,
        title: title || `第${num}章`,
        content: section,
      });
    }
  }

  return Array.from(chapterMap.values()).sort((a, b) => a.number - b.number);
}

/* ================================================================
   Markdown 自定义渲染组件 — 暗色主题专属样式
   ================================================================ */
const mdComponents: Components = {
  h1: ({ children }) => (
    <h1 className="text-2xl font-bold text-white mt-8 mb-4 pb-3 border-b border-border-light">
      {children}
    </h1>
  ),
  h2: ({ children }) => (
    <div className="mt-10 mb-5">
      <h2 className="text-xl font-bold text-white flex items-center gap-3">
        <span className="w-1 h-6 bg-gradient-to-b from-violet-500 to-purple-600 rounded-full" />
        {children}
      </h2>
      <div className="mt-2 h-px bg-gradient-to-r from-border-light to-transparent" />
    </div>
  ),
  h3: ({ children }) => (
    <h3 className="text-lg font-semibold text-primary-light mt-6 mb-3 flex items-center gap-2">
      <span className="w-1.5 h-1.5 bg-primary-light rounded-full" />
      {children}
    </h3>
  ),
  h4: ({ children }) => (
    <h4 className="text-base font-semibold text-gray-200 mt-4 mb-2">{children}</h4>
  ),
  p: ({ children }) => (
    <p className="text-gray-300 leading-7 mb-4">{children}</p>
  ),
  strong: ({ children }) => (
    <strong className="text-white font-semibold">{children}</strong>
  ),
  em: ({ children }) => (
    <em className="text-accent-cyan not-italic font-medium">{children}</em>
  ),
  ul: ({ children }) => (
    <ul className="space-y-2 mb-4 ml-1">{children}</ul>
  ),
  ol: ({ children }) => (
    <ol className="space-y-2 mb-4 ml-1 list-decimal list-inside">{children}</ol>
  ),
  li: ({ children }) => (
    <li className="text-gray-300 leading-7 flex items-start gap-2">
      <span className="mt-2.5 w-1.5 h-1.5 bg-accent-purple rounded-full shrink-0" />
      <span className="flex-1">{children}</span>
    </li>
  ),
  blockquote: ({ children }) => (
    <blockquote className="border-l-4 border-accent-purple/50 bg-accent-purple/5 pl-4 py-2 my-4 rounded-r-lg">
      {children}
    </blockquote>
  ),
  code: ({ className, children }) => {
    const isBlock = className?.includes('language-');
    if (isBlock) {
      return (
        <code className="block bg-dark text-sm text-gray-300 p-4 rounded-lg overflow-x-auto border border-border my-4 font-mono">
          {children}
        </code>
      );
    }
    return (
      <code className="bg-violet-500/15 text-violet-300 px-1.5 py-0.5 rounded text-sm font-mono">
        {children}
      </code>
    );
  },
  pre: ({ children }) => (
    <pre className="bg-dark rounded-xl border border-border overflow-hidden my-4">{children}</pre>
  ),
  table: ({ children }) => (
    <div className="overflow-x-auto my-5 rounded-xl border border-border">
      <table className="w-full text-sm">{children}</table>
    </div>
  ),
  thead: ({ children }) => (
    <thead className="bg-dark-light border-b border-border">{children}</thead>
  ),
  tbody: ({ children }) => (
    <tbody className="divide-y divide-border">{children}</tbody>
  ),
  tr: ({ children }) => (
    <tr className="hover:bg-dark-light/50 transition-colors">{children}</tr>
  ),
  th: ({ children }) => (
    <th className="px-4 py-3 text-left text-xs font-semibold text-gray-400 uppercase tracking-wider">
      {children}
    </th>
  ),
  td: ({ children }) => (
    <td className="px-4 py-3 text-gray-300">{children}</td>
  ),
  hr: () => (
    <hr className="my-8 border-none h-px bg-gradient-to-r from-transparent via-border-light to-transparent" />
  ),
  a: ({ href, children }) => (
    <a href={href} className="text-primary-light hover:text-primary underline underline-offset-2 transition-colors">
      {children}
    </a>
  ),
};

/* ================================================================
   主组件
   ================================================================ */
export const Reports: React.FC = () => {
  const [reports, setReports] = useState<ReportSummary[]>([]);
  const [loading, setLoading] = useState(false);
  const [totalReports, setTotalReports] = useState(0);

  const [reportType, setReportType] = useState('daily');
  const [model, setModel] = useState('gpt-5.5');

  const [view, setView] = useState<View>('list');
  const [generating, setGenerating] = useState(false);
  const [currentReport, setCurrentReport] = useState<ReportDetail | null>(null);
  const [genMeta, setGenMeta] = useState<{ reportId?: string; title?: string; tokenCount?: number; genTime?: number }>({});
  const [collectingMsg, setCollectingMsg] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  const [activeChapter, setActiveChapter] = useState<number | null>(null);
  const [generatingChapter, setGeneratingChapter] = useState<number | null>(null);
  const [renderTick, setRenderTick] = useState(0);

  const abortRef = useRef<AbortController | null>(null);
  const rafRef = useRef(0);
  const userInteractedRef = useRef(false);
  const chapterContentsRef = useRef<Record<number, string>>({});
  const currentGenChapterRef = useRef<number | null>(null);

  const loadReports = useCallback(async () => {
    setLoading(true);
    try {
      const res = await reportService.getReports({ limit: 50 });
      setReports(res.reports || []);
      setTotalReports(res.total || 0);
    } catch (e) {
      console.error('加载报告列表失败:', e);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => { loadReports(); }, [loadReports]);

  const handleGenerate = async () => {
    setView('generate');
    setGenerating(true);
    setError(null);
    setGenMeta({});
    setCollectingMsg(null);
    setActiveChapter(null);
    userInteractedRef.current = false;
    setGeneratingChapter(null);
    chapterContentsRef.current = {};
    currentGenChapterRef.current = null;
    setRenderTick(0);

    const controller = new AbortController();
    abortRef.current = controller;

    try {
      await reportService.generateReport(
        { report_type: reportType, model },
        (event: ReportStreamEvent) => {
          if (event.event === 'collecting') {
            setCollectingMsg(event.message || '正在收集数据...');
          } else if (event.event === 'start') {
            setGenMeta({ reportId: event.report_id, title: event.title });
          } else if (event.event === 'chapter_progress') {
            setCollectingMsg(event.label || `正在生成第${event.call_index}/${event.total_calls}部分...`);
            const targetCh = event.chapters?.[0];
            if (targetCh != null) {
              currentGenChapterRef.current = targetCh;
              setGeneratingChapter(targetCh);
              // 初始化该章节的内容 buffer
              if (!(targetCh in chapterContentsRef.current)) {
                chapterContentsRef.current[targetCh] = '';
              }
              // 只有第一个章节自动切换，后续章节不抢焦点
              if (event.call_index === 1) {
                setActiveChapter(targetCh);
              }
            }
            // 强制触发 useMemo 重算，确保前序章节已积累的内容能被渲染
            // （rAF 可能被流式读取的微任务链阻塞而延迟）
            setRenderTick(t => t + 1);
          } else if (event.event === 'chapter_complete') {
            // 强制触发 useMemo 重算，保证已完成的章节立即可见
            setRenderTick(t => t + 1);
            // Ch1 是最后生成的，生成完毕后自动跳到第1章
            if (event.chapter_number === 1) {
              setActiveChapter(1);
              userInteractedRef.current = false;
            }
          } else if (event.event === 'chunk') {
            // 按章节独立存储，互不干扰
            const ch = currentGenChapterRef.current;
            if (ch != null) {
              chapterContentsRef.current[ch] = (chapterContentsRef.current[ch] || '') + (event.content || '');
            }
            if (!rafRef.current) {
              rafRef.current = requestAnimationFrame(() => {
                rafRef.current = 0;
                setRenderTick(t => t + 1);
              });
            }
          } else if (event.event === 'done') {
            if (rafRef.current) { cancelAnimationFrame(rafRef.current); rafRef.current = 0; }
            setRenderTick(t => t + 1);
            setGeneratingChapter(null);
            currentGenChapterRef.current = null;
            setGenMeta(prev => ({ ...prev, reportId: event.report_id, tokenCount: event.token_count, genTime: event.generation_time }));
          } else if (event.event === 'error') {
            setError(event.message || '生成失败');
          }
        },
        controller.signal,
      );
    } catch (e: unknown) {
      if (e instanceof Error && e.name === 'AbortError') { setError('已取消生成'); }
      else { setError(e instanceof Error ? e.message : '生成失败'); }
    } finally {
      if (rafRef.current) { cancelAnimationFrame(rafRef.current); rafRef.current = 0; }
      setGenerating(false);
      abortRef.current = null;
      loadReports();
    }
  };

  const handleCancel = () => { abortRef.current?.abort(); };

  const handleViewReport = async (reportId: string) => {
    setView('view');
    setCurrentReport(null);
    setError(null);
    setActiveChapter(null);
    try { setCurrentReport(await reportService.getReport(reportId)); }
    catch { setError('加载报告失败'); }
  };

  const handleDelete = async (reportId: string) => {
    try { await reportService.deleteReport(reportId); loadReports(); }
    catch (e) { console.error('删除失败:', e); }
  };

  const handleBack = () => {
    setView('list'); setCurrentReport(null); setGenMeta({}); setCollectingMsg(null); setError(null); setActiveChapter(null); userInteractedRef.current = false; setGeneratingChapter(null);
    chapterContentsRef.current = {}; currentGenChapterRef.current = null;
  };

  /* ==================== 章节解析与导航 ==================== */
  const displayTitle = view === 'generate' ? genMeta.title : currentReport?.title;
  const displayMeta = view === 'generate'
    ? { model, tokenCount: genMeta.tokenCount, genTime: genMeta.genTime }
    : { model: currentReport?.model_used, tokenCount: currentReport?.token_count, genTime: currentReport?.generation_time_seconds };

  const chapters = useMemo((): Chapter[] => {
    if (view === 'generate') {
      // 生成模式：从按章节独立存储的 buffer 中派生
      const contents = chapterContentsRef.current;
      return Object.keys(contents)
        .map(Number)
        .filter(num => contents[num].trim().length > 0)
        .sort((a, b) => a - b)
        .map(num => {
          const raw = contents[num];
          // 去除续批 GPT 可能重复输出的二级标题（保留第一次出现，删除后续重复）
          const headerPattern = new RegExp(`^##\\s+${num}[.\\s、：:][^\\n]*\\n?`, 'gm');
          let firstSeen = false;
          const content = raw.replace(headerPattern, (match) => {
            if (!firstSeen) { firstSeen = true; return match; }
            return '';
          });
          const firstLine = content.split('\n')[0] || '';
          const title = firstLine.replace(/^##\s*\d+[.\s、：:]\s*/, '').trim();
          return { number: num, title: title || `第${num}章`, content };
        });
    }
    // 查看模式：从保存的报告内容中解析
    return parseChapters(currentReport?.content || '');
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [view, renderTick, currentReport?.content]);

  const currentChapter = activeChapter !== null
    ? chapters.find(ch => ch.number === activeChapter)
    : chapters[0];
  const currentChapterIndex = currentChapter
    ? chapters.indexOf(currentChapter)
    : -1;

  const goPrev = () => {
    if (currentChapterIndex > 0) setActiveChapter(chapters[currentChapterIndex - 1].number);
  };
  const goNext = () => {
    if (currentChapterIndex < chapters.length - 1) setActiveChapter(chapters[currentChapterIndex + 1].number);
  };

  // 章节 tab 点击：标记用户已手动交互
  const handleChapterClick = (chNumber: number) => {
    setActiveChapter(chNumber);
    if (generating) {
      userInteractedRef.current = true;
    }
  };

  /* ==================== 日历状态 ==================== */
  const now = new Date();
  const [calYear, setCalYear] = useState(now.getFullYear());
  const [calMonth, setCalMonth] = useState(now.getMonth());
  const [selectedDay, setSelectedDay] = useState<number | null>(now.getDate());
  const [showGenConfig, setShowGenConfig] = useState(false);

  const goMonthPrev = () => {
    if (calMonth === 0) { setCalYear(y => y - 1); setCalMonth(11); }
    else setCalMonth(m => m - 1);
    setSelectedDay(null);
  };
  const goMonthNext = () => {
    if (calMonth === 11) { setCalYear(y => y + 1); setCalMonth(0); }
    else setCalMonth(m => m + 1);
    setSelectedDay(null);
  };
  const goToday = () => {
    const t = new Date();
    setCalYear(t.getFullYear());
    setCalMonth(t.getMonth());
    setSelectedDay(t.getDate());
  };

  // 按日期分组报告
  const reportsByDay = useMemo(() => {
    const map = new Map<number, ReportSummary[]>();
    for (const r of reports) {
      if (!r.created_at) continue;
      const d = new Date(r.created_at);
      if (d.getFullYear() === calYear && d.getMonth() === calMonth) {
        const day = d.getDate();
        if (!map.has(day)) map.set(day, []);
        map.get(day)!.push(r);
      }
    }
    return map;
  }, [reports, calYear, calMonth]);

  // 选中日期的报告
  const selectedReports = selectedDay ? (reportsByDay.get(selectedDay) || []) : [];

  // 日历网格数据
  const calendarGrid = useMemo(() => {
    const totalDays = getMonthDays(calYear, calMonth);
    const firstDow = getFirstDayOfWeek(calYear, calMonth);
    const cells: (number | null)[] = [];
    for (let i = 0; i < firstDow; i++) cells.push(null);
    for (let d = 1; d <= totalDays; d++) cells.push(d);
    while (cells.length % 7 !== 0) cells.push(null);
    return cells;
  }, [calYear, calMonth]);

  /* ==================== 列表视图 ==================== */
  if (view === 'list') {
    return (
      <div className="min-h-screen bg-gradient-dark p-3 md:p-6 pb-20 md:pb-6">
        <div className="max-w-6xl mx-auto space-y-4 md:space-y-5">
          {/* 头部 */}
          <div className="flex items-center justify-between">
            <div>
              <div className="flex items-center gap-4">
                <h1 className="text-2xl md:text-3xl font-bold bg-gradient-to-r from-violet-400 to-purple-300 bg-clip-text text-transparent">
                  AI 分析报告
                </h1>
                <MarketSelector />
              </div>
              <p className="text-gray-500 text-sm mt-1">基于量化数据的智能投资分析</p>
            </div>
            <button
              onClick={() => setShowGenConfig(!showGenConfig)}
              className="group relative px-3 py-1.5 md:px-5 md:py-2 text-sm bg-gradient-to-r from-violet-500 to-purple-600 text-white rounded-xl
                hover:from-violet-600 hover:to-purple-700 shadow-lg shadow-purple-500/25
                hover:shadow-purple-500/40 transition-all duration-300 flex items-center gap-2 font-medium"
            >
              <svg className="h-4 w-4 transition-transform group-hover:scale-110" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M13 10V3L4 14h7v7l9-11h-7z" />
              </svg>
              生成报告
            </button>
          </div>

          {/* 生成配置卡片 — 折叠 */}
          {showGenConfig && (
            <div className="bg-gradient-card border border-violet-500/30 shadow-card p-4 rounded-2xl animate-slide-in-right">
              <div className="grid grid-cols-1 md:grid-cols-3 gap-3 items-end">
                <div>
                  <label className="block text-xs font-medium text-gray-500 mb-1.5 uppercase tracking-wider">报告类型</label>
                  <select
                    value={reportType}
                    onChange={e => setReportType(e.target.value)}
                    className="w-full px-3 py-2 bg-dark text-white rounded-xl border border-border
                      focus:border-violet-500 focus:ring-2 focus:ring-violet-500/20 outline-none transition-all text-sm"
                  >
                    <option value="daily">日报</option>
                    <option value="weekly">周报</option>
                    <option value="monthly">月报</option>
                  </select>
                </div>
                <div>
                  <label className="block text-xs font-medium text-gray-500 mb-1.5 uppercase tracking-wider">AI 模型</label>
                  <select
                    value={model}
                    onChange={e => setModel(e.target.value)}
                    className="w-full px-3 py-2 bg-dark text-white rounded-xl border border-border
                      focus:border-violet-500 focus:ring-2 focus:ring-violet-500/20 outline-none transition-all text-sm"
                  >
                    <optgroup label="推荐（投资建议用最强）">
                      <option value="gpt-5.5">GPT-5.5 ~¥3/份（最强）</option>
                      <option value="gpt-5.4">GPT-5.4 ~¥1.5/份</option>
                    </optgroup>
                    <optgroup label="省钱">
                      <option value="gpt-5.4-mini">GPT-5.4 Mini ~¥0.2/份</option>
                      <option value="gpt-5.4-nano">GPT-5.4 Nano ~¥0.05/份</option>
                    </optgroup>
                  </select>
                </div>
                <button
                  onClick={() => { setShowGenConfig(false); handleGenerate(); }}
                  className="px-4 py-2 bg-violet-500 hover:bg-violet-600 text-white rounded-xl transition-colors text-sm font-medium"
                >
                  开始生成
                </button>
              </div>
            </div>
          )}

          {/* ========== 日历 + 报告面板 ========== */}
          <div className="grid grid-cols-1 lg:grid-cols-5 gap-4 md:gap-5">
            {/* 日历面板 */}
            <div className="lg:col-span-3 bg-gradient-card border border-border rounded-2xl overflow-hidden">
              {/* 月份导航 */}
              <div className="flex items-center justify-between px-4 py-3 border-b border-border">
                <button onClick={goMonthPrev} className="p-1.5 rounded-lg text-gray-400 hover:text-white hover:bg-dark-light transition-all">
                  <svg className="h-5 w-5" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M15 19l-7-7 7-7" /></svg>
                </button>
                <div className="flex items-center gap-3">
                  <h2 className="text-lg font-bold text-white">{calYear} 年 {calMonth + 1} 月</h2>
                  <button onClick={goToday} className="px-2 py-0.5 text-xs text-violet-400 bg-violet-500/10 rounded-md hover:bg-violet-500/20 transition-colors">
                    今天
                  </button>
                </div>
                <button onClick={goMonthNext} className="p-1.5 rounded-lg text-gray-400 hover:text-white hover:bg-dark-light transition-all">
                  <svg className="h-5 w-5" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M9 5l7 7-7 7" /></svg>
                </button>
              </div>

              {/* 星期表头 */}
              <div className="grid grid-cols-7 border-b border-border">
                {WEEKDAY_LABELS.map((w, i) => (
                  <div key={w} className={`text-center text-xs font-medium py-2 ${isWeekend(i) ? 'text-gray-600' : 'text-gray-500'}`}>
                    {w}
                  </div>
                ))}
              </div>

              {/* 日期网格 */}
              <div className="grid grid-cols-7">
                {calendarGrid.map((day, idx) => {
                  if (day === null) return <div key={`empty-${idx}`} className="aspect-square border-b border-r border-border/30" />;
                  const dow = idx % 7;
                  const dayReports = reportsByDay.get(day) || [];
                  const hasReports = dayReports.length > 0;
                  const isSelected = selectedDay === day;
                  const isTodayCell = isToday(day, calMonth, calYear);
                  const weekendCell = isWeekend(dow);

                  return (
                    <button
                      key={day}
                      onClick={() => setSelectedDay(isSelected ? null : day)}
                      className={`relative aspect-square border-b border-r border-border/30 flex flex-col items-center justify-center gap-0.5 transition-all duration-200 group/cell
                        ${isSelected
                          ? 'bg-violet-500/20 ring-1 ring-inset ring-violet-500/50'
                          : hasReports
                          ? 'hover:bg-dark-light/80'
                          : 'hover:bg-dark-light/40'
                        }
                        ${weekendCell && !isSelected ? 'bg-dark/30' : ''}
                      `}
                    >
                      {/* 今天的发光边框 */}
                      {isTodayCell && (
                        <span className="absolute inset-1 rounded-lg border border-violet-500/60 pointer-events-none" />
                      )}

                      {/* 日期数字 */}
                      <span className={`text-sm font-medium transition-colors z-10
                        ${isSelected ? 'text-violet-300' : isTodayCell ? 'text-violet-400' : weekendCell ? 'text-gray-600' : 'text-gray-400'}
                        ${hasReports && !isSelected ? 'text-white' : ''}
                      `}>
                        {day}
                      </span>

                      {/* 报告指示点 */}
                      {hasReports && (
                        <div className="flex items-center gap-0.5 z-10">
                          {dayReports.slice(0, 3).map((r, i) => (
                            <span key={i} className={`w-1.5 h-1.5 rounded-full ${
                              r.report_type === 'daily' ? 'bg-green-400' :
                              r.report_type === 'weekly' ? 'bg-blue-400' : 'bg-amber-400'
                            }`} />
                          ))}
                          {dayReports.length > 3 && (
                            <span className="text-[8px] text-gray-500">+{dayReports.length - 3}</span>
                          )}
                        </div>
                      )}
                    </button>
                  );
                })}
              </div>

              {/* 图例 */}
              <div className="flex items-center justify-center gap-4 py-2.5 border-t border-border text-xs text-gray-500">
                <span className="flex items-center gap-1.5"><span className="w-2 h-2 rounded-full bg-green-400" />日报</span>
                <span className="flex items-center gap-1.5"><span className="w-2 h-2 rounded-full bg-blue-400" />周报</span>
                <span className="flex items-center gap-1.5"><span className="w-2 h-2 rounded-full bg-amber-400" />月报</span>
                <span className="text-gray-600">|</span>
                <span>共 {totalReports} 份报告</span>
              </div>
            </div>

            {/* 报告列表面板 */}
            <div className="lg:col-span-2">
              <div className="bg-gradient-card border border-border rounded-2xl overflow-hidden h-full flex flex-col">
                {/* 面板标题 */}
                <div className="px-4 py-3 border-b border-border flex items-center justify-between">
                  <h3 className="text-sm font-semibold text-white">
                    {selectedDay
                      ? `${calMonth + 1}月${selectedDay}日 — ${selectedReports.length} 份报告`
                      : '选择日期查看报告'
                    }
                  </h3>
                </div>

                {/* 报告卡片列表 */}
                <div className="flex-1 overflow-y-auto scrollbar-hide p-3 space-y-2.5">
                  {loading ? (
                    <div className="text-center py-12 text-gray-500">
                      <svg className="animate-spin h-6 w-6 mx-auto mb-2 text-gray-600" viewBox="0 0 24 24">
                        <circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4" fill="none" />
                        <path className="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4zm2 5.291A7.962 7.962 0 014 12H0c0 3.042 1.135 5.824 3 7.938l3-2.647z" />
                      </svg>
                      加载中...
                    </div>
                  ) : !selectedDay ? (
                    <div className="text-center py-12">
                      <div className="text-3xl mb-3 opacity-20">📅</div>
                      <div className="text-gray-500 text-sm">点击日历中的日期</div>
                      <div className="text-gray-600 text-xs mt-1">查看当日生成的报告</div>
                    </div>
                  ) : selectedReports.length === 0 ? (
                    <div className="text-center py-12">
                      <div className="text-3xl mb-3 opacity-20">📄</div>
                      <div className="text-gray-500 text-sm">该日暂无报告</div>
                      <div className="text-gray-600 text-xs mt-1">点击右上角"生成报告"创建</div>
                    </div>
                  ) : (
                    selectedReports.map((r) => (
                      <div
                        key={r.report_id}
                        onClick={() => handleViewReport(r.report_id)}
                        className="group bg-dark-light/50 border border-border/50 rounded-xl p-3
                          hover:border-violet-500/30 hover:bg-dark-light
                          transition-all duration-200 cursor-pointer"
                      >
                        {/* 标签 + 状态 */}
                        <div className="flex items-center gap-2 mb-1.5">
                          <span className={`px-2 py-0.5 rounded text-[10px] font-semibold ${
                            r.report_type === 'daily' ? 'bg-green-500/15 text-green-400' :
                            r.report_type === 'weekly' ? 'bg-blue-500/15 text-blue-400' :
                            'bg-amber-500/15 text-amber-400'
                          }`}>
                            {r.report_type === 'daily' ? '日报' : r.report_type === 'weekly' ? '周报' : '月报'}
                          </span>
                          <span className={`px-2 py-0.5 rounded text-[10px] font-medium ${
                            r.status === 'completed' ? 'bg-green-500/15 text-green-400' :
                            r.status === 'failed' ? 'bg-red-500/15 text-red-400' :
                            'bg-yellow-500/15 text-yellow-400'
                          }`}>
                            {r.status === 'completed' ? '已完成' : r.status === 'failed' ? '失败' : '生成中'}
                          </span>
                        </div>

                        {/* 标题 */}
                        <div className="text-white text-sm font-medium truncate mb-1.5 group-hover:text-violet-300 transition-colors">
                          {r.title}
                        </div>

                        {/* 元信息 */}
                        <div className="flex items-center gap-2 text-[10px] text-gray-500">
                          <span>{r.model_used}</span>
                          {r.token_count != null && <span>{r.token_count} tokens</span>}
                          {r.generation_time_seconds != null && <span>{r.generation_time_seconds.toFixed(1)}s</span>}
                        </div>

                        {/* 右箭头 + 删除 */}
                        <div className="flex items-center justify-end gap-1 mt-1">
                          <button
                            onClick={e => { e.stopPropagation(); handleDelete(r.report_id); }}
                            className="p-1 rounded text-gray-600 hover:text-red-400 hover:bg-red-500/10 transition-all opacity-0 group-hover:opacity-100"
                            title="删除"
                          >
                            <svg className="h-3.5 w-3.5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M19 7l-.867 12.142A2 2 0 0116.138 21H7.862a2 2 0 01-1.995-1.858L5 7m5 4v6m4-6v6m1-10V4a1 1 0 00-1-1h-4a1 1 0 00-1 1v3M4 7h16" />
                            </svg>
                          </button>
                          <svg className="h-4 w-4 text-gray-600 group-hover:text-violet-400 transition-colors" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                            <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M9 5l7 7-7 7" />
                          </svg>
                        </div>
                      </div>
                    ))
                  )}
                </div>
              </div>
            </div>
          </div>
        </div>
      </div>
    );
  }

  /* ==================== 查看 / 生成视图 ==================== */
  return (
    <div className="min-h-screen bg-gradient-dark p-3 md:p-6 pb-20 md:pb-6">
      <div className="max-w-4xl mx-auto space-y-4 md:space-y-5">
        {/* 顶部栏 */}
        <div className="flex items-center justify-between">
          <button
            onClick={handleBack}
            className="flex items-center gap-2 text-gray-400 hover:text-white transition-colors group"
          >
            <svg className="h-5 w-5 transition-transform group-hover:-translate-x-0.5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M15 19l-7-7 7-7" />
            </svg>
            返回列表
          </button>

          <div className="flex items-center gap-3">
            {/* PDF 下载按钮 — 仅在查看已完成报告或生成完毕后显示 */}
            {!generating && (view === 'view' ? currentReport?.report_id : genMeta.reportId) && (
              <button
                onClick={async () => {
                  const rid = view === 'view' ? currentReport!.report_id : genMeta.reportId!;
                  const t = displayTitle || rid;
                  try {
                    await reportService.downloadPdf(rid, t);
                  } catch (e) {
                    console.error('PDF 下载失败:', e);
                    setError(e instanceof Error ? e.message : 'PDF 下载失败');
                  }
                }}
                className="px-4 py-2 bg-violet-500/15 text-violet-300 border border-violet-500/20 rounded-xl
                  hover:bg-violet-500/25 transition-all flex items-center gap-2 text-sm"
              >
                <svg className="h-4 w-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                  <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M12 10v6m0 0l-3-3m3 3l3-3m2 8H7a2 2 0 01-2-2V5a2 2 0 012-2h5.586a1 1 0 01.707.293l5.414 5.414a1 1 0 01.293.707V19a2 2 0 01-2 2z" />
                </svg>
                下载 PDF
              </button>
            )}

            {generating && (
              <button
                onClick={handleCancel}
                className="px-4 py-2 bg-red-500/15 text-red-400 border border-red-500/20 rounded-xl
                  hover:bg-red-500/25 transition-all flex items-center gap-2 text-sm"
              >
                <svg className="h-4 w-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                  <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M6 18L18 6M6 6l12 12" />
                </svg>
                取消生成
              </button>
            )}
          </div>
        </div>

        {/* 报告头部卡片 */}
        <div className="bg-gradient-card border border-border rounded-2xl overflow-hidden">
          {/* 渐变顶部条 */}
          <div className="h-1 bg-gradient-to-r from-violet-500 via-purple-500 to-accent-cyan" />
          <div className="p-3 md:p-5">
            <div className="flex flex-col gap-2 md:flex-row md:items-start md:justify-between">
              <div className="flex-1">
                <h1 className="text-lg md:text-xl font-bold text-white mb-2">
                  {displayTitle || (generating ? '正在生成报告...' : '加载中...')}
                </h1>
                <div className="flex items-center gap-4 text-sm text-gray-500">
                  {displayMeta.model && (
                    <span className="flex items-center gap-1.5 bg-dark px-2.5 py-1 rounded-lg">
                      <svg className="h-3.5 w-3.5 text-violet-400" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M9.75 17L9 20l-1 1h8l-1-1-.75-3M3 13h18M5 17h14a2 2 0 002-2V5a2 2 0 00-2-2H5a2 2 0 00-2 2v10a2 2 0 002 2z" /></svg>
                      {displayMeta.model}
                    </span>
                  )}
                  {displayMeta.tokenCount != null && (
                    <span className="flex items-center gap-1.5 bg-dark px-2.5 py-1 rounded-lg">
                      <svg className="h-3.5 w-3.5 text-accent-cyan" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M7 7h.01M7 3h5c.512 0 1.024.195 1.414.586l7 7a2 2 0 010 2.828l-7 7a2 2 0 01-2.828 0l-7-7A2 2 0 013 12V7a4 4 0 014-4z" /></svg>
                      {displayMeta.tokenCount} tokens
                    </span>
                  )}
                  {displayMeta.genTime != null && (
                    <span className="flex items-center gap-1.5 bg-dark px-2.5 py-1 rounded-lg">
                      <svg className="h-3.5 w-3.5 text-bull" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M12 8v4l3 3m6-3a9 9 0 11-18 0 9 9 0 0118 0z" /></svg>
                      {displayMeta.genTime.toFixed(1)}s
                    </span>
                  )}
                </div>
              </div>
              {generating && (
                <div className="flex items-center gap-2.5 bg-violet-500/10 px-4 py-2 rounded-xl border border-violet-500/20">
                  <span className="relative flex h-2.5 w-2.5">
                    <span className="animate-ping absolute inline-flex h-full w-full rounded-full bg-violet-400 opacity-75" />
                    <span className="relative inline-flex rounded-full h-2.5 w-2.5 bg-violet-500" />
                  </span>
                  <span className="text-sm text-violet-300 font-medium">
                    {collectingMsg || '生成中'}
                  </span>
                </div>
              )}
            </div>
          </div>
        </div>

        {/* 错误提示 */}
        {error && (
          <div className="p-4 rounded-xl border bg-red-500/10 border-red-500/20 text-red-400 flex items-center gap-3">
            <svg className="h-5 w-5 shrink-0" fill="none" stroke="currentColor" viewBox="0 0 24 24">
              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M12 8v4m0 4h.01M21 12a9 9 0 11-18 0 9 9 0 0118 0z" />
            </svg>
            {error}
          </div>
        )}

        {/* 报告正文 — 分章节展示 */}
        <div className="bg-gradient-card border border-border rounded-2xl overflow-hidden">
          {chapters.length > 0 ? (
            <>
              {/* 章节标签栏 */}
              <div className="border-b border-border px-4 pt-3 pb-0">
                <div className="flex gap-1 overflow-x-auto" style={{ scrollbarWidth: 'none' }}>
                  {chapters.map((ch) => {
                    const isActive = currentChapter?.number === ch.number;
                    const isGenerating = generating && generatingChapter === ch.number;
                    return (
                      <button
                        key={ch.number}
                        onClick={() => handleChapterClick(ch.number)}
                        className={`relative px-4 py-2.5 text-sm font-medium whitespace-nowrap rounded-t-lg transition-all duration-200 ${
                          isActive
                            ? 'text-violet-300 bg-dark-lighter'
                            : 'text-gray-500 hover:text-gray-300 hover:bg-dark-light/50'
                        }`}
                      >
                        <span className="flex items-center gap-1.5">
                          <span className={`inline-flex items-center justify-center w-5 h-5 rounded text-xs font-bold ${
                            isGenerating
                              ? 'bg-violet-500/50 text-violet-200 animate-pulse'
                              : isActive
                              ? 'bg-violet-500/30 text-violet-300'
                              : 'bg-dark-light text-gray-500'
                          }`}>
                            {ch.number}
                          </span>
                          {ch.title}
                        </span>
                        {isActive && (
                          <span className="absolute bottom-0 left-2 right-2 h-0.5 bg-gradient-to-r from-violet-500 to-purple-500 rounded-full" />
                        )}
                      </button>
                    );
                  })}
                </div>
              </div>

              {/* 当前章节内容 */}
              <div className="px-4 py-6 md:px-8 md:py-10">
                {currentChapter ? (
                  <ReactMarkdown remarkPlugins={[remarkGfm]} components={mdComponents}>
                    {currentChapter.content}
                  </ReactMarkdown>
                ) : (
                  <div className="text-center py-8 text-gray-500">选择一个章节查看</div>
                )}
                {generating && currentChapter && generatingChapter === currentChapter.number && (
                  <span className="inline-block w-2 h-5 bg-violet-400 rounded-sm animate-pulse ml-0.5 align-text-bottom" />
                )}
              </div>

              {/* 底部翻页导航 */}
              <div className="border-t border-border px-6 py-3 flex items-center justify-between">
                <button
                  onClick={goPrev}
                  disabled={currentChapterIndex <= 0}
                  className="flex items-center gap-1.5 px-4 py-2 text-sm rounded-lg transition-all
                    disabled:text-gray-600 disabled:cursor-not-allowed
                    text-gray-400 hover:text-white hover:bg-dark-light"
                >
                  <svg className="h-4 w-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                    <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M15 19l-7-7 7-7" />
                  </svg>
                  上一章
                </button>
                <span className="text-sm text-gray-500">
                  {currentChapterIndex >= 0 ? currentChapterIndex + 1 : 0} / {chapters.length} 章
                </span>
                <button
                  onClick={goNext}
                  disabled={currentChapterIndex >= chapters.length - 1}
                  className="flex items-center gap-1.5 px-4 py-2 text-sm rounded-lg transition-all
                    disabled:text-gray-600 disabled:cursor-not-allowed
                    text-gray-400 hover:text-white hover:bg-dark-light"
                >
                  下一章
                  <svg className="h-4 w-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                    <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M9 5l7 7-7 7" />
                  </svg>
                </button>
              </div>
            </>
          ) : (
            <div className="px-4 py-6 md:px-8 md:py-10">
              {!generating && !error && view === 'view' && !currentReport ? (
                <div className="text-center py-16 text-gray-500">
                  <svg className="animate-spin h-8 w-8 mx-auto mb-3 text-gray-600" viewBox="0 0 24 24">
                    <circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4" fill="none" />
                    <path className="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4zm2 5.291A7.962 7.962 0 014 12H0c0 3.042 1.135 5.824 3 7.938l3-2.647z" />
                  </svg>
                  加载中...
                </div>
              ) : generating && collectingMsg ? (
                <div className="text-center py-16">
                  <svg className="animate-spin h-10 w-10 mx-auto mb-4 text-violet-500" viewBox="0 0 24 24">
                    <circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4" fill="none" />
                    <path className="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4zm2 5.291A7.962 7.962 0 014 12H0c0 3.042 1.135 5.824 3 7.938l3-2.647z" />
                  </svg>
                  <p className="text-violet-300 text-lg font-medium">{collectingMsg}</p>
                  <p className="text-gray-500 text-sm mt-2">正在从东方财富获取指数、板块、概念、资金流向、新闻等数据...</p>
                </div>
              ) : null}
            </div>
          )}
        </div>
      </div>
    </div>
  );
};

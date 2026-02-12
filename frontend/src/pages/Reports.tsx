import React, { useState, useEffect, useRef, useCallback, useMemo } from 'react';
import ReactMarkdown from 'react-markdown';
import remarkGfm from 'remark-gfm';
import type { Components } from 'react-markdown';
import { reportService } from '../services/reportService';
import type { ReportSummary, ReportDetail, ReportStreamEvent } from '../services/reportService';

type View = 'list' | 'view' | 'generate';

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

  const chapters: Chapter[] = [];
  const headerRegex = /^## (\d+)[.\s、：:]/gm;
  const matches: { number: number; index: number }[] = [];

  let m;
  while ((m = headerRegex.exec(markdown)) !== null) {
    matches.push({ number: parseInt(m[1]), index: m.index });
  }

  if (matches.length === 0) return [];

  for (let i = 0; i < matches.length; i++) {
    const start = matches[i].index;
    const end = i + 1 < matches.length ? matches[i + 1].index : markdown.length;
    const section = markdown.slice(start, end).trimEnd();
    const firstLine = section.split('\n')[0];
    const title = firstLine.replace(/^##\s*\d+[.\s、：:]\s*/, '').trim();

    chapters.push({
      number: matches[i].number,
      title: title || `第${matches[i].number}章`,
      content: section,
    });
  }

  chapters.sort((a, b) => a.number - b.number);
  return chapters;
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

  const [reportType, setReportType] = useState('weekly');
  const [model, setModel] = useState('gpt-4.1');

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
          const content = contents[num];
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

  /* ==================== 列表视图 ==================== */
  if (view === 'list') {
    return (
      <div className="min-h-screen bg-gradient-dark p-6">
        <div className="max-w-5xl mx-auto space-y-6">
          {/* 头部 */}
          <div className="flex items-center justify-between">
            <div>
              <h1 className="text-3xl font-bold bg-gradient-to-r from-violet-400 to-purple-300 bg-clip-text text-transparent">
                AI 分析报告
              </h1>
              <p className="text-gray-500 text-sm mt-1">基于量化数据的智能投资分析</p>
            </div>
            <button
              onClick={handleGenerate}
              className="group relative px-6 py-2.5 bg-gradient-to-r from-violet-500 to-purple-600 text-white rounded-xl
                hover:from-violet-600 hover:to-purple-700 shadow-lg shadow-purple-500/25
                hover:shadow-purple-500/40 transition-all duration-300 flex items-center gap-2.5 font-medium"
            >
              <svg className="h-5 w-5 transition-transform group-hover:scale-110" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M13 10V3L4 14h7v7l9-11h-7z" />
              </svg>
              生成报告
            </button>
          </div>

          {/* 配置卡片 */}
          <div className="bg-gradient-card border border-border shadow-card p-5 rounded-2xl">
            <div className="flex items-center gap-2 mb-4">
              <svg className="h-4 w-4 text-gray-400" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M10.325 4.317c.426-1.756 2.924-1.756 3.35 0a1.724 1.724 0 002.573 1.066c1.543-.94 3.31.826 2.37 2.37a1.724 1.724 0 001.066 2.573c1.756.426 1.756 2.924 0 3.35a1.724 1.724 0 00-1.066 2.573c.94 1.543-.826 3.31-2.37 2.37a1.724 1.724 0 00-2.573 1.066c-.426 1.756-2.924 1.756-3.35 0a1.724 1.724 0 00-2.573-1.066c-1.543.94-3.31-.826-2.37-2.37a1.724 1.724 0 00-1.066-2.573c-1.756-.426-1.756-2.924 0-3.35a1.724 1.724 0 001.066-2.573c-.94-1.543.826-3.31 2.37-2.37.996.608 2.296.07 2.572-1.065z" />
                <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M15 12a3 3 0 11-6 0 3 3 0 016 0z" />
              </svg>
              <span className="text-sm font-medium text-gray-400">报告配置</span>
            </div>
            <div className="grid grid-cols-2 gap-4">
              <div>
                <label className="block text-xs font-medium text-gray-500 mb-1.5 uppercase tracking-wider">报告类型</label>
                <select
                  value={reportType}
                  onChange={e => setReportType(e.target.value)}
                  className="w-full px-3.5 py-2.5 bg-dark text-white rounded-xl border border-border
                    focus:border-violet-500 focus:ring-2 focus:ring-violet-500/20 outline-none transition-all"
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
                  className="w-full px-3.5 py-2.5 bg-dark text-white rounded-xl border border-border
                    focus:border-violet-500 focus:ring-2 focus:ring-violet-500/20 outline-none transition-all"
                >
                  <optgroup label="推荐">
                    <option value="gpt-4.1">GPT-4.1 — 周报/月报首选 ~¥1.0/份</option>
                    <option value="gpt-4.1-mini">GPT-4.1 Mini — 日报首选 ~¥0.2/份</option>
                    <option value="gpt-4.1-nano">GPT-4.1 Nano — 极速省钱 ~¥0.05/份</option>
                  </optgroup>
                  <optgroup label="其他">
                    <option value="gpt-4o">GPT-4o — 高质量 ~¥1.5/份</option>
                    <option value="gpt-4o-mini">GPT-4o Mini — 快速 ~¥0.1/份</option>
                    <option value="o3-mini">o3-mini — 深度推理 ~¥0.7/份</option>
                  </optgroup>
                </select>
              </div>
            </div>
          </div>

          {/* 报告列表 */}
          <div>
            <div className="flex items-center justify-between mb-4">
              <h2 className="text-lg font-semibold text-white">
                历史报告
                {totalReports > 0 && <span className="text-sm text-gray-500 font-normal ml-2">({totalReports})</span>}
              </h2>
            </div>

            {loading ? (
              <div className="text-center py-16 text-gray-500">
                <svg className="animate-spin h-8 w-8 mx-auto mb-3 text-gray-600" viewBox="0 0 24 24">
                  <circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4" fill="none" />
                  <path className="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4zm2 5.291A7.962 7.962 0 014 12H0c0 3.042 1.135 5.824 3 7.938l3-2.647z" />
                </svg>
                加载中...
              </div>
            ) : reports.length === 0 ? (
              <div className="bg-gradient-card border border-border rounded-2xl text-center py-16">
                <div className="text-5xl mb-4 opacity-30">📄</div>
                <div className="text-gray-400 text-lg mb-1">暂无报告</div>
                <div className="text-gray-600 text-sm">点击上方"生成报告"创建你的第一份 AI 分析报告</div>
              </div>
            ) : (
              <div className="space-y-3">
                {reports.map((r) => (
                  <div
                    key={r.report_id}
                    onClick={() => handleViewReport(r.report_id)}
                    className="group bg-gradient-card border border-border rounded-2xl p-5
                      hover:border-violet-500/30 hover:shadow-lg hover:shadow-violet-500/5
                      transition-all duration-300 cursor-pointer"
                  >
                    <div className="flex items-start justify-between">
                      <div className="flex-1 min-w-0">
                        {/* 标签行 */}
                        <div className="flex items-center gap-2 mb-2">
                          <span className={`px-2.5 py-0.5 rounded-lg text-xs font-semibold ${
                            r.report_type === 'daily'
                              ? 'bg-green-500/15 text-green-400'
                              : r.report_type === 'weekly'
                              ? 'bg-blue-500/15 text-blue-400'
                              : 'bg-amber-500/15 text-amber-400'
                          }`}>
                            {r.report_type === 'daily' ? '日报' : r.report_type === 'weekly' ? '周报' : '月报'}
                          </span>
                          <span className={`px-2.5 py-0.5 rounded-lg text-xs font-medium ${
                            r.status === 'completed'
                              ? 'bg-green-500/15 text-green-400'
                              : r.status === 'failed'
                              ? 'bg-red-500/15 text-red-400'
                              : 'bg-yellow-500/15 text-yellow-400'
                          }`}>
                            {r.status === 'completed' ? '已完成' : r.status === 'failed' ? '失败' : '生成中'}
                          </span>
                        </div>
                        {/* 标题 */}
                        <div className="text-white font-medium text-base mb-2 truncate group-hover:text-violet-300 transition-colors">
                          {r.title}
                        </div>
                        {/* 元信息 */}
                        <div className="flex items-center gap-3 text-xs text-gray-500">
                          <span className="flex items-center gap-1">
                            <svg className="h-3.5 w-3.5" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M9.75 17L9 20l-1 1h8l-1-1-.75-3M3 13h18M5 17h14a2 2 0 002-2V5a2 2 0 00-2-2H5a2 2 0 00-2 2v10a2 2 0 002 2z" /></svg>
                            {r.model_used}
                          </span>
                          {r.token_count != null && (
                            <span className="flex items-center gap-1">
                              <svg className="h-3.5 w-3.5" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M7 7h.01M7 3h5c.512 0 1.024.195 1.414.586l7 7a2 2 0 010 2.828l-7 7a2 2 0 01-2.828 0l-7-7A2 2 0 013 12V7a4 4 0 014-4z" /></svg>
                              {r.token_count} tokens
                            </span>
                          )}
                          {r.generation_time_seconds != null && (
                            <span className="flex items-center gap-1">
                              <svg className="h-3.5 w-3.5" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M12 8v4l3 3m6-3a9 9 0 11-18 0 9 9 0 0118 0z" /></svg>
                              {r.generation_time_seconds.toFixed(1)}s
                            </span>
                          )}
                          <span className="flex items-center gap-1">
                            <svg className="h-3.5 w-3.5" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M8 7V3m8 4V3m-9 8h10M5 21h14a2 2 0 002-2V7a2 2 0 00-2-2H5a2 2 0 00-2 2v12a2 2 0 002 2z" /></svg>
                            {r.created_at?.split('.')[0]}
                          </span>
                        </div>
                      </div>
                      {/* 右侧操作 */}
                      <div className="flex items-center gap-2 ml-4">
                        <button
                          onClick={e => { e.stopPropagation(); handleDelete(r.report_id); }}
                          className="p-2 rounded-lg text-gray-600 hover:text-red-400 hover:bg-red-500/10 transition-all opacity-0 group-hover:opacity-100"
                          title="删除"
                        >
                          <svg className="h-4 w-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                            <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M19 7l-.867 12.142A2 2 0 0116.138 21H7.862a2 2 0 01-1.995-1.858L5 7m5 4v6m4-6v6m1-10V4a1 1 0 00-1-1h-4a1 1 0 00-1 1v3M4 7h16" />
                          </svg>
                        </button>
                        <svg className="h-5 w-5 text-gray-600 group-hover:text-violet-400 transition-colors" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                          <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M9 5l7 7-7 7" />
                        </svg>
                      </div>
                    </div>
                  </div>
                ))}
              </div>
            )}
          </div>
        </div>
      </div>
    );
  }

  /* ==================== 查看 / 生成视图 ==================== */
  return (
    <div className="min-h-screen bg-gradient-dark p-6">
      <div className="max-w-4xl mx-auto space-y-5">
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
          <div className="p-5">
            <div className="flex items-start justify-between">
              <div className="flex-1">
                <h1 className="text-xl font-bold text-white mb-2">
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
              <div className="px-8 py-10 sm:px-10 sm:py-12">
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
            <div className="px-8 py-10 sm:px-10 sm:py-12">
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

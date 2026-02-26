import React, { useState, useRef, useCallback } from 'react';
import ReactMarkdown from 'react-markdown';
import remarkGfm from 'remark-gfm';
import type { Components } from 'react-markdown';
import { newsService } from '../services/newsService';
import type { NewsArticle, NewsStreamEvent } from '../services/newsService';

/* ================================================================
   Markdown 暗色主题组件
   ================================================================ */
const mdComponents: Components = {
  h1: ({ children }) => (
    <h1 className="text-xl md:text-2xl font-bold text-white mt-8 mb-4 pb-3 border-b border-border-light">{children}</h1>
  ),
  h2: ({ children }) => (
    <div className="mt-8 mb-4">
      <h2 className="text-lg md:text-xl font-bold text-white flex items-center gap-3">
        <span className="w-1 h-6 bg-gradient-to-b from-emerald-500 to-teal-600 rounded-full" />
        {children}
      </h2>
      <div className="mt-2 h-px bg-gradient-to-r from-border-light to-transparent" />
    </div>
  ),
  h3: ({ children }) => (
    <h3 className="text-base md:text-lg font-semibold text-primary-light mt-6 mb-3 flex items-center gap-2">
      <span className="w-1.5 h-1.5 bg-primary-light rounded-full" />
      {children}
    </h3>
  ),
  h4: ({ children }) => (
    <h4 className="text-sm md:text-base font-semibold text-gray-200 mt-4 mb-2">{children}</h4>
  ),
  p: ({ children }) => <p className="text-gray-300 leading-7 mb-4 text-sm md:text-base">{children}</p>,
  strong: ({ children }) => <strong className="text-white font-semibold">{children}</strong>,
  em: ({ children }) => <em className="text-accent-cyan not-italic font-medium">{children}</em>,
  ul: ({ children }) => <ul className="space-y-2 mb-4 ml-1">{children}</ul>,
  ol: ({ children }) => <ol className="space-y-2 mb-4 ml-1 list-decimal list-inside">{children}</ol>,
  li: ({ children }) => (
    <li className="text-gray-300 leading-7 flex items-start gap-2 text-sm md:text-base">
      <span className="mt-2.5 w-1.5 h-1.5 bg-emerald-500 rounded-full shrink-0" />
      <span className="flex-1">{children}</span>
    </li>
  ),
  blockquote: ({ children }) => (
    <blockquote className="border-l-4 border-emerald-500/50 bg-emerald-500/5 pl-4 py-2 my-4 rounded-r-lg">
      {children}
    </blockquote>
  ),
  code: ({ className, children }) => {
    const isBlock = className?.includes('language-');
    if (isBlock) {
      return (
        <code className="block bg-dark text-xs md:text-sm text-gray-300 p-3 md:p-4 rounded-lg overflow-x-auto border border-border my-4 font-mono">
          {children}
        </code>
      );
    }
    return (
      <code className="bg-emerald-500/15 text-emerald-300 px-1.5 py-0.5 rounded text-xs md:text-sm font-mono">
        {children}
      </code>
    );
  },
  pre: ({ children }) => (
    <pre className="bg-dark rounded-xl border border-border overflow-hidden my-4">{children}</pre>
  ),
  table: ({ children }) => (
    <div className="overflow-x-auto my-5 rounded-xl border border-border">
      <table className="w-full text-xs md:text-sm">{children}</table>
    </div>
  ),
  thead: ({ children }) => <thead className="bg-dark-light border-b border-border">{children}</thead>,
  tbody: ({ children }) => <tbody className="divide-y divide-border">{children}</tbody>,
  tr: ({ children }) => <tr className="hover:bg-dark-light/50 transition-colors">{children}</tr>,
  th: ({ children }) => (
    <th className="px-2 md:px-4 py-2 md:py-3 text-left text-xs font-semibold text-gray-400 uppercase tracking-wider">{children}</th>
  ),
  td: ({ children }) => <td className="px-2 md:px-4 py-2 md:py-3 text-gray-300">{children}</td>,
  hr: () => (
    <hr className="my-8 border-none h-px bg-gradient-to-r from-transparent via-border-light to-transparent" />
  ),
  a: ({ href, children }) => (
    <a href={href} target="_blank" rel="noopener noreferrer" className="text-primary-light hover:text-primary underline underline-offset-2 transition-colors">
      {children}
    </a>
  ),
};

/* ================================================================
   情感颜色/标签
   ================================================================ */
const sentimentConfig = {
  positive: { label: '利好', color: 'text-green-400', bg: 'bg-green-500/15', border: 'border-green-500/30', dot: 'bg-green-500' },
  negative: { label: '利空', color: 'text-red-400', bg: 'bg-red-500/15', border: 'border-red-500/30', dot: 'bg-red-500' },
  neutral: { label: '中性', color: 'text-gray-400', bg: 'bg-gray-500/15', border: 'border-gray-500/30', dot: 'bg-gray-500' },
};

/* ================================================================
   主组件
   ================================================================ */
export const News: React.FC = () => {
  // 输入状态
  const [market, setMarket] = useState<'a_share' | 'general'>('a_share');
  const [symbol, setSymbol] = useState('');
  const [model, setModel] = useState('gpt-4o');

  // 数据
  const [articles, setArticles] = useState<NewsArticle[]>([]);
  const [selectedArticle, setSelectedArticle] = useState<NewsArticle | null>(null);

  // 流式状态
  const [isFetching, setIsFetching] = useState(false);
  const [isAnalyzing, setIsAnalyzing] = useState(false);
  const [statusMsg, setStatusMsg] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  // AI 分析结果
  const [analysisContent, setAnalysisContent] = useState('');
  const [analysisMode, setAnalysisMode] = useState<'none' | 'single' | 'report'>('none');
  const [analysisMeta, setAnalysisMeta] = useState<{ tokenCount?: number; genTime?: number } | null>(null);

  // 移动端面板切换：'list' 显示新闻列表, 'detail' 显示详情/分析
  const [mobilePanel, setMobilePanel] = useState<'list' | 'detail'>('list');

  // 流式 RAF 节流
  const streamBufferRef = useRef('');
  const rafRef = useRef(0);
  const abortRef = useRef<AbortController | null>(null);

  // 情感统计
  const sentimentStats = {
    positive: articles.filter(a => a.sentiment?.sentiment === 'positive').length,
    negative: articles.filter(a => a.sentiment?.sentiment === 'negative').length,
    neutral: articles.filter(a => a.sentiment?.sentiment === 'neutral').length,
  };

  // ==================== 刷新缓冲区 ====================
  const flushStream = useCallback(() => {
    if (rafRef.current) {
      cancelAnimationFrame(rafRef.current);
      rafRef.current = 0;
    }
    setAnalysisContent(streamBufferRef.current);
  }, []);

  // ==================== 抓取新闻 ====================
  const handleFetchNews = async () => {
    if (isFetching || isAnalyzing) return;
    if (market === 'a_share' && !symbol.trim()) return;

    setError(null);
    setStatusMsg(null);
    setIsFetching(true);
    setSelectedArticle(null);
    setAnalysisContent('');
    setAnalysisMode('none');
    setAnalysisMeta(null);

    const controller = new AbortController();
    abortRef.current = controller;

    try {
      await newsService.fetchNews(
        { symbol: symbol.trim() || undefined, market },
        (event: NewsStreamEvent) => {
          switch (event.event) {
            case 'fetching':
            case 'fetched':
            case 'analyzing':
              setStatusMsg(event.message || '');
              break;
            case 'complete':
              setStatusMsg(null);
              break;
            case 'error':
              setError(event.message || '抓取失败');
              break;
          }
        },
        controller.signal,
      );

      // 抓取完成后加载文章列表
      const data = await newsService.getArticles({
        symbol: market === 'a_share' ? symbol.trim() : undefined,
        market,
        limit: 100,
      });
      setArticles(data.articles);
    } catch (e: unknown) {
      if (e instanceof Error && e.name === 'AbortError') {
        setError('已取消');
      } else {
        setError(e instanceof Error ? e.message : '请求失败');
      }
    } finally {
      setIsFetching(false);
      setStatusMsg(null);
      abortRef.current = null;
    }
  };

  // ==================== AI 单篇分析 ====================
  const handleAnalyzeArticle = async (article: NewsArticle) => {
    if (isAnalyzing || isFetching) return;

    setSelectedArticle(article);
    setAnalysisMode('single');
    setAnalysisContent('');
    setAnalysisMeta(null);
    setError(null);
    setIsAnalyzing(true);
    setMobilePanel('detail');
    streamBufferRef.current = '';

    const controller = new AbortController();
    abortRef.current = controller;

    try {
      await newsService.analyzeArticle(
        { article_id: article.article_id, model },
        (event: NewsStreamEvent) => {
          switch (event.event) {
            case 'chunk':
              streamBufferRef.current += (event.content || '');
              if (!rafRef.current) {
                rafRef.current = requestAnimationFrame(() => {
                  rafRef.current = 0;
                  setAnalysisContent(streamBufferRef.current);
                });
              }
              break;
            case 'done':
              setAnalysisMeta({ tokenCount: event.token_count, genTime: event.generation_time });
              break;
            case 'error':
              setError(event.message || '分析失败');
              break;
          }
        },
        controller.signal,
      );
    } catch (e: unknown) {
      if (e instanceof Error && e.name !== 'AbortError') {
        setError(e instanceof Error ? e.message : '分析失败');
      }
    } finally {
      flushStream();
      setIsAnalyzing(false);
      abortRef.current = null;
    }
  };

  // ==================== AI 综合报告 ====================
  const handleGenerateReport = async () => {
    if (isAnalyzing || isFetching || articles.length === 0) return;

    setSelectedArticle(null);
    setAnalysisMode('report');
    setAnalysisContent('');
    setAnalysisMeta(null);
    setError(null);
    setIsAnalyzing(true);
    setMobilePanel('detail');
    streamBufferRef.current = '';

    const controller = new AbortController();
    abortRef.current = controller;

    try {
      await newsService.generateReport(
        {
          symbol: market === 'a_share' ? symbol.trim() : undefined,
          market,
          model,
        },
        (event: NewsStreamEvent) => {
          switch (event.event) {
            case 'chunk':
              streamBufferRef.current += (event.content || '');
              if (!rafRef.current) {
                rafRef.current = requestAnimationFrame(() => {
                  rafRef.current = 0;
                  setAnalysisContent(streamBufferRef.current);
                });
              }
              break;
            case 'done':
              setAnalysisMeta({ tokenCount: event.token_count, genTime: event.generation_time });
              break;
            case 'error':
              setError(event.message || '报告生成失败');
              break;
          }
        },
        controller.signal,
      );
    } catch (e: unknown) {
      if (e instanceof Error && e.name !== 'AbortError') {
        setError(e instanceof Error ? e.message : '报告生成失败');
      }
    } finally {
      flushStream();
      setIsAnalyzing(false);
      abortRef.current = null;
    }
  };

  const handleCancel = () => { abortRef.current?.abort(); };

  // 移动端选择文章时切换到详情面板
  const handleSelectArticle = (article: NewsArticle) => {
    setSelectedArticle(article);
    setMobilePanel('detail');
  };

  // 移动端返回列表
  const handleBackToList = () => {
    setMobilePanel('list');
    setAnalysisMode('none');
  };

  const isLoading = isFetching || isAnalyzing;

  /* ==================== 右侧/详情面板内容 ==================== */
  const renderDetailPanel = () => {
    if (analysisMode === 'none' && selectedArticle) {
      /* 新闻详情 */
      return (
        <div className="p-3 md:p-6">
          <h2 className="text-base md:text-lg font-bold text-white mb-3 leading-7">{selectedArticle.title}</h2>

          <div className="flex items-center gap-3 mb-5 text-xs text-gray-500 flex-wrap">
            {selectedArticle.source && <span>{selectedArticle.source}</span>}
            {selectedArticle.published_at && (
              <span>{selectedArticle.published_at.replace('T', ' ').slice(0, 19)}</span>
            )}
            {selectedArticle.sentiment && (
              <span className={`px-2 py-0.5 rounded-full border ${
                sentimentConfig[selectedArticle.sentiment.sentiment]?.bg || ''
              } ${sentimentConfig[selectedArticle.sentiment.sentiment]?.color || ''} ${
                sentimentConfig[selectedArticle.sentiment.sentiment]?.border || ''
              }`}>
                {sentimentConfig[selectedArticle.sentiment.sentiment]?.label || '未知'}{' '}
                {(selectedArticle.sentiment.confidence * 100).toFixed(0)}%
              </span>
            )}
          </div>

          {selectedArticle.content ? (
            <p className="text-gray-300 leading-7 whitespace-pre-wrap text-sm md:text-base">{selectedArticle.content}</p>
          ) : (
            <p className="text-gray-500 italic">无正文内容</p>
          )}

          {selectedArticle.url && (
            <a
              href={selectedArticle.url}
              target="_blank"
              rel="noopener noreferrer"
              className="inline-block mt-5 text-sm text-emerald-400 hover:text-emerald-300 underline underline-offset-2"
            >
              查看原文
            </a>
          )}

          <div className="mt-6">
            <button
              onClick={() => handleAnalyzeArticle(selectedArticle)}
              disabled={isLoading}
              className="px-4 md:px-5 py-2 md:py-2.5 bg-gradient-to-r from-emerald-500 to-teal-600 text-white rounded-xl
                hover:from-emerald-600 hover:to-teal-700 shadow-lg shadow-emerald-500/25
                transition-all duration-300 font-medium text-sm md:text-base
                disabled:opacity-50 disabled:cursor-not-allowed"
            >
              AI 深度分析
            </button>
          </div>
        </div>
      );
    }

    if (analysisMode === 'single' || analysisMode === 'report') {
      /* AI 分析结果 */
      return (
        <div className="p-3 md:p-6">
          <div className="flex items-center gap-3 mb-4">
            <div className="w-7 h-7 md:w-8 md:h-8 rounded-lg bg-emerald-500/20 flex items-center justify-center text-xs text-emerald-300">
              AI
            </div>
            <h2 className="text-base md:text-lg font-bold text-white">
              {analysisMode === 'single' ? 'AI 深度分析' : '综合新闻报告'}
            </h2>
            {isAnalyzing && (
              <svg className="animate-spin h-4 w-4 text-emerald-400" viewBox="0 0 24 24">
                <circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4" fill="none" />
                <path className="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4zm2 5.291A7.962 7.962 0 014 12H0c0 3.042 1.135 5.824 3 7.938l3-2.647z" />
              </svg>
            )}
          </div>

          {analysisContent ? (
            <div className="prose-dark">
              <ReactMarkdown remarkPlugins={[remarkGfm]} components={mdComponents}>
                {analysisContent}
              </ReactMarkdown>
              {isAnalyzing && (
                <span className="inline-block w-2 h-5 bg-emerald-400 rounded-sm animate-pulse ml-0.5 align-text-bottom" />
              )}
            </div>
          ) : isAnalyzing ? (
            <div className="flex items-center gap-3 text-emerald-400 text-sm py-8">
              <svg className="animate-spin h-5 w-5" viewBox="0 0 24 24">
                <circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4" fill="none" />
                <path className="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4zm2 5.291A7.962 7.962 0 014 12H0c0 3.042 1.135 5.824 3 7.938l3-2.647z" />
              </svg>
              正在分析中...
            </div>
          ) : null}

          {/* 完成元信息 */}
          {analysisMeta && !isAnalyzing && (
            <div className="mt-6 flex items-center gap-4 text-xs text-gray-600 bg-dark-card/50 px-3 md:px-4 py-2 rounded-full w-fit">
              {analysisMeta.tokenCount != null && <span>{analysisMeta.tokenCount} tokens</span>}
              {analysisMeta.genTime != null && <span>{analysisMeta.genTime}s</span>}
            </div>
          )}
        </div>
      );
    }

    /* 空状态 */
    return (
      <div className="flex items-center justify-center h-full">
        <p className="text-gray-600 text-sm px-4 text-center">选择一条新闻查看详情，或点击「AI 分析」进行深度解读</p>
      </div>
    );
  };

  /* ==================== 渲染 ==================== */
  return (
    <div className="h-full flex flex-col bg-gradient-dark pb-20 md:pb-0">
      {/* 头部栏 */}
      <div className="flex-shrink-0 border-b border-border bg-dark-card/50 backdrop-blur-sm px-3 md:px-6 py-3 md:py-4">
        <div className="flex flex-col gap-2 md:flex-row md:items-center md:gap-3 mb-3 md:mb-4">
          <div className="flex items-center gap-3">
            <div className="w-8 h-8 md:w-10 md:h-10 rounded-xl bg-gradient-to-br from-emerald-500 to-teal-600 flex items-center justify-center text-base md:text-lg shadow-lg shadow-emerald-500/25">
              N
            </div>
            <div>
              <h1 className="text-lg md:text-xl font-bold text-white">新闻分析</h1>
              <p className="text-xs text-gray-500">新闻抓取 + BERT 情感分析 + AI 深度解读</p>
            </div>
          </div>
        </div>

        <div className="flex items-center gap-2 md:gap-3 flex-wrap">
          {/* 市场切换 */}
          <div className="flex rounded-xl border border-border overflow-hidden">
            <button
              onClick={() => setMarket('a_share')}
              disabled={isLoading}
              className={`px-3 md:px-4 py-1.5 md:py-2 text-xs md:text-sm font-medium transition-all ${
                market === 'a_share'
                  ? 'bg-emerald-500/20 text-emerald-300 border-r border-emerald-500/30'
                  : 'bg-dark text-gray-400 hover:text-white border-r border-border'
              } disabled:opacity-50`}
            >
              A 股
            </button>
            <button
              onClick={() => setMarket('general')}
              disabled={isLoading}
              className={`px-3 md:px-4 py-1.5 md:py-2 text-xs md:text-sm font-medium transition-all ${
                market === 'general'
                  ? 'bg-emerald-500/20 text-emerald-300'
                  : 'bg-dark text-gray-400 hover:text-white'
              } disabled:opacity-50`}
            >
              全球
            </button>
          </div>

          {/* 股票代码输入（仅 A 股） */}
          {market === 'a_share' && (
            <input
              type="text"
              value={symbol}
              onChange={e => setSymbol(e.target.value)}
              placeholder="输入股票代码，如 300059"
              disabled={isLoading}
              className="px-3 md:px-4 py-2 md:py-2.5 bg-dark text-white rounded-xl border border-border
                focus:border-emerald-500 focus:ring-2 focus:ring-emerald-500/20 outline-none transition-all
                placeholder:text-gray-600 disabled:opacity-50 w-40 md:w-52 text-sm"
              onKeyDown={e => { if (e.key === 'Enter') handleFetchNews(); }}
            />
          )}

          {/* 模型选择 */}
          <select
            value={model}
            onChange={e => setModel(e.target.value)}
            disabled={isLoading}
            className="px-2 md:px-3 py-2 md:py-2.5 bg-dark text-white rounded-xl border border-border
              focus:border-emerald-500 focus:ring-2 focus:ring-emerald-500/20 outline-none transition-all text-xs md:text-sm
              disabled:opacity-50"
          >
            <option value="gpt-4o">GPT-4o</option>
            <option value="gpt-4o-mini">GPT-4o Mini</option>
            <option value="gpt-4.1">GPT-4.1</option>
            <option value="gpt-4.1-mini">GPT-4.1 Mini</option>
          </select>

          {/* 获取新闻按钮 */}
          <button
            onClick={handleFetchNews}
            disabled={isLoading || (market === 'a_share' && !symbol.trim())}
            className="px-3 md:px-5 py-2 md:py-2.5 bg-gradient-to-r from-emerald-500 to-teal-600 text-white rounded-xl
              hover:from-emerald-600 hover:to-teal-700 shadow-lg shadow-emerald-500/25
              hover:shadow-emerald-500/40 transition-all duration-300 font-medium text-xs md:text-sm
              disabled:opacity-50 disabled:cursor-not-allowed flex items-center gap-1.5 md:gap-2"
          >
            {isFetching ? (
              <>
                <svg className="animate-spin h-3.5 w-3.5 md:h-4 md:w-4" viewBox="0 0 24 24">
                  <circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4" fill="none" />
                  <path className="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4zm2 5.291A7.962 7.962 0 014 12H0c0 3.042 1.135 5.824 3 7.938l3-2.647z" />
                </svg>
                抓取中
              </>
            ) : '获取新闻'}
          </button>

          {/* 综合报告按钮 */}
          <button
            onClick={handleGenerateReport}
            disabled={isLoading || articles.length === 0}
            className="px-3 md:px-5 py-2 md:py-2.5 bg-gradient-to-r from-violet-500 to-purple-600 text-white rounded-xl
              hover:from-violet-600 hover:to-purple-700 shadow-lg shadow-purple-500/25
              transition-all duration-300 font-medium text-xs md:text-sm
              disabled:opacity-50 disabled:cursor-not-allowed flex items-center gap-1.5 md:gap-2"
          >
            {isAnalyzing && analysisMode === 'report' ? (
              <>
                <svg className="animate-spin h-3.5 w-3.5 md:h-4 md:w-4" viewBox="0 0 24 24">
                  <circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4" fill="none" />
                  <path className="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4zm2 5.291A7.962 7.962 0 014 12H0c0 3.042 1.135 5.824 3 7.938l3-2.647z" />
                </svg>
                生成中
              </>
            ) : '综合报告'}
          </button>

          {/* 取消按钮 */}
          {isLoading && (
            <button
              onClick={handleCancel}
              className="px-3 md:px-4 py-2 md:py-2.5 bg-red-500/15 text-red-400 border border-red-500/20 rounded-xl
                hover:bg-red-500/25 transition-all text-xs md:text-sm font-medium"
            >
              取消
            </button>
          )}
        </div>

        {/* 状态提示 */}
        {statusMsg && (
          <div className="mt-2 md:mt-3 flex items-center gap-2 text-xs md:text-sm text-emerald-400">
            <svg className="animate-spin h-3.5 w-3.5 md:h-4 md:w-4 shrink-0" viewBox="0 0 24 24">
              <circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4" fill="none" />
              <path className="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4zm2 5.291A7.962 7.962 0 014 12H0c0 3.042 1.135 5.824 3 7.938l3-2.647z" />
            </svg>
            {statusMsg}
          </div>
        )}

        {/* 错误提示 */}
        {error && (
          <div className="mt-2 md:mt-3 p-2 md:p-3 rounded-xl border bg-red-500/10 border-red-500/20 text-red-400 text-xs md:text-sm">
            {error}
          </div>
        )}
      </div>

      {/* 情感统计卡片 */}
      {articles.length > 0 && (
        <div className="flex-shrink-0 px-3 md:px-6 py-2 md:py-3 border-b border-border bg-dark-card/30 flex items-center gap-3 md:gap-4 flex-wrap">
          <span className="text-xs md:text-sm text-gray-400">情感分布:</span>
          <div className="flex items-center gap-1.5 text-xs md:text-sm">
            <span className="w-2 h-2 rounded-full bg-green-500" />
            <span className="text-green-400 font-medium">{sentimentStats.positive}</span>
            <span className="text-gray-600">利好</span>
          </div>
          <div className="flex items-center gap-1.5 text-xs md:text-sm">
            <span className="w-2 h-2 rounded-full bg-red-500" />
            <span className="text-red-400 font-medium">{sentimentStats.negative}</span>
            <span className="text-gray-600">利空</span>
          </div>
          <div className="flex items-center gap-1.5 text-xs md:text-sm">
            <span className="w-2 h-2 rounded-full bg-gray-500" />
            <span className="text-gray-400 font-medium">{sentimentStats.neutral}</span>
            <span className="text-gray-600">中性</span>
          </div>
          <span className="text-gray-600 text-xs md:text-sm ml-auto">共 {articles.length} 条</span>
        </div>
      )}

      {/* 主内容区：桌面端左右分栏，移动端切换面板 */}
      <div className="flex-1 min-h-0 flex">
        {/* 左侧新闻列表 — 桌面端始终显示，移动端仅在 list 面板时显示 */}
        <div className={`md:w-[55%] md:border-r border-border overflow-y-auto ${
          mobilePanel === 'list' ? 'w-full' : 'hidden md:block'
        }`}>
          {articles.length === 0 ? (
            <div className="flex items-center justify-center h-full">
              <div className="text-center py-12 md:py-20 px-4">
                <div className="w-16 h-16 md:w-20 md:h-20 rounded-2xl bg-gradient-to-br from-emerald-500/20 to-teal-600/20 border border-emerald-500/20
                  flex items-center justify-center text-2xl md:text-3xl mx-auto mb-4 md:mb-6">
                  N
                </div>
                <h2 className="text-lg md:text-xl font-bold text-white mb-2 md:mb-3">新闻分析</h2>
                <p className="text-gray-500 max-w-sm mx-auto leading-relaxed text-sm md:text-base">
                  {market === 'a_share'
                    ? '输入 A 股代码，点击「获取新闻」开始分析'
                    : '切换到全球市场，点击「获取新闻」获取最新资讯'}
                </p>
              </div>
            </div>
          ) : (
            <div className="divide-y divide-border">
              {articles.map(article => {
                const isSelected = selectedArticle?.article_id === article.article_id;
                const sent = article.sentiment;
                const sentCfg = sent ? sentimentConfig[sent.sentiment] || sentimentConfig.neutral : null;

                return (
                  <div
                    key={article.article_id}
                    onClick={() => handleSelectArticle(article)}
                    className={`px-3 md:px-5 py-3 md:py-4 cursor-pointer transition-all duration-150 hover:bg-dark-light/50 ${
                      isSelected ? 'bg-emerald-500/5 border-l-2 border-l-emerald-500' : 'border-l-2 border-l-transparent'
                    }`}
                  >
                    <div className="flex items-start gap-2 md:gap-3">
                      {/* 情感色标 */}
                      {sentCfg && (
                        <span className={`mt-1.5 w-2 h-2 rounded-full shrink-0 ${sentCfg.dot}`} />
                      )}

                      <div className="flex-1 min-w-0">
                        <h3 className="text-xs md:text-sm font-medium text-gray-200 leading-5 md:leading-6 line-clamp-2">
                          {article.title}
                        </h3>

                        <div className="flex items-center gap-2 md:gap-3 mt-1.5 md:mt-2 text-xs text-gray-500 flex-wrap">
                          {article.source && <span>{article.source}</span>}
                          {article.published_at && (
                            <span>{article.published_at.replace('T', ' ').slice(0, 16)}</span>
                          )}

                          {sent && sentCfg && (
                            <span className={`px-1.5 md:px-2 py-0.5 rounded-full text-xs ${sentCfg.bg} ${sentCfg.color} ${sentCfg.border} border`}>
                              {sentCfg.label} {(sent.confidence * 100).toFixed(0)}%
                            </span>
                          )}
                        </div>
                      </div>

                      {/* AI 分析按钮 */}
                      <button
                        onClick={e => { e.stopPropagation(); handleAnalyzeArticle(article); }}
                        disabled={isLoading}
                        className="shrink-0 px-2 md:px-3 py-1 md:py-1.5 text-xs bg-emerald-500/10 text-emerald-400 border border-emerald-500/20
                          rounded-lg hover:bg-emerald-500/20 transition-all disabled:opacity-50 disabled:cursor-not-allowed"
                      >
                        AI 分析
                      </button>
                    </div>
                  </div>
                );
              })}
            </div>
          )}
        </div>

        {/* 右侧面板 — 桌面端始终显示，移动端仅在 detail 面板时显示 */}
        <div className={`md:w-[45%] overflow-y-auto ${
          mobilePanel === 'detail' ? 'w-full' : 'hidden md:block'
        }`}>
          {/* 移动端返回按钮 */}
          {mobilePanel === 'detail' && (
            <div className="md:hidden flex-shrink-0 border-b border-border px-3 py-2">
              <button
                onClick={handleBackToList}
                className="flex items-center gap-1.5 text-sm text-emerald-400 hover:text-emerald-300 transition-colors"
              >
                <svg className="w-4 h-4" fill="none" viewBox="0 0 24 24" strokeWidth="2" stroke="currentColor">
                  <path strokeLinecap="round" strokeLinejoin="round" d="M15.75 19.5L8.25 12l7.5-7.5" />
                </svg>
                返回列表
              </button>
            </div>
          )}

          {renderDetailPanel()}
        </div>
      </div>
    </div>
  );
};

import React, { useState, useRef, useCallback, useEffect } from 'react';
import ReactMarkdown from 'react-markdown';
import remarkGfm from 'remark-gfm';
import type { Components } from 'react-markdown';
import { advisorService } from '../services/advisorService';
import type { AdvisorStreamEvent } from '../services/advisorService';
import { StockSymbolInput } from '../components/common/StockSymbolInput';

/* ================================================================
   Markdown 暗色主题组件（与 Reports.tsx 一致）
   ================================================================ */
const mdComponents: Components = {
  h1: ({ children }) => (
    <h1 className="text-2xl font-bold text-white mt-8 mb-4 pb-3 border-b border-border-light">{children}</h1>
  ),
  h2: ({ children }) => (
    <div className="mt-8 mb-4">
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
  p: ({ children }) => <p className="text-gray-300 leading-7 mb-4">{children}</p>,
  strong: ({ children }) => <strong className="text-white font-semibold">{children}</strong>,
  em: ({ children }) => <em className="text-accent-cyan not-italic font-medium">{children}</em>,
  ul: ({ children }) => <ul className="space-y-2 mb-4 ml-1">{children}</ul>,
  ol: ({ children }) => <ol className="space-y-2 mb-4 ml-1 list-decimal list-inside">{children}</ol>,
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
  thead: ({ children }) => <thead className="bg-dark-light border-b border-border">{children}</thead>,
  tbody: ({ children }) => <tbody className="divide-y divide-border">{children}</tbody>,
  tr: ({ children }) => <tr className="hover:bg-dark-light/50 transition-colors">{children}</tr>,
  th: ({ children }) => (
    <th className="px-4 py-3 text-left text-xs font-semibold text-gray-400 uppercase tracking-wider">{children}</th>
  ),
  td: ({ children }) => <td className="px-4 py-3 text-gray-300">{children}</td>,
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
   类型定义
   ================================================================ */
interface ChatMessage {
  id: string;
  role: 'user' | 'assistant' | 'status';
  content: string;
  isStreaming?: boolean;
}

interface SessionTab {
  symbol: string;
  name: string;
  sessionId: string | null;
  messages: ChatMessage[];
  doneMeta: { tokenCount?: number; genTime?: number } | null;
}

/* ================================================================
   主组件
   ================================================================ */
export const Advisor: React.FC = () => {
  // 输入
  const [symbol, setSymbol] = useState('');
  const [stockName, setStockName] = useState('');
  const [model, setModel] = useState('gpt-4.1');
  const [enableWebSearch, setEnableWebSearch] = useState(false);
  const [followUpInput, setFollowUpInput] = useState('');

  // 多会话标签
  const [tabs, setTabs] = useState<SessionTab[]>([]);
  const [activeTabIdx, setActiveTabIdx] = useState(-1); // -1 = 无活跃标签

  // 当前会话状态
  const [isStreaming, setIsStreaming] = useState(false);
  const [collectingMsg, setCollectingMsg] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  // 流式 RAF 节流
  const streamBufferRef = useRef('');
  const rafRef = useRef(0);
  const streamMsgIdRef = useRef('');
  const abortRef = useRef<AbortController | null>(null);
  const chatEndRef = useRef<HTMLDivElement>(null);

  // 当前活跃标签的便捷引用
  const activeTab = activeTabIdx >= 0 ? tabs[activeTabIdx] : null;

  // 自动滚动
  const scrollToBottom = useCallback(() => {
    chatEndRef.current?.scrollIntoView({ behavior: 'smooth' });
  }, []);

  useEffect(() => {
    scrollToBottom();
  }, [activeTab?.messages.length, collectingMsg, scrollToBottom]);

  // 更新当前标签的 messages
  const updateActiveMessages = useCallback((updater: (prev: ChatMessage[]) => ChatMessage[]) => {
    setTabs(prev => prev.map((t, i) => i === activeTabIdx ? { ...t, messages: updater(t.messages) } : t));
  }, [activeTabIdx]);

  // 更新当前标签的字段
  const updateActiveTab = useCallback((patch: Partial<SessionTab>) => {
    setTabs(prev => prev.map((t, i) => i === activeTabIdx ? { ...t, ...patch } : t));
  }, [activeTabIdx]);

  // ---------- 开始分析（新股票或重新分析） ----------
  const handleAnalyze = async () => {
    if (!symbol.trim() || isStreaming) return;

    const targetSymbol = symbol.trim();
    const targetName = stockName || targetSymbol;

    // 如果正在流式输出，先取消
    if (abortRef.current) abortRef.current.abort();

    // 查找是否已有该股票的标签
    let tabIdx = tabs.findIndex(t => t.symbol === targetSymbol);

    if (tabIdx >= 0) {
      // 已有标签 → 切换到它，但重新分析（清空历史）
      setTabs(prev => prev.map((t, i) => i === tabIdx
        ? { ...t, messages: [], sessionId: null, doneMeta: null }
        : t
      ));
      setActiveTabIdx(tabIdx);
    } else {
      // 新建标签
      const newTab: SessionTab = {
        symbol: targetSymbol,
        name: targetName,
        sessionId: null,
        messages: [],
        doneMeta: null,
      };
      setTabs(prev => [...prev, newTab]);
      tabIdx = tabs.length;
      setActiveTabIdx(tabIdx);
    }

    setError(null);
    setCollectingMsg(null);
    streamBufferRef.current = '';
    setIsStreaming(true);

    const controller = new AbortController();
    abortRef.current = controller;

    let sid: string | null = null;
    const currentTabIdx = tabIdx;

    try {
      await advisorService.chat(
        {
          symbol: targetSymbol,
          enable_web_search: enableWebSearch,
          model,
        },
        (event: AdvisorStreamEvent) => {
          handleEvent(event, currentTabIdx, (newSid) => { sid = newSid; });
        },
        controller.signal,
      );
    } catch (e: unknown) {
      if (e instanceof Error && e.name === 'AbortError') {
        setError('已取消');
      } else {
        setError(e instanceof Error ? e.message : '请求失败');
      }
    } finally {
      flushStream(currentTabIdx);
      setIsStreaming(false);
      abortRef.current = null;
      if (sid) {
        setTabs(prev => prev.map((t, i) => i === currentTabIdx ? { ...t, sessionId: sid } : t));
      }
    }
  };

  // ---------- 追问 ----------
  const handleFollowUp = async () => {
    if (!followUpInput.trim() || !activeTab?.sessionId || isStreaming) return;

    const userMsg: ChatMessage = {
      id: `user_${Date.now()}`,
      role: 'user',
      content: followUpInput.trim(),
    };
    updateActiveMessages(prev => [...prev, userMsg]);
    setFollowUpInput('');
    setError(null);
    streamBufferRef.current = '';
    setIsStreaming(true);

    const controller = new AbortController();
    abortRef.current = controller;
    const currentTabIdx = activeTabIdx;

    try {
      await advisorService.chat(
        {
          symbol: activeTab.symbol,
          session_id: activeTab.sessionId,
          message: userMsg.content,
          model,
        },
        (event: AdvisorStreamEvent) => {
          handleEvent(event, currentTabIdx, () => {});
        },
        controller.signal,
      );
    } catch (e: unknown) {
      if (e instanceof Error && e.name === 'AbortError') {
        setError('已取消');
      } else {
        setError(e instanceof Error ? e.message : '请求失败');
      }
    } finally {
      flushStream(currentTabIdx);
      setIsStreaming(false);
      abortRef.current = null;
    }
  };

  // ---------- 事件处理（指定 tab index） ----------
  const handleEvent = (event: AdvisorStreamEvent, tabIdx: number, onSessionCreated: (sid: string) => void) => {
    switch (event.event) {
      case 'session_created':
        if (event.session_id) onSessionCreated(event.session_id);
        break;

      case 'collecting':
        setCollectingMsg(event.message || '正在收集数据...');
        break;

      case 'start': {
        setCollectingMsg(null);
        const msgId = `assistant_${Date.now()}`;
        streamMsgIdRef.current = msgId;
        streamBufferRef.current = '';
        setTabs(prev => prev.map((t, i) => i === tabIdx
          ? { ...t, messages: [...t.messages, { id: msgId, role: 'assistant', content: '', isStreaming: true }] }
          : t
        ));
        break;
      }

      case 'chunk':
        streamBufferRef.current += (event.content || '');
        if (!rafRef.current) {
          rafRef.current = requestAnimationFrame(() => {
            rafRef.current = 0;
            const buf = streamBufferRef.current;
            const mid = streamMsgIdRef.current;
            setTabs(prev => prev.map((t, i) => i === tabIdx
              ? { ...t, messages: t.messages.map(m => m.id === mid ? { ...m, content: buf } : m) }
              : t
            ));
          });
        }
        break;

      case 'done':
        setTabs(prev => prev.map((t, i) => i === tabIdx
          ? { ...t, doneMeta: { tokenCount: event.token_count, genTime: event.generation_time } }
          : t
        ));
        break;

      case 'error':
        setError(event.message || '分析失败');
        break;
    }
  };

  const flushStream = (tabIdx: number) => {
    if (rafRef.current) {
      cancelAnimationFrame(rafRef.current);
      rafRef.current = 0;
    }
    const buf = streamBufferRef.current;
    const mid = streamMsgIdRef.current;
    if (mid && buf) {
      setTabs(prev => prev.map((t, i) => i === tabIdx
        ? { ...t, messages: t.messages.map(m => m.id === mid ? { ...m, content: buf, isStreaming: false } : m) }
        : t
      ));
    }
  };

  const handleCancel = () => { abortRef.current?.abort(); };

  const handleKeyDown = (e: React.KeyboardEvent) => {
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault();
      handleFollowUp();
    }
  };

  // 切换标签
  const switchTab = (idx: number) => {
    if (isStreaming) return;
    setActiveTabIdx(idx);
    setSymbol(tabs[idx].symbol);
    setStockName(tabs[idx].name);
    setError(null);
    setCollectingMsg(null);
  };

  // 关闭标签
  const closeTab = (idx: number, e: React.MouseEvent) => {
    e.stopPropagation();
    if (isStreaming) return;
    setTabs(prev => prev.filter((_, i) => i !== idx));
    if (idx === activeTabIdx) {
      setActiveTabIdx(tabs.length > 1 ? Math.max(0, idx - 1) : -1);
    } else if (idx < activeTabIdx) {
      setActiveTabIdx(prev => prev - 1);
    }
  };

  // 当前显示的消息
  const currentMessages = activeTab?.messages || [];
  const currentDoneMeta = activeTab?.doneMeta;

  /* ==================== 渲染 ==================== */
  return (
    <div className="min-h-screen bg-gradient-dark flex flex-col pb-20 md:pb-0">
      {/* 头部 */}
      <div className="flex-shrink-0 border-b border-border bg-dark-card/50 backdrop-blur-sm">
        <div className="max-w-4xl mx-auto px-3 md:px-6 py-3 md:py-4">
          <div className="flex items-center gap-3 mb-3 md:mb-4">
            <div className="w-8 h-8 md:w-10 md:h-10 rounded-xl bg-gradient-to-br from-violet-500 to-purple-600 flex items-center justify-center text-sm md:text-lg shadow-lg shadow-purple-500/25">
              AI
            </div>
            <div>
              <h1 className="text-lg md:text-xl font-bold text-white">AI 投资顾问</h1>
              <p className="text-xs text-gray-500 hidden md:block">输入新代码随时切换分析，支持多股票对话</p>
            </div>
          </div>

          {/* 输入栏 */}
          <div className="flex flex-col gap-2 md:flex-row md:items-center md:gap-3 md:flex-wrap">
            <div className="flex-1 min-w-0 md:min-w-[200px]">
              <StockSymbolInput
                value={symbol}
                onChange={(sym, name) => { setSymbol(sym); if (name) setStockName(name); }}
                placeholder="输入代码或名称搜索"
                disabled={isStreaming}
                className="w-full px-3 md:px-4 py-2 md:py-2.5 bg-dark text-white rounded-xl border border-border
                  focus:border-violet-500 focus:ring-2 focus:ring-violet-500/20 outline-none transition-all
                  placeholder:text-gray-600 disabled:opacity-50 text-sm md:text-base"
              />
            </div>

            <div className="flex items-center gap-2 md:gap-3">
              <label className="flex items-center gap-1.5 text-sm text-gray-400 cursor-pointer select-none">
                <input
                  type="checkbox"
                  checked={enableWebSearch}
                  onChange={e => setEnableWebSearch(e.target.checked)}
                  className="w-4 h-4 rounded border-border bg-dark text-violet-500 focus:ring-violet-500/20"
                />
                联网
              </label>

              <select
                value={model}
                onChange={e => setModel(e.target.value)}
                disabled={isStreaming}
                className="px-2 md:px-3 py-2 md:py-2.5 bg-dark text-white rounded-xl border border-border
                  focus:border-violet-500 focus:ring-2 focus:ring-violet-500/20 outline-none transition-all text-xs md:text-sm
                  disabled:opacity-50"
              >
                <option value="gpt-4.1">GPT-4.1</option>
                <option value="gpt-4.1-mini">GPT-4.1 Mini</option>
                <option value="gpt-4.1-nano">GPT-4.1 Nano</option>
                <option value="gpt-4o">GPT-4o</option>
                <option value="gpt-4o-mini">GPT-4o Mini</option>
              </select>

              <button
                onClick={handleAnalyze}
                disabled={!symbol.trim() || isStreaming}
                className="px-4 md:px-6 py-2 md:py-2.5 bg-gradient-to-r from-violet-500 to-purple-600 text-white rounded-xl
                  hover:from-violet-600 hover:to-purple-700 shadow-lg shadow-purple-500/25
                  hover:shadow-purple-500/40 transition-all duration-300 font-medium text-sm md:text-base
                  disabled:opacity-50 disabled:cursor-not-allowed flex items-center gap-2"
              >
                {isStreaming ? (
                  <>
                    <svg className="animate-spin h-4 w-4" viewBox="0 0 24 24">
                      <circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4" fill="none" />
                      <path className="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4zm2 5.291A7.962 7.962 0 014 12H0c0 3.042 1.135 5.824 3 7.938l3-2.647z" />
                    </svg>
                    分析中
                  </>
                ) : (
                  '分析'
                )}
              </button>
            </div>
          </div>

          {/* 股票标签栏 */}
          {tabs.length > 0 && (
            <div className="flex gap-1.5 mt-3 overflow-x-auto" style={{ scrollbarWidth: 'none' }}>
              {tabs.map((tab, idx) => {
                const isActive = idx === activeTabIdx;
                return (
                  <button
                    key={`${tab.symbol}_${idx}`}
                    onClick={() => switchTab(idx)}
                    className={`group relative flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-sm font-medium
                      whitespace-nowrap transition-all duration-200 ${
                      isActive
                        ? 'bg-violet-500/20 text-violet-300 border border-violet-500/30'
                        : 'bg-dark-light/50 text-gray-500 hover:text-gray-300 hover:bg-dark-light border border-transparent'
                    }`}
                  >
                    <span className="font-mono text-xs">{tab.symbol.split('.')[0]}</span>
                    <span className="text-xs opacity-70">{tab.name}</span>
                    {tab.sessionId && <span className="w-1.5 h-1.5 rounded-full bg-green-500/60" title="会话存活" />}
                    <span
                      onClick={(e) => closeTab(idx, e)}
                      className="ml-1 opacity-100 md:opacity-0 md:group-hover:opacity-100 hover:text-red-400 transition-opacity text-xs"
                    >
                      x
                    </span>
                  </button>
                );
              })}
            </div>
          )}
        </div>
      </div>

      {/* 聊天区域 */}
      <div className="flex-1 overflow-y-auto">
        <div className="max-w-4xl mx-auto px-3 md:px-6 py-4 md:py-6 space-y-4 md:space-y-6">
          {/* 欢迎提示 */}
          {currentMessages.length === 0 && !collectingMsg && !error && (
            <div className="text-center py-20">
              <div className="w-20 h-20 rounded-2xl bg-gradient-to-br from-violet-500/20 to-purple-600/20 border border-violet-500/20
                flex items-center justify-center text-3xl mx-auto mb-6">
                AI
              </div>
              <h2 className="text-2xl font-bold text-white mb-3">AI 投资顾问</h2>
              <p className="text-gray-500 max-w-md mx-auto leading-relaxed">
                输入股票代码点击分析，随时输入新代码切换股票。每个股票独立对话，支持追问。
              </p>
            </div>
          )}

          {/* 数据收集状态 */}
          {collectingMsg && (
            <div className="flex items-start gap-3">
              <div className="w-8 h-8 rounded-lg bg-violet-500/20 flex items-center justify-center text-xs text-violet-300 shrink-0 mt-0.5">
                AI
              </div>
              <div className="bg-gradient-card border border-border rounded-2xl rounded-tl-sm px-5 py-4 max-w-[85%]">
                <div className="flex items-center gap-3">
                  <svg className="animate-spin h-4 w-4 text-violet-400 shrink-0" viewBox="0 0 24 24">
                    <circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4" fill="none" />
                    <path className="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4zm2 5.291A7.962 7.962 0 014 12H0c0 3.042 1.135 5.824 3 7.938l3-2.647z" />
                  </svg>
                  <span className="text-violet-300 text-sm">{collectingMsg}</span>
                </div>
              </div>
            </div>
          )}

          {/* 消息列表 */}
          {currentMessages.map(msg => (
            <div
              key={msg.id}
              className={`flex items-start gap-3 ${msg.role === 'user' ? 'flex-row-reverse' : ''}`}
            >
              <div className={`w-8 h-8 rounded-lg flex items-center justify-center text-xs shrink-0 mt-0.5 ${
                msg.role === 'user'
                  ? 'bg-blue-500/20 text-blue-300'
                  : 'bg-violet-500/20 text-violet-300'
              }`}>
                {msg.role === 'user' ? 'Me' : 'AI'}
              </div>

              <div className={`max-w-[88%] md:max-w-[85%] ${
                msg.role === 'user'
                  ? 'bg-blue-500/10 border border-blue-500/20 rounded-2xl rounded-tr-sm px-5 py-3'
                  : 'bg-gradient-card border border-border rounded-2xl rounded-tl-sm px-6 py-5'
              }`}>
                {msg.role === 'user' ? (
                  <p className="text-gray-200 leading-7 whitespace-pre-wrap">{msg.content}</p>
                ) : (
                  <div className="prose-dark">
                    <ReactMarkdown remarkPlugins={[remarkGfm]} components={mdComponents}>
                      {msg.content}
                    </ReactMarkdown>
                    {msg.isStreaming && (
                      <span className="inline-block w-2 h-5 bg-violet-400 rounded-sm animate-pulse ml-0.5 align-text-bottom" />
                    )}
                  </div>
                )}
              </div>
            </div>
          ))}

          {/* 错误提示 */}
          {error && (
            <div className="p-4 rounded-xl border bg-red-500/10 border-red-500/20 text-red-400 flex items-center gap-3">
              <svg className="h-5 w-5 shrink-0" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M12 8v4m0 4h.01M21 12a9 9 0 11-18 0 9 9 0 0118 0z" />
              </svg>
              {error}
            </div>
          )}

          {/* 完成元信息 */}
          {currentDoneMeta && !isStreaming && (
            <div className="flex justify-center">
              <div className="flex items-center gap-4 text-xs text-gray-600 bg-dark-card/50 px-4 py-2 rounded-full">
                {currentDoneMeta.tokenCount != null && <span>{currentDoneMeta.tokenCount} tokens</span>}
                {currentDoneMeta.genTime != null && <span>{currentDoneMeta.genTime}s</span>}
              </div>
            </div>
          )}

          <div ref={chatEndRef} />
        </div>
      </div>

      {/* 底部追问栏 */}
      {activeTab?.sessionId && (
        <div className="flex-shrink-0 border-t border-border bg-dark-card/50 backdrop-blur-sm">
          <div className="max-w-4xl mx-auto px-3 md:px-6 py-3 md:py-4">
            <div className="flex items-center gap-2 md:gap-3">
              <input
                type="text"
                value={followUpInput}
                onChange={e => setFollowUpInput(e.target.value)}
                onKeyDown={handleKeyDown}
                placeholder={`追问 ${activeTab.name || activeTab.symbol}...`}
                disabled={isStreaming}
                className="flex-1 px-3 md:px-4 py-2 md:py-2.5 bg-dark text-white rounded-xl border border-border
                  focus:border-violet-500 focus:ring-2 focus:ring-violet-500/20 outline-none transition-all
                  placeholder:text-gray-600 disabled:opacity-50 text-sm md:text-base"
              />

              {isStreaming ? (
                <button
                  onClick={handleCancel}
                  className="px-5 py-2.5 bg-red-500/15 text-red-400 border border-red-500/20 rounded-xl
                    hover:bg-red-500/25 transition-all flex items-center gap-2 text-sm font-medium"
                >
                  <svg className="h-4 w-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                    <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M6 18L18 6M6 6l12 12" />
                  </svg>
                  停止
                </button>
              ) : (
                <button
                  onClick={handleFollowUp}
                  disabled={!followUpInput.trim()}
                  className="px-5 py-2.5 bg-gradient-to-r from-violet-500 to-purple-600 text-white rounded-xl
                    hover:from-violet-600 hover:to-purple-700 shadow-lg shadow-purple-500/25
                    transition-all duration-300 font-medium
                    disabled:opacity-50 disabled:cursor-not-allowed flex items-center gap-2"
                >
                  <svg className="h-4 w-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                    <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M12 19l9 2-9-18-9 18 9-2zm0 0v-8" />
                  </svg>
                  发送
                </button>
              )}
            </div>
          </div>
        </div>
      )}
    </div>
  );
};

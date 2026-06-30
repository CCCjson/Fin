import React, { useState, useRef, useCallback, useEffect } from 'react';
import { agentService } from '../services/agentService';
import type { AgentStreamEvent, WidgetSpec } from '../services/agentService';
import { MarkdownView } from '../components/common/MarkdownView';

/* ================================================================
   MoneyBill 💰 — 对话式量化助手（Phase 1：文本 + 工具调用轨迹）
   ================================================================ */

type Part =
  | { kind: 'text'; text: string }
  | { kind: 'tool'; id: string; name: string; status: 'running' | 'done'; ok?: boolean }
  | { kind: 'widget'; widget: WidgetSpec }
  | { kind: 'handoff'; agent: string };

interface Msg {
  role: 'user' | 'assistant';
  parts: Part[];
}

const TOOL_LABELS: Record<string, string> = {
  search_stocks: '搜索股票',
  get_daily_data: '获取日线',
  get_realtime_quote: '实时行情',
  get_cockpit_score: '五维体检',
  predict_stock: '涨跌预测',
  get_positions: '查询持仓',
  get_performance: '组合绩效',
};

const QUICK_PROMPTS = [
  '帮我体检一下贵州茅台',
  '我现在持有哪些股票？',
  '看看比亚迪最近的走势',
  '我的组合绩效怎么样？',
];

export const MoneyBill: React.FC = () => {
  const [messages, setMessages] = useState<Msg[]>([]);
  const [input, setInput] = useState('');
  const [loading, setLoading] = useState(false);
  const sessionIdRef = useRef<string | undefined>(undefined);
  const abortRef = useRef<AbortController | null>(null);
  const chatEndRef = useRef<HTMLDivElement>(null);

  // rAF 节流的文本缓冲
  const bufferRef = useRef('');
  const rafRef = useRef<number | null>(null);

  useEffect(() => {
    chatEndRef.current?.scrollIntoView({ behavior: 'smooth' });
  }, [messages, loading]);

  const updateAssistant = useCallback((fn: (last: Msg) => void) => {
    setMessages((prev) => {
      if (prev.length === 0) return prev;
      const next = [...prev];
      const last: Msg = { ...next[next.length - 1], parts: [...next[next.length - 1].parts] };
      fn(last);
      next[next.length - 1] = last;
      return next;
    });
  }, []);

  const flushBuffer = useCallback(() => {
    rafRef.current = null;
    const text = bufferRef.current;
    if (!text) return;
    bufferRef.current = '';
    updateAssistant((last) => {
      const lastPart = last.parts[last.parts.length - 1];
      if (lastPart && lastPart.kind === 'text') {
        last.parts[last.parts.length - 1] = { kind: 'text', text: lastPart.text + text };
      } else {
        last.parts.push({ kind: 'text', text });
      }
    });
  }, [updateAssistant]);

  const scheduleFlush = useCallback(() => {
    if (rafRef.current == null) {
      rafRef.current = requestAnimationFrame(flushBuffer);
    }
  }, [flushBuffer]);

  const onEvent = useCallback((ev: AgentStreamEvent) => {
    switch (ev.event) {
      case 'session_created':
        sessionIdRef.current = ev.session_id;
        break;
      case 'chunk':
        if (ev.content) {
          bufferRef.current += ev.content;
          scheduleFlush();
        }
        break;
      case 'tool_call':
        flushBuffer();
        updateAssistant((last) =>
          last.parts.push({ kind: 'tool', id: ev.id || '', name: ev.name || '', status: 'running' }),
        );
        break;
      case 'tool_result':
        updateAssistant((last) => {
          last.parts = last.parts.map((p) =>
            p.kind === 'tool' && p.id === ev.id ? { ...p, status: 'done', ok: ev.ok } : p,
          );
        });
        break;
      case 'agent_handoff':
        flushBuffer();
        updateAssistant((last) => last.parts.push({ kind: 'handoff', agent: ev.agent || '' }));
        break;
      case 'widget':
        flushBuffer();
        if (ev.widget) updateAssistant((last) => last.parts.push({ kind: 'widget', widget: ev.widget! }));
        break;
      case 'error':
        flushBuffer();
        updateAssistant((last) =>
          last.parts.push({ kind: 'text', text: `\n\n⚠️ ${ev.message || '出错了'}` }),
        );
        break;
      case 'done':
        flushBuffer();
        break;
      default:
        break;
    }
  }, [flushBuffer, scheduleFlush, updateAssistant]);

  const send = useCallback(async (text: string) => {
    const msg = text.trim();
    if (!msg || loading) return;
    setInput('');
    setMessages((prev) => [
      ...prev,
      { role: 'user', parts: [{ kind: 'text', text: msg }] },
      { role: 'assistant', parts: [] },
    ]);
    setLoading(true);
    abortRef.current = new AbortController();
    try {
      await agentService.chat(
        { session_id: sessionIdRef.current, message: msg },
        onEvent,
        abortRef.current.signal,
      );
    } catch (e: any) {
      if (e?.name !== 'AbortError') {
        updateAssistant((last) => last.parts.push({ kind: 'text', text: `\n\n⚠️ ${e?.message || e}` }));
      }
    } finally {
      flushBuffer();
      setLoading(false);
      abortRef.current = null;
    }
  }, [loading, onEvent, flushBuffer, updateAssistant]);

  const stop = useCallback(() => {
    abortRef.current?.abort();
    setLoading(false);
  }, []);

  const newChat = useCallback(() => {
    abortRef.current?.abort();
    sessionIdRef.current = undefined;
    setMessages([]);
    setLoading(false);
  }, []);

  return (
    <div className="flex flex-col h-full bg-dark">
      {/* 顶栏 */}
      <div className="flex items-center justify-between px-6 py-4 border-b border-border">
        <div className="flex items-center gap-3">
          <span className="text-2xl">💰</span>
          <div>
            <h1 className="text-lg font-bold text-white">MoneyBill</h1>
            <p className="text-xs text-gray-500">你的私人量化助手 · 聊天即可调度全部工具</p>
          </div>
        </div>
        <button
          onClick={newChat}
          className="text-sm text-gray-400 hover:text-white border border-border hover:border-border-light rounded-lg px-3 py-1.5 transition-colors"
        >
          ＋ 新对话
        </button>
      </div>

      {/* 消息区 */}
      <div className="flex-1 overflow-y-auto px-4 md:px-8 py-6 space-y-5">
        {messages.length === 0 && (
          <div className="max-w-2xl mx-auto text-center mt-12">
            <div className="text-5xl mb-4">💰</div>
            <h2 className="text-xl font-bold text-white mb-2">嗨 Jason，我是 MoneyBill</h2>
            <p className="text-gray-400 mb-8">告诉我你想做什么，我会自己调用系统里的工具帮你搞定。</p>
            <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
              {QUICK_PROMPTS.map((q) => (
                <button
                  key={q}
                  onClick={() => send(q)}
                  className="text-left text-sm text-gray-300 bg-dark-light hover:bg-dark-lighter border border-border hover:border-primary/50 rounded-xl px-4 py-3 transition-colors"
                >
                  {q}
                </button>
              ))}
            </div>
          </div>
        )}

        {messages.map((m, i) => (
          <MessageBubble key={i} msg={m} streaming={loading && i === messages.length - 1} />
        ))}
        <div ref={chatEndRef} />
      </div>

      {/* 输入区 */}
      <div className="border-t border-border px-4 md:px-8 py-4">
        <div className="max-w-3xl mx-auto flex items-end gap-2">
          <textarea
            value={input}
            onChange={(e) => setInput(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === 'Enter' && !e.shiftKey) {
                e.preventDefault();
                send(input);
              }
            }}
            rows={1}
            placeholder="问问 MoneyBill…（Enter 发送，Shift+Enter 换行）"
            className="flex-1 resize-none bg-dark-light border border-border focus:border-primary/60 rounded-xl px-4 py-3 text-sm text-gray-200 placeholder-gray-600 outline-none max-h-40"
          />
          {loading ? (
            <button onClick={stop} className="shrink-0 bg-bear/80 hover:bg-bear text-white rounded-xl px-4 py-3 text-sm transition-colors">
              停止
            </button>
          ) : (
            <button
              onClick={() => send(input)}
              disabled={!input.trim()}
              className="shrink-0 bg-primary hover:bg-primary-light disabled:opacity-40 disabled:cursor-not-allowed text-dark rounded-xl px-5 py-3 text-sm font-medium transition-colors"
            >
              发送
            </button>
          )}
        </div>
      </div>
    </div>
  );
};

/* ---------------- 单条消息 ---------------- */
const MessageBubble: React.FC<{ msg: Msg; streaming: boolean }> = ({ msg, streaming }) => {
  if (msg.role === 'user') {
    return (
      <div className="flex justify-end">
        <div className="max-w-[80%] bg-primary/90 text-dark rounded-2xl rounded-tr-sm px-4 py-2.5 text-sm whitespace-pre-wrap">
          {msg.parts.map((p, i) => (p.kind === 'text' ? <span key={i}>{p.text}</span> : null))}
        </div>
      </div>
    );
  }

  return (
    <div className="flex justify-start">
      <div className="max-w-[88%] w-full">
        <div className="flex items-center gap-2 mb-1.5">
          <span className="text-lg">💰</span>
          <span className="text-xs text-gray-500">MoneyBill</span>
        </div>
        <div className="bg-dark-light border border-border rounded-2xl rounded-tl-sm px-5 py-4 space-y-2">
          {msg.parts.length === 0 && streaming && (
            <div className="flex items-center gap-2 text-gray-500 text-sm">
              <span className="inline-block w-2 h-4 bg-primary animate-pulse rounded-sm" />
              思考中…
            </div>
          )}
          {msg.parts.map((p, i) => {
            if (p.kind === 'text') {
              return (
                <div key={i} className="prose-invert max-w-none text-sm">
                  <MarkdownView>{p.text}</MarkdownView>
                </div>
              );
            }
            if (p.kind === 'tool') {
              return <ToolPill key={i} name={p.name} status={p.status} ok={p.ok} />;
            }
            if (p.kind === 'handoff') {
              return (
                <div key={i} className="text-xs text-accent-purple flex items-center gap-1.5">
                  <span>🤝</span> 交给子助手「{p.agent}」处理…
                </div>
              );
            }
            if (p.kind === 'widget') {
              // Phase 3 接入 WidgetRenderer；此处先占位展示
              return (
                <details key={i} className="text-xs bg-dark rounded-lg border border-border p-2">
                  <summary className="cursor-pointer text-gray-400">📊 结果卡片：{p.widget.title || p.widget.type}</summary>
                  <pre className="mt-2 overflow-x-auto text-gray-500">{JSON.stringify(p.widget.data, null, 2)}</pre>
                </details>
              );
            }
            return null;
          })}
          {streaming && msg.parts.some((p) => p.kind === 'text') && (
            <span className="inline-block w-2 h-4 bg-primary animate-pulse rounded-sm align-middle" />
          )}
        </div>
      </div>
    </div>
  );
};

/* ---------------- 工具调用小药丸 ---------------- */
const ToolPill: React.FC<{ name: string; status: 'running' | 'done'; ok?: boolean }> = ({ name, status, ok }) => {
  const label = TOOL_LABELS[name] || name;
  return (
    <div className="inline-flex items-center gap-2 text-xs bg-dark border border-border rounded-full px-3 py-1 text-gray-400 mr-2">
      {status === 'running' ? (
        <svg className="animate-spin h-3 w-3 text-primary" viewBox="0 0 24 24" fill="none">
          <circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4" />
          <path className="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4z" />
        </svg>
      ) : (
        <span className={ok === false ? 'text-bear' : 'text-bull'}>{ok === false ? '✕' : '✓'}</span>
      )}
      <span>{status === 'running' ? `正在${label}…` : label}</span>
    </div>
  );
};

export default MoneyBill;

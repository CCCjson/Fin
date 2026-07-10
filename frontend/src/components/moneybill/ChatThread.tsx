import React, { useState, useRef, useCallback, useEffect } from 'react';
import { useNavigate } from 'react-router-dom';
import { motion, AnimatePresence, useReducedMotion } from 'framer-motion';
import { MarkdownView } from '../common/MarkdownView';
import { WidgetRenderer } from './WidgetRenderer';
import { ConfirmDialog } from './ConfirmDialog';
import { SessionIdBadge } from './SessionIdBadge';
import { useChatStore } from '../../store/agentChatStore';
import type { ChatMessage, ChatSession } from '../../store/agentChatStore';
import { estimateTokens } from '../../store/usageStore';
import { useAgentChat } from '../../hooks/useAgentChat';
import { useImeGuard } from '../../hooks/useImeGuard';
import { navigateFromAgent } from '../../utils/agentNavigate';
import {
  fadeFromLeft, fadeFromRight, popIn, staggerContainer, fadeUp, pickVariants,
} from '../common/motion';
import {
  THINKING_COPY, TOOL_COPY, AGENT_RUNNING_COPY, FALLBACK_COPY, TOOL_FAIL,
  useRotatingPhrase, pickStable,
} from './statusCopy';

/* ================================================================
   MoneyBill 💰 聊天主体（接入多会话 store；统一外壳主区使用）
   ================================================================ */

const AGENT_LABELS: Record<string, string> = {
  run_deep_stock: '深度个股研判',
  run_news_analysis: '新闻舆情解读',
  run_alpha_lab: '策略研发',
  // 五个报告章节（13.2 把整篇周报拆开）
  report_market: '大盘与板块',
  report_news: '新闻舆情',
  report_positions: '持仓诊断',
  report_strategy: '回顾与策略',
  report_picks: '买入推荐',
};

const QUICK_PROMPTS: { icon: string; text: string }[] = [
  { icon: '🩺', text: '帮我体检一下贵州茅台' },
  { icon: '📊', text: '我现在持有哪些股票？' },
  { icon: '📈', text: '看看比亚迪最近的走势' },
  { icon: '💼', text: '我的组合绩效怎么样？' },
  { icon: '🔮', text: '预测一下宁德时代涨跌' },
  { icon: '⚡', text: '看看上证指数实时行情' },
];

export const ChatThread: React.FC = () => {
  const active = useChatStore((s) =>
    s.activeId ? s.sessions.find((x) => x.id === s.activeId) : undefined,
  ) as ChatSession | undefined;
  const messages = active?.messages ?? [];

  const [input, setInput] = useState('');
  const [showJump, setShowJump] = useState(false);
  const chatEndRef = useRef<HTMLDivElement>(null);
  const scrollRef = useRef<HTMLDivElement>(null);
  const navigate = useNavigate();

  const onNavigate = useCallback((path: string, symbol?: string) => {
    // 完整页导航 → 弹开浮窗续接对话（popFloating）
    navigateFromAgent(navigate, path, symbol, { popFloating: true });
  }, [navigate]);

  const { loading, pendingConfirm, send: sendMsg, respondConfirm, stop, retry } = useAgentChat({
    storeApi: useChatStore,
    onNavigate,
  });

  const send = useCallback((text: string) => {
    setInput('');
    void sendMsg(text);
  }, [sendMsg]);

  const { onCompositionStart, onCompositionEnd, shouldSend } = useImeGuard();

  useEffect(() => {
    useChatStore.getState().startFresh();
  }, []);

  // 粘底：只有用户没有主动上滚时才自动滚到底。
  // 解除粘底靠 wheel 向上（程序化平滑滚动不触发 wheel，不会误判）；滚回底部附近恢复粘底。
  const pinnedRef = useRef(true);

  useEffect(() => {
    if (pinnedRef.current) chatEndRef.current?.scrollIntoView({ behavior: 'smooth' });
  }, [messages, loading]);

  const onWheel = useCallback((e: React.WheelEvent) => {
    if (e.deltaY < 0) pinnedRef.current = false;
  }, []);

  // 上滑查看历史时显示「回到最新」按钮
  const onScroll = useCallback(() => {
    const el = scrollRef.current;
    if (!el) return;
    const dist = el.scrollHeight - el.scrollTop - el.clientHeight;
    if (dist < 120) pinnedRef.current = true;
    setShowJump(dist > 240);
  }, []);

  const jumpToLatest = useCallback(() => {
    pinnedRef.current = true;
    chatEndRef.current?.scrollIntoView({ behavior: 'smooth' });
  }, []);

  const inputTokens = input.trim() ? estimateTokens(input) : 0;

  return (
    <div className="flex flex-col h-full bg-dark relative">
      <SessionIdBadge sessionId={active?.serverSessionId} className="absolute top-2 right-4 md:right-8 z-10" />

      {/* 消息区（居中列，ChatGPT 风格） */}
      <div ref={scrollRef} onScroll={onScroll} onWheel={onWheel} className="flex-1 overflow-y-auto px-4 md:px-8 py-6">
        <div className="max-w-3xl mx-auto space-y-5">
        {messages.length === 0 && <EmptyHero onPick={send} />}

        {messages.map((m, i) => (
          <MessageBubble key={i} msg={m} streaming={loading && i === messages.length - 1} onRetry={retry} />
        ))}
        <div ref={chatEndRef} />
        </div>
      </div>

      {/* 回到最新 */}
      <AnimatePresence>
        {showJump && (
          <motion.button
            initial={{ opacity: 0, y: 8 }}
            animate={{ opacity: 1, y: 0 }}
            exit={{ opacity: 0, y: 8 }}
            onClick={jumpToLatest}
            className="absolute bottom-24 left-1/2 -translate-x-1/2 z-20 flex items-center gap-1.5 bg-dark-card/90 backdrop-blur border border-border-light hover:border-primary/60 text-gray-300 text-xs rounded-full px-3 py-1.5 shadow-card transition-colors"
          >
            <span>↓</span> 回到最新
          </motion.button>
        )}
      </AnimatePresence>

      {/* 输入区 */}
      <div className="px-4 md:px-8 py-4">
        <div className="max-w-3xl mx-auto">
          <div className="flex items-end gap-2">
            <textarea
              value={input}
              onChange={(e) => setInput(e.target.value)}
              onCompositionStart={onCompositionStart}
              onCompositionEnd={onCompositionEnd}
              onKeyDown={(e) => {
                // 输入法拼音选词按 Enter 时不发送，见 useImeGuard
                if (e.key === 'Enter' && !e.shiftKey && shouldSend(e)) {
                  e.preventDefault();
                  send(input);
                }
              }}
              rows={1}
              placeholder="问问 MoneyBill…（Enter 发送，Shift+Enter 换行）"
              className="flex-1 resize-none bg-dark-light border border-border focus:border-primary/60 rounded-xl px-4 py-3 text-sm text-gray-200 placeholder-gray-600 outline-none max-h-40 transition-colors"
            />
            {loading ? (
              <motion.button
                whileTap={{ scale: 0.94 }}
                onClick={stop}
                className="shrink-0 bg-bear/80 hover:bg-bear text-white rounded-xl px-4 py-3 text-sm transition-colors"
              >
                停止
              </motion.button>
            ) : (
              <motion.button
                whileTap={{ scale: 0.94 }}
                onClick={() => send(input)}
                disabled={!input.trim()}
                className="shrink-0 bg-primary hover:bg-primary-light disabled:opacity-40 disabled:cursor-not-allowed text-white rounded-xl px-5 py-3 text-sm font-medium transition-colors"
              >
                发送
              </motion.button>
            )}
          </div>
          {/* 提示行：token 估算 */}
          <div className="h-4 mt-1.5 px-1 flex justify-end">
            <AnimatePresence>
              {inputTokens > 0 && (
                <motion.span
                  initial={{ opacity: 0 }}
                  animate={{ opacity: 1 }}
                  exit={{ opacity: 0 }}
                  className="text-[10px] text-gray-600"
                >
                  ~{inputTokens} tokens
                </motion.span>
              )}
            </AnimatePresence>
          </div>
        </div>
      </div>

      {pendingConfirm && (
        <ConfirmDialog
          name={pendingConfirm.name}
          preview={pendingConfirm.preview}
          onConfirm={() => respondConfirm(true)}
          onCancel={() => respondConfirm(false)}
        />
      )}
    </div>
  );
};

/* ---------------- 空态欢迎页 ---------------- */
const EmptyHero: React.FC<{ onPick: (text: string) => void }> = ({ onPick }) => {
  const reduce = useReducedMotion();
  return (
    <motion.div
      variants={pickVariants(reduce, staggerContainer)}
      initial="hidden"
      animate="show"
      className="max-w-2xl mx-auto text-center mt-16"
    >
      <motion.div
        variants={pickVariants(reduce, fadeUp)}
        className="text-6xl mb-4 inline-block"
        animate={reduce ? undefined : { y: [0, -6, 0] }}
        transition={reduce ? undefined : { duration: 3.2, repeat: Infinity, ease: 'easeInOut' }}
        style={{ filter: 'drop-shadow(0 0 18px rgba(0,255,156,0.45))' }}
      >
        💰
      </motion.div>
      <motion.h2 variants={pickVariants(reduce, fadeUp)} className="text-2xl font-bold neon-text mb-2">
        嗨 Jason，我是 MoneyBill
      </motion.h2>
      <motion.p variants={pickVariants(reduce, fadeUp)} className="text-gray-400 mb-8">
        告诉我你想做什么，我会自己调用系统里的工具帮你搞定。
      </motion.p>
      <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
        {QUICK_PROMPTS.map((q) => (
          <motion.button
            key={q.text}
            variants={pickVariants(reduce, fadeUp)}
            whileHover={reduce ? undefined : { y: -3, boxShadow: '0 0 20px rgba(0,255,156,0.18)' }}
            whileTap={{ scale: 0.98 }}
            onClick={() => onPick(q.text)}
            className="flex items-center gap-3 text-left text-sm text-gray-300 bg-dark-light hover:bg-dark-lighter border border-border hover:border-primary/50 rounded-xl px-4 py-3 transition-colors"
          >
            <span className="text-lg shrink-0">{q.icon}</span>
            <span>{q.text}</span>
          </motion.button>
        ))}
      </div>
    </motion.div>
  );
};

/* ---------------- 单条消息 ---------------- */
// memo：updateAssistant 只重建最后一条消息对象，历史消息引用稳定 → 流式期间只有最后一条重渲染
export const MessageBubble: React.FC<{ msg: ChatMessage; streaming: boolean; onRetry?: () => void }> = React.memo(({ msg, streaming, onRetry }) => {
  const reduce = useReducedMotion();

  // 「正在沉思」出现时机：开场，或上一步是已完成的工具 / widget（模型正憋下一句话）。
  const lastPart = msg.parts[msg.parts.length - 1];
  const showThinking = streaming && (
    msg.parts.length === 0 ||
    lastPart.kind === 'widget' ||
    (lastPart.kind === 'tool' && lastPart.status === 'done')
  );

  if (msg.role === 'user') {
    return (
      <motion.div
        variants={pickVariants(reduce, fadeFromRight)}
        initial="hidden"
        animate="show"
        className="flex justify-end"
      >
        <div className="max-w-[80%] bg-primary/90 text-white rounded-2xl rounded-tr-sm px-4 py-2.5 text-sm whitespace-pre-wrap">
          {msg.parts.map((p, i) => (p.kind === 'text' ? <span key={i}>{p.text}</span> : null))}
        </div>
      </motion.div>
    );
  }

  return (
    <motion.div
      variants={pickVariants(reduce, fadeFromLeft)}
      initial="hidden"
      animate="show"
      className="flex justify-start"
    >
      <div className="max-w-[88%] w-full">
        <div className="flex items-center gap-2 mb-1.5">
          <span className="text-lg">💰</span>
          <span className="text-xs text-gray-500">MoneyBill</span>
        </div>
        <div className="bg-dark-light border border-border rounded-2xl rounded-tl-sm px-5 py-4 space-y-2">
          {msg.parts.map((p, i) => {
            if (p.kind === 'text') {
              return (
                <div key={i} className="max-w-none text-sm">
                  <MarkdownView>{p.text}</MarkdownView>
                </div>
              );
            }
            if (p.kind === 'tool') {
              return <ToolPill key={i} id={p.id} name={p.name} status={p.status} ok={p.ok} desc={p.desc} verdict={p.verdict} elapsedMs={p.elapsedMs} />;
            }
            if (p.kind === 'handoff') {
              return <Handoff key={i} agent={p.agent} />;
            }
            if (p.kind === 'progress') {
              return (
                <div key={i} className="text-xs text-gray-500 flex items-center gap-2 pl-1">
                  <span className="inline-block w-1.5 h-1.5 bg-accent-cyan rounded-full animate-pulse" />
                  {p.text}
                </div>
              );
            }
            if (p.kind === 'widget') {
              return <WidgetRenderer key={i} widget={p.widget} />;
            }
            if (p.kind === 'error') {
              return (
                <div key={i} className="flex flex-wrap items-center gap-2 text-sm text-bear">
                  <span>⚠️ {p.message}</span>
                  {onRetry && (
                    <button
                      onClick={onRetry}
                      className="shrink-0 rounded-full border border-bear/50 px-2.5 py-0.5 text-xs text-bear transition-colors hover:bg-bear/10"
                    >
                      重试
                    </button>
                  )}
                </div>
              );
            }
            return null;
          })}
          {showThinking && <ThinkingDots />}
          {streaming && msg.parts.some((p) => p.kind === 'text') && <StreamCaret />}
        </div>
      </div>
    </motion.div>
  );
});

/* ---------------- 思考中：三点跳动 + 轮换趣味文案 ---------------- */
const ThinkingDots: React.FC = () => {
  const reduce = useReducedMotion();
  const phrase = useRotatingPhrase(THINKING_COPY, true);
  return (
    <div className="flex items-center gap-2 text-gray-500 text-sm">
      <div className="flex items-center gap-1">
        {[0, 1, 2].map((i) => (
          <motion.span
            key={i}
            className="inline-block w-1.5 h-1.5 bg-primary rounded-full"
            animate={reduce ? undefined : { opacity: [0.3, 1, 0.3], y: [0, -3, 0] }}
            transition={reduce ? undefined : { duration: 1, repeat: Infinity, delay: i * 0.16, ease: 'easeInOut' }}
          />
        ))}
      </div>
      <AnimatePresence mode="wait" initial={false}>
        <motion.span
          key={phrase}
          initial={reduce ? undefined : { opacity: 0, y: 3 }}
          animate={reduce ? undefined : { opacity: 1, y: 0 }}
          exit={reduce ? undefined : { opacity: 0, y: -3 }}
          transition={{ duration: 0.25 }}
        >
          {phrase}
        </motion.span>
      </AnimatePresence>
    </div>
  );
};

/* ---------------- 子助手派单 ---------------- */
const Handoff: React.FC<{ agent: string }> = ({ agent }) => {
  const phrase = pickStable(AGENT_RUNNING_COPY[agent] || [`交给「${AGENT_LABELS[agent] || agent}」处理…`], agent);
  return (
    <div className="text-xs text-accent-purple flex items-center gap-1.5">
      <span>🤝</span> {phrase}
    </div>
  );
};

/* ---------------- 流式光标：呼吸 ---------------- */
const StreamCaret: React.FC = () => {
  const reduce = useReducedMotion();
  return (
    <motion.span
      className="inline-block w-1.5 h-4 bg-primary rounded-sm align-middle ml-0.5"
      animate={reduce ? undefined : { opacity: [1, 0.2, 1] }}
      transition={reduce ? undefined : { duration: 1.1, repeat: Infinity, ease: 'easeInOut' }}
    />
  );
};

/* ---------------- 工具调用小药丸 ---------------- */
// 监控质量徽标：有效✓（沿用 ok 绿）之外，空结果/重复拦截/风控拒单单独标出来
const PILL_BADGE: Record<string, { label: string; cls: string }> = {
  empty: { label: '空', cls: 'text-accent-orange' },
  duplicate: { label: '拦', cls: 'text-gray-500' },
  negative: { label: '拒', cls: 'text-accent-purple' },
};

const ToolPill: React.FC<{ id: string; name: string; status: 'running' | 'done'; ok?: boolean; desc?: string; verdict?: string; elapsedMs?: number }> = ({ id, name, status, ok, desc, verdict, elapsedMs }) => {
  const reduce = useReducedMotion();
  const running = status === 'running';
  // 按 tool_call id 锁定同一对台词：开工 run → 收工 done 讲同一个梗，刷新也不跳。
  const beat = pickStable(TOOL_COPY[name] || FALLBACK_COPY, id || name);
  // 完成态优先显示后端下发的成果话术（「完成了什么」）；旧消息无 desc 时回退趣味文案
  const baseText = running ? beat.run : (desc || (ok === false ? TOOL_FAIL : beat.done));
  const text = !running && elapsedMs != null && elapsedMs >= 1000
    ? `${baseText} · ${(elapsedMs / 1000).toFixed(1)}s`
    : baseText;
  const badge = !running && verdict ? PILL_BADGE[verdict] : undefined;
  return (
    <motion.div
      variants={pickVariants(reduce, popIn)}
      initial="hidden"
      animate="show"
      className="inline-flex items-center gap-2 text-xs bg-dark border border-border rounded-full px-3 py-1 text-gray-400 mr-2"
    >
      <AnimatePresence mode="wait" initial={false}>
        {running ? (
          <motion.svg
            key="spin"
            initial={{ opacity: 0 }}
            animate={{ opacity: 1 }}
            exit={{ opacity: 0 }}
            className="animate-spin h-3 w-3 text-primary"
            viewBox="0 0 24 24"
            fill="none"
          >
            <circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4" />
            <path className="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4z" />
          </motion.svg>
        ) : (
          <motion.span
            key="check"
            variants={pickVariants(reduce, popIn)}
            initial="hidden"
            animate="show"
            className={badge ? badge.cls : ok === false ? 'text-bear' : 'text-bull'}
          >
            {badge ? badge.label : ok === false ? '✕' : '✓'}
          </motion.span>
        )}
      </AnimatePresence>
      <AnimatePresence mode="wait" initial={false}>
        <motion.span
          key={text}
          initial={reduce ? undefined : { opacity: 0, y: 3 }}
          animate={reduce ? undefined : { opacity: 1, y: 0 }}
          exit={reduce ? undefined : { opacity: 0, y: -3 }}
          transition={{ duration: 0.22 }}
        >
          {text}
        </motion.span>
      </AnimatePresence>
    </motion.div>
  );
};

export default ChatThread;

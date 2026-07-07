import React, { useState, useRef, useEffect, useCallback } from 'react';
import { motion, AnimatePresence } from 'framer-motion';
import { useLocation, useNavigate } from 'react-router-dom';
import { useAgentChat } from '../../hooks/useAgentChat';
import { useImeGuard } from '../../hooks/useImeGuard';
import { useChatStore, useFloatingChatStore } from '../../store/agentChatStore';
import type { ChatSession } from '../../store/agentChatStore';
import { useFloatingChatUiStore } from '../../store/floatingChatUiStore';
import { capturePageContext } from '../../utils/pageContext';
import { navigateFromAgent } from '../../utils/agentNavigate';
import { MessageBubble } from './ChatThread';
import { ConfirmDialog } from './ConfirmDialog';
import { SessionIdBadge } from './SessionIdBadge';

/* ================================================================
   FloatingChat 💰 —— MoneyBill 的「最小化」浮窗
   仅在离开 MoneyBill 对话页（被「关闭」）时出现；在完整页面上不显示。
   任意工具页右下角悬浮，随手问；发送时带上「当前屏幕在看什么」
   （route + entities + 可见文字），理解「这只股票/当前」的指代。
   顶栏可拖动；会话两模式：共享（与完整页面同一份历史）/ 独立（临时）。
   ================================================================ */

const POS_KEY = 'moneybill-float-pos';
const PANEL_W = 380;
const MARGIN = 8;

type Pos = { left: number; top: number };

/** 读取上次拖动位置，并夹到当前视口内（防窗口缩小后跑出屏幕）。 */
function loadPos(): Pos | null {
  try {
    const raw = localStorage.getItem(POS_KEY);
    if (!raw) return null;
    const p = JSON.parse(raw) as Pos;
    if (typeof p?.left !== 'number' || typeof p?.top !== 'number') return null;
    const maxLeft = window.innerWidth - PANEL_W - MARGIN;
    return {
      left: Math.max(MARGIN, Math.min(p.left, Math.max(MARGIN, maxLeft))),
      top: Math.max(MARGIN, Math.min(p.top, Math.max(MARGIN, window.innerHeight - 120))),
    };
  } catch {
    return null;
  }
}

const BUBBLE_POS_KEY = 'moneybill-bubble-pos';
const BUBBLE_SIZE = 56; // h-14 w-14
const DRAG_THRESHOLD = 4; // 移动超过它才算拖动，否则当点击

/** 读取圆球上次拖动位置并夹到视口内。 */
function loadBubblePos(): Pos | null {
  try {
    const raw = localStorage.getItem(BUBBLE_POS_KEY);
    if (!raw) return null;
    const p = JSON.parse(raw) as Pos;
    if (typeof p?.left !== 'number' || typeof p?.top !== 'number') return null;
    return {
      left: Math.max(MARGIN, Math.min(p.left, window.innerWidth - BUBBLE_SIZE - MARGIN)),
      top: Math.max(MARGIN, Math.min(p.top, window.innerHeight - BUBBLE_SIZE - MARGIN)),
    };
  } catch {
    return null;
  }
}

const QUICK: string[] = ['这只股票现在能买吗？', '屏幕上现在显示了什么？', '帮我总结当前页面的重点'];

export const FloatingChat: React.FC = () => {
  const { pathname } = useLocation();
  const navigate = useNavigate();
  // 浮窗是 MoneyBill 的最小化形态：完整对话页上不显示
  const onMoneyBillPage = pathname === '/' || pathname === '/app';

  // open / mode 提升到全局 store：让 agent 驱动导航能远程弹开浮窗并强制共享会话
  const open = useFloatingChatUiStore((s) => s.open);
  const setOpen = useFloatingChatUiStore((s) => s.setOpen);
  const mode = useFloatingChatUiStore((s) => s.mode);
  const toggleMode = useFloatingChatUiStore((s) => s.toggleMode);
  const [input, setInput] = useState('');
  const [pos, setPos] = useState<Pos | null>(() => loadPos()); // 面板拖动位置（持久化）
  const [bubblePos, setBubblePos] = useState<Pos | null>(() => loadBubblePos()); // 圆球拖动位置（持久化）
  const chatEndRef = useRef<HTMLDivElement>(null);
  const chatScrollRef = useRef<HTMLDivElement>(null);
  const pinnedRef = useRef(true); // 粘底：用户上滚后不再强制滚到底
  const panelRef = useRef<HTMLDivElement>(null);
  const bubbleRef = useRef<HTMLButtonElement>(null);
  const bubbleDragRef = useRef(false);
  const bubbleLastRef = useRef<Pos | null>(null);

  // 两个 store 都无条件订阅（遵守 hooks 规则），按 mode 取用
  const sharedActive = useChatStore((s) =>
    s.activeId ? s.sessions.find((x) => x.id === s.activeId) : undefined,
  ) as ChatSession | undefined;
  const floatActive = useFloatingChatStore((s) =>
    s.activeId ? s.sessions.find((x) => x.id === s.activeId) : undefined,
  ) as ChatSession | undefined;

  const storeApi = mode === 'shared' ? useChatStore : useFloatingChatStore;
  const active = mode === 'shared' ? sharedActive : floatActive;
  const messages = active?.messages ?? [];

  const onNavigate = useCallback((path: string, symbol?: string) => {
    // 浮窗内导航：切页但不强制切共享模式（保留用户当前会话模式），浮窗保持打开
    navigateFromAgent(navigate, path, symbol, { popFloating: false });
    setOpen(true);
  }, [navigate, setOpen]);

  const { loading, pendingConfirm, send: sendMsg, respondConfirm, stop, retry } = useAgentChat({
    storeApi,
    getPageContext: capturePageContext,
    onNavigate,
  });

  const send = useCallback((text: string) => {
    if (!text.trim()) return;
    setInput('');
    void sendMsg(text);
  }, [sendMsg]);

  const { onCompositionStart, onCompositionEnd, shouldSend } = useImeGuard();

  const newChat = useCallback(() => {
    storeApi.getState().newSession();
  }, [storeApi]);

  const expand = useCallback(() => {
    setOpen(false);
    navigate('/');
  }, [navigate]);

  // ---- 拖动（按住顶栏；pointerdown 时挂 window 原生监听，拖动全程稳，不依赖指针捕获）----
  const onDragStart = useCallback((e: React.PointerEvent) => {
    if ((e.target as HTMLElement).closest('button')) return;
    const el = panelRef.current;
    if (!el) return;
    e.preventDefault();
    const startX = e.clientX;
    const startY = e.clientY;
    const ox = el.offsetLeft; // 用布局位置起步（无视入场动画 transform，避免抓取跳动）
    const oy = el.offsetTop;
    const w = el.offsetWidth || PANEL_W;
    const h = el.offsetHeight || 560;

    const onMove = (ev: PointerEvent) => {
      let left = ox + (ev.clientX - startX);
      let top = oy + (ev.clientY - startY);
      left = Math.max(MARGIN, Math.min(left, window.innerWidth - w - MARGIN));
      top = Math.max(MARGIN, Math.min(top, window.innerHeight - h - MARGIN));
      setPos({ left, top });
    };
    const onUp = () => {
      window.removeEventListener('pointermove', onMove);
      window.removeEventListener('pointerup', onUp);
      const cur = panelRef.current;
      if (cur) {
        try { localStorage.setItem(POS_KEY, JSON.stringify({ left: cur.offsetLeft, top: cur.offsetTop })); } catch { /* ignore */ }
      }
    };
    window.addEventListener('pointermove', onMove);
    window.addEventListener('pointerup', onUp);
  }, []);

  // ---- 圆球拖动（同 token 胶囊：阈值区分点击/拖动，落点存 ref，松手持久化）----
  const onBubblePointerDown = useCallback((e: React.PointerEvent) => {
    const el = bubbleRef.current;
    if (!el) return;
    const rect = el.getBoundingClientRect();
    const startX = e.clientX;
    const startY = e.clientY;
    const ox = rect.left;
    const oy = rect.top;
    const w = rect.width || BUBBLE_SIZE;
    const h = rect.height || BUBBLE_SIZE;
    bubbleDragRef.current = false;

    const onMove = (ev: PointerEvent) => {
      if (!bubbleDragRef.current && Math.hypot(ev.clientX - startX, ev.clientY - startY) < DRAG_THRESHOLD) return;
      bubbleDragRef.current = true;
      let left = ox + (ev.clientX - startX);
      let top = oy + (ev.clientY - startY);
      left = Math.max(MARGIN, Math.min(left, window.innerWidth - w - MARGIN));
      top = Math.max(MARGIN, Math.min(top, window.innerHeight - h - MARGIN));
      bubbleLastRef.current = { left, top };
      setBubblePos({ left, top });
    };
    const onUp = () => {
      window.removeEventListener('pointermove', onMove);
      window.removeEventListener('pointerup', onUp);
      if (bubbleDragRef.current && bubbleLastRef.current) {
        try { localStorage.setItem(BUBBLE_POS_KEY, JSON.stringify(bubbleLastRef.current)); } catch { /* ignore */ }
      } else if (!bubbleDragRef.current) {
        setOpen(true); // 几乎没动 → 当点击，展开面板
      }
    };
    window.addEventListener('pointermove', onMove);
    window.addEventListener('pointerup', onUp);
  }, []);

  useEffect(() => {
    if (open && pinnedRef.current) chatEndRef.current?.scrollIntoView({ behavior: 'smooth' });
  }, [messages, loading, open]);

  // wheel 向上 → 解除粘底（程序化平滑滚动不触发 wheel）；滚回底部附近 → 恢复粘底
  const onChatWheel = useCallback((e: React.WheelEvent) => {
    if (e.deltaY < 0) pinnedRef.current = false;
  }, []);
  const onChatScroll = useCallback(() => {
    const el = chatScrollRef.current;
    if (!el) return;
    if (el.scrollHeight - el.scrollTop - el.clientHeight < 120) pinnedRef.current = true;
  }, []);

  // 在 MoneyBill 完整页面上完全不渲染（浮窗 = 最小化形态）
  if (onMoneyBillPage) return null;

  const panelStyle: React.CSSProperties = pos
    ? { left: pos.left, top: pos.top, right: 'auto', bottom: 'auto', height: 'min(560px, calc(100vh - 3rem))' }
    : { height: 'min(560px, calc(100vh - 3rem))' };

  return (
    <>
      {/* 悬浮按钮 */}
      <AnimatePresence>
        {!open && (
          <motion.button
            ref={bubbleRef}
            initial={{ scale: 0, opacity: 0 }}
            animate={{ scale: 1, opacity: 1 }}
            exit={{ scale: 0, opacity: 0 }}
            whileHover={{ scale: 1.08 }}
            onPointerDown={onBubblePointerDown}
            title="问问 MoneyBill（可拖动）"
            className={`fixed z-40 flex h-14 w-14 items-center justify-center rounded-full bg-primary text-2xl shadow-card cursor-grab active:cursor-grabbing${bubblePos ? '' : ' bottom-6 right-6'}`}
            style={{
              filter: 'drop-shadow(0 0 14px rgba(0,255,156,0.5))',
              touchAction: 'none',
              ...(bubblePos ? { left: bubblePos.left, top: bubblePos.top } : {}),
            }}
          >
            💰
          </motion.button>
        )}
      </AnimatePresence>

      {/* 对话面板 */}
      <AnimatePresence>
        {open && (
          <motion.div
            ref={panelRef}
            initial={{ opacity: 0, y: 24, scale: 0.96 }}
            animate={{ opacity: 1, y: 0, scale: 1 }}
            exit={{ opacity: 0, y: 24, scale: 0.96 }}
            transition={{ type: 'spring', stiffness: 300, damping: 26 }}
            className="fixed bottom-6 right-6 z-40 flex w-[380px] max-w-[calc(100vw-2rem)] flex-col rounded-2xl border border-border bg-dark-card shadow-card"
            style={panelStyle}
          >
            {/* 顶栏（拖动手柄） */}
            <div
              onPointerDown={onDragStart}
              style={{ touchAction: 'none' }}
              className="flex cursor-move select-none items-center justify-between border-b border-border px-4 py-2.5"
            >
              <div className="flex items-center gap-2">
                <span className="text-lg">💰</span>
                <span className="text-sm font-bold text-white">MoneyBill</span>
              </div>
              <div className="flex items-center gap-1.5">
                <SessionIdBadge sessionId={active?.serverSessionId} />
                <button
                  onClick={toggleMode}
                  title={mode === 'shared' ? '共享：与完整页面同一会话' : '独立：临时会话，问完即走'}
                  className={`rounded-full border px-2 py-0.5 text-[10px] transition-colors ${
                    mode === 'shared'
                      ? 'border-primary/50 text-primary'
                      : 'border-accent-purple/50 text-accent-purple'
                  }`}
                >
                  {mode === 'shared' ? '共享会话' : '独立会话'}
                </button>
                <IconBtn onClick={expand} title="展开为完整页面">⤢</IconBtn>
                <IconBtn onClick={newChat} title="新对话">＋</IconBtn>
                <IconBtn onClick={() => setOpen(false)} title="收起">×</IconBtn>
              </div>
            </div>

            {/* 消息区 */}
            <div ref={chatScrollRef} onScroll={onChatScroll} onWheel={onChatWheel} className="flex-1 space-y-4 overflow-y-auto px-3 py-4">
              {messages.length === 0 && (
                <div className="mt-6 text-center">
                  <div className="mb-2 text-3xl">💰</div>
                  <p className="mb-4 px-4 text-xs text-gray-500">
                    我能看到你当前页面在看什么，直接问吧～
                  </p>
                  <div className="space-y-2 px-2">
                    {QUICK.map((q) => (
                      <button
                        key={q}
                        onClick={() => send(q)}
                        className="block w-full rounded-lg border border-border bg-dark-light px-3 py-2 text-left text-xs text-gray-300 transition-colors hover:border-primary/50"
                      >
                        {q}
                      </button>
                    ))}
                  </div>
                </div>
              )}
              {messages.map((m, i) => (
                <MessageBubble key={i} msg={m} streaming={loading && i === messages.length - 1} onRetry={retry} />
              ))}
              <div ref={chatEndRef} />
            </div>

            {/* 输入区 */}
            <div className="border-t border-border p-2.5">
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
                  placeholder="问问 MoneyBill…"
                  className="max-h-28 flex-1 resize-none rounded-xl border border-border bg-dark-light px-3 py-2 text-sm text-gray-200 placeholder-gray-600 outline-none transition-colors focus:border-primary/60"
                />
                {loading ? (
                  <button
                    onClick={stop}
                    className="shrink-0 rounded-xl bg-bear/80 px-3 py-2 text-xs text-white transition-colors hover:bg-bear"
                  >
                    停止
                  </button>
                ) : (
                  <button
                    onClick={() => send(input)}
                    disabled={!input.trim()}
                    className="shrink-0 rounded-xl bg-primary px-4 py-2 text-xs font-medium text-white transition-colors hover:bg-primary-light disabled:cursor-not-allowed disabled:opacity-40"
                  >
                    发送
                  </button>
                )}
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
          </motion.div>
        )}
      </AnimatePresence>
    </>
  );
};

const IconBtn: React.FC<{ onClick: () => void; title: string; children: React.ReactNode }> = ({ onClick, title, children }) => (
  <button
    onClick={onClick}
    title={title}
    className="flex h-6 w-6 items-center justify-center rounded-md text-gray-400 transition-colors hover:bg-dark-light hover:text-white"
  >
    {children}
  </button>
);

export default FloatingChat;

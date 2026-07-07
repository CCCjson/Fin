import { create } from 'zustand';
import { persist } from 'zustand/middleware';
import type { StateCreator } from 'zustand';
import type { PersistStorage, StorageValue } from 'zustand/middleware';
import type { WidgetSpec } from '../services/agentService';

/* ================================================================
   MoneyBill 多会话 store（zustand）
   - useChatStore：持久化（localStorage），完整页面 + 浮窗「共享」模式共用。
   - useFloatingChatStore：非持久化临时实例，浮窗「独立」模式用，问完即走。
   两者结构完全一致，靠 createChatStore 工厂产出，配 useAgentChat hook 复用。
   ================================================================ */

export type ChatPart =
  | { kind: 'text'; text: string }
  | { kind: 'tool'; id: string; name: string; status: 'running' | 'done'; ok?: boolean; desc?: string; verdict?: string; elapsedMs?: number }
  | { kind: 'widget'; widget: WidgetSpec }
  | { kind: 'handoff'; agent: string }
  | { kind: 'progress'; text: string }
  | { kind: 'error'; message: string };

export interface ChatMessage {
  role: 'user' | 'assistant';
  parts: ChatPart[];
}

export interface ChatSession {
  id: string;
  title: string;
  serverSessionId?: string;
  messages: ChatMessage[];
  createdAt: number;
}

export interface ChatState {
  sessions: ChatSession[];
  activeId: string | null;

  newSession: () => string;
  ensureActive: () => string;
  startFresh: () => string;
  switchSession: (id: string) => void;
  deleteSession: (id: string) => void;
  renameSession: (id: string, title: string) => void;
  setServerSessionId: (id: string, sid: string) => void;
  startTurn: (id: string, userText: string) => void;
  updateAssistant: (id: string, fn: (m: ChatMessage) => void) => void;
}

const newId = () =>
  (globalThis.crypto?.randomUUID?.() ?? `s_${Math.random().toString(36).slice(2)}_${performance.now()}`);

const creator: StateCreator<ChatState> = (set, get) => ({
  sessions: [],
  activeId: null,

  newSession: () => {
    const id = newId();
    const sess: ChatSession = { id, title: '新对话', messages: [], createdAt: Date.now() };
    set((s) => ({ sessions: [sess, ...s.sessions], activeId: id }));
    return id;
  },

  ensureActive: () => {
    const { activeId, sessions, newSession } = get();
    if (activeId && sessions.some((s) => s.id === activeId)) return activeId;
    if (sessions.length > 0) {
      set({ activeId: sessions[0].id });
      return sessions[0].id;
    }
    return newSession();
  },

  // 启动时默认进入一个新的空对话：不恢复上次会话，
  // 但复用顶部已是空的会话，避免每次刷新堆积空「新对话」。
  startFresh: () => {
    const { sessions, newSession } = get();
    if (sessions.length > 0 && sessions[0].messages.length === 0) {
      set({ activeId: sessions[0].id });
      return sessions[0].id;
    }
    return newSession();
  },

  switchSession: (id) => set({ activeId: id }),

  deleteSession: (id) =>
    set((s) => {
      const sessions = s.sessions.filter((x) => x.id !== id);
      let activeId = s.activeId;
      if (activeId === id) activeId = sessions[0]?.id ?? null;
      return { sessions, activeId };
    }),

  renameSession: (id, title) =>
    set((s) => ({ sessions: s.sessions.map((x) => (x.id === id ? { ...x, title } : x)) })),

  setServerSessionId: (id, sid) =>
    set((s) => ({ sessions: s.sessions.map((x) => (x.id === id ? { ...x, serverSessionId: sid } : x)) })),

  startTurn: (id, userText) =>
    set((s) => ({
      sessions: s.sessions.map((x) => {
        if (x.id !== id) return x;
        const isFirst = x.messages.length === 0;
        return {
          ...x,
          title: isFirst ? userText.slice(0, 24) || '新对话' : x.title,
          messages: [
            ...x.messages,
            { role: 'user', parts: [{ kind: 'text', text: userText }] },
            { role: 'assistant', parts: [] },
          ],
        };
      }),
    })),

  updateAssistant: (id, fn) =>
    set((s) => ({
      sessions: s.sessions.map((x) => {
        if (x.id !== id || x.messages.length === 0) return x;
        const messages = [...x.messages];
        const lastIdx = messages.length - 1;
        const last: ChatMessage = { ...messages[lastIdx], parts: [...messages[lastIdx].parts] };
        fn(last);
        messages[lastIdx] = last;
        return { ...x, messages };
      }),
    })),
});

/**
 * 节流版 localStorage：流式期间每个 rAF flush 都会触发 persist 落盘，
 * 长会话下每秒几十次「全量 stringify + 同步写盘」会把 WKWebView 内存/主线程压垮
 * （WebContent 进程被杀后静默重载页面，表现为"闪退回底部"）。
 * 这里把序列化推迟到 flush 时刻：窗口期内只保存最新 state 引用（消息更新是不可变的，持引用安全），
 * 至多每 delay 毫秒写一次；刷新/关闭前强制 flush 防丢尾。
 */
function throttledStorage<T>(delay = 500): PersistStorage<T> {
  let timer: number | null = null;
  let pendingKey: string | null = null;
  let pendingValue: StorageValue<T> | null = null;

  const flush = () => {
    if (timer != null) {
      window.clearTimeout(timer);
      timer = null;
    }
    if (pendingKey != null && pendingValue != null) {
      localStorage.setItem(pendingKey, JSON.stringify(pendingValue));
      pendingKey = null;
      pendingValue = null;
    }
  };
  window.addEventListener('beforeunload', flush);
  window.addEventListener('pagehide', flush);

  return {
    getItem: (name) => {
      const raw = localStorage.getItem(name);
      return raw ? (JSON.parse(raw) as StorageValue<T>) : null;
    },
    setItem: (name, value) => {
      pendingKey = name;
      pendingValue = value;
      if (timer == null) timer = window.setTimeout(flush, delay);
    },
    removeItem: (name) => {
      pendingKey = null;
      pendingValue = null;
      localStorage.removeItem(name);
    },
  };
}

/** 工厂：persistName 存在则持久化到 localStorage，否则内存临时实例。 */
export function createChatStore(persistName?: string) {
  if (persistName) {
    return create<ChatState>()(
      persist(creator, {
        name: persistName,
        // 不持久化 activeId：刷新/重开后默认从新的空对话开始，历史仍在 sessions 中。
        partialize: (s) => ({ sessions: s.sessions }),
        storage: throttledStorage<{ sessions: ChatSession[] }>(500),
      }),
    );
  }
  return create<ChatState>()(creator);
}

/** 持久化主 store：完整页面 + 浮窗「共享」模式共用同一份会话历史。 */
export const useChatStore = createChatStore('moneybill-chat');

/** 非持久化临时 store：浮窗「独立」模式专用，问完即走、不进历史列表。 */
export const useFloatingChatStore = createChatStore();

export type ChatStoreApi = typeof useChatStore;

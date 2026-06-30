import { create } from 'zustand';
import { persist } from 'zustand/middleware';
import type { WidgetSpec } from '../services/agentService';

/* ================================================================
   MoneyBill 多会话 store（zustand + localStorage 持久化）
   刷新/重开浏览器，会话历史仍在。
   ================================================================ */

export type ChatPart =
  | { kind: 'text'; text: string }
  | { kind: 'tool'; id: string; name: string; status: 'running' | 'done'; ok?: boolean }
  | { kind: 'widget'; widget: WidgetSpec }
  | { kind: 'handoff'; agent: string }
  | { kind: 'progress'; text: string };

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

interface ChatState {
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

export const useChatStore = create<ChatState>()(
  persist(
    (set, get) => ({
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
    }),
    {
      name: 'moneybill-chat',
      // 不持久化 activeId：刷新/重开后默认从新的空对话开始，历史仍在 sessions 中。
      partialize: (s) => ({ sessions: s.sessions }),
    },
  ),
);

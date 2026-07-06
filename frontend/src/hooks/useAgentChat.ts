import { useCallback, useRef, useState } from 'react';
import { agentService } from '../services/agentService';
import type { AgentStreamEvent, PageContext } from '../services/agentService';
import type { ChatStoreApi } from '../store/agentChatStore';
import { useUsageStore, estimateTokens } from '../store/usageStore';
import { useMonitorStore, interventionText } from '../store/monitorStore';
import type { TurnSummary } from '../store/monitorStore';

/* ================================================================
   useAgentChat —— MoneyBill 流式对话核心逻辑（从 ChatThread 抽出复用）
   完整页面(ChatThread) 与 浮窗(FloatingChat) 共用同一套编排。
   storeApi 只用 getState() 驱动写入，不在此反应式订阅；消息渲染由组件自行订阅。
   ================================================================ */

export interface PendingConfirm {
  id: string;
  name: string;
  preview: any;
}

interface UseAgentChatOptions {
  /** 用哪个会话 store（共享持久化 or 独立临时）。*/
  storeApi: ChatStoreApi;
  /** 发送时采集页面上下文（route / entities / visible_text），随消息一起上传。*/
  getPageContext?: () => PageContext | undefined;
  /** 覆盖模型（浮窗可传 cheap 档省钱）。*/
  model?: string;
  /** agent 驱动导航回调：收到 navigate 事件时切页（并把对话带进浮窗续接）。*/
  onNavigate?: (path: string, symbol?: string) => void;
}

export function useAgentChat({ storeApi, getPageContext, model, onNavigate }: UseAgentChatOptions) {
  const [loading, setLoading] = useState(false);
  const [pendingConfirm, setPendingConfirm] = useState<PendingConfirm | null>(null);
  const abortRef = useRef<AbortController | null>(null);

  // rAF 节流的文本缓冲
  const bufferRef = useRef('');
  const rafRef = useRef<number | null>(null);

  const flushBuffer = useCallback((id: string) => {
    rafRef.current = null;
    const text = bufferRef.current;
    if (!text) return;
    bufferRef.current = '';
    storeApi.getState().updateAssistant(id, (last) => {
      const lastPart = last.parts[last.parts.length - 1];
      if (lastPart && lastPart.kind === 'text') {
        last.parts[last.parts.length - 1] = { kind: 'text', text: lastPart.text + text };
      } else {
        last.parts.push({ kind: 'text', text });
      }
    });
  }, [storeApi]);

  const scheduleFlush = useCallback((id: string) => {
    if (rafRef.current == null) rafRef.current = requestAnimationFrame(() => flushBuffer(id));
  }, [flushBuffer]);

  const makeOnEvent = useCallback((id: string) => (ev: AgentStreamEvent) => {
    const s = storeApi.getState();
    switch (ev.event) {
      case 'start':
        // turn 开始的信号（run_stream / resume_with_confirmation 起手都会发）。
        // loading/streaming 状态已经在 runStream() 调用前同步置位，这里无需
        // 额外动作；显式列出这个 case 只是为了和"真正未知的事件类型"区分开，
        // 别让协议里声明过的事件悄悄落进 default 分支变得像是没处理。
        break;
      case 'session_created':
        if (ev.session_id) s.setServerSessionId(id, ev.session_id);
        break;
      case 'chunk':
        if (ev.content) {
          bufferRef.current += ev.content;
          useUsageStore.getState().addLiveTokens(estimateTokens(ev.content));
          scheduleFlush(id);
        }
        break;
      case 'tool_call':
        flushBuffer(id);
        s.updateAssistant(id, (last) =>
          last.parts.push({ kind: 'tool', id: ev.id || '', name: ev.name || '', status: 'running' }),
        );
        useMonitorStore.getState().callStart(ev.id || '', ev.name || '');
        break;
      case 'tool_result':
        s.updateAssistant(id, (last) => {
          last.parts = last.parts.map((p) =>
            p.kind === 'tool' && p.id === ev.id
              ? { ...p, status: 'done', ok: ev.ok, desc: ev.desc, verdict: ev.verdict, elapsedMs: ev.elapsed_ms }
              : p,
          );
        });
        useMonitorStore.getState().callEnd(ev.id || '', {
          ok: ev.ok, verdict: ev.verdict, elapsedMs: ev.elapsed_ms,
        });
        break;
      case 'agent_handoff':
        flushBuffer(id);
        s.updateAssistant(id, (last) => last.parts.push({ kind: 'handoff', agent: ev.agent || '' }));
        break;
      case 'agent_progress':
        flushBuffer(id);
        s.updateAssistant(id, (last) => {
          const lp = last.parts[last.parts.length - 1];
          if (lp && lp.kind === 'progress') {
            last.parts[last.parts.length - 1] = { kind: 'progress', text: ev.message || '' };
          } else {
            last.parts.push({ kind: 'progress', text: ev.message || '' });
          }
        });
        break;
      case 'widget':
        flushBuffer(id);
        if (ev.widget) s.updateAssistant(id, (last) => last.parts.push({ kind: 'widget', widget: ev.widget! }));
        break;
      case 'navigate':
        if (ev.path) onNavigate?.(ev.path, ev.symbol);
        break;
      case 'confirm_required':
        flushBuffer(id);
        // 后端强制要求确认回传时点名真实 tool_call_id（缺省一律被拒），所以这里
        // 不再用空串兜底：正常情况下 ev.id 必有值，缺失说明后端异常，静默塞空串
        // 只会让确认永远匹配失败得莫名其妙——宁可留个告警把问题暴露出来。
        if (!ev.id) console.warn('confirm_required 缺少 tool_call_id，后端可能异常，确认将无法匹配');
        setPendingConfirm({ id: ev.id ?? '', name: ev.name || '', preview: ev.preview });
        break;
      case 'await_confirm':
        // 确认弹窗已经由上面的 confirm_required 驱动出来了；这个事件只是后端
        // 状态机里"轮到用户决策"的标记，前端没有额外要做的，显式列出同上。
        break;
      case 'error':
        flushBuffer(id);
        s.updateAssistant(id, (last) => last.parts.push({ kind: 'text', text: `\n\n⚠️ ${ev.message || '出错了'}` }));
        break;
      case 'usage':
        if (ev.cumulative) useUsageStore.getState().setSnapshot(ev.cumulative);
        useMonitorStore.getState().bumpRound(); // 每轮 LLM 结束发一次 usage → 轮次+1
        break;
      case 'monitor': {
        const m = useMonitorStore.getState();
        if (ev.kind === 'intervention' && ev.action) {
          m.pushIntervention({
            action: ev.action,
            round: ev.round,
            text: interventionText(ev.action, ev.detail),
          });
        } else if (ev.kind === 'turn_summary') {
          m.endTurn({
            rounds: ev.rounds ?? 0,
            calls: ev.calls ?? 0,
            verdicts: ev.verdicts ?? {},
            elapsedMsTotal: ev.elapsed_ms_total ?? 0,
            tokens: ev.turn_tokens ?? { prompt: 0, completion: 0 },
            interventions: ev.interventions ?? [],
          } as TurnSummary);
        }
        break;
      }
      case 'done':
        flushBuffer(id);
        break;
      default:
        break;
    }
  }, [storeApi, flushBuffer, scheduleFlush, onNavigate]);

  const runStream = useCallback(async (id: string, params: any) => {
    setLoading(true);
    useMonitorStore.getState().setStreaming(true);
    abortRef.current = new AbortController();
    try {
      await agentService.chat(
        { ...(model ? { model } : {}), ...params },
        makeOnEvent(id),
        abortRef.current.signal,
      );
    } catch (e: any) {
      if (e?.name !== 'AbortError') {
        storeApi.getState().updateAssistant(id, (last) =>
          last.parts.push({ kind: 'text', text: `\n\n⚠️ ${e?.message || e}` }),
        );
      }
    } finally {
      flushBuffer(id);
      setLoading(false);
      useMonitorStore.getState().setStreaming(false);
      abortRef.current = null;
    }
  }, [storeApi, model, makeOnEvent, flushBuffer]);

  const send = useCallback(async (text: string) => {
    const msg = text.trim();
    if (!msg || loading) return;
    const store = storeApi.getState();
    const id = store.ensureActive();
    const serverSessionId = store.sessions.find((x) => x.id === id)?.serverSessionId;
    store.startTurn(id, msg);
    useMonitorStore.getState().beginTurn(); // 新用户消息=新 turn；确认续跑不清空，轨迹接着累计
    const page_context = getPageContext?.();
    await runStream(id, {
      session_id: serverSessionId,
      message: msg,
      ...(page_context ? { page_context } : {}),
    });
  }, [loading, storeApi, getPageContext, runStream]);

  const respondConfirm = useCallback(async (approved: boolean) => {
    const pc = pendingConfirm;
    if (!pc) return;
    setPendingConfirm(null);
    const store = storeApi.getState();
    const id = store.activeId;
    if (!id) return;
    const serverSessionId = store.sessions.find((x) => x.id === id)?.serverSessionId;
    await runStream(id, { session_id: serverSessionId, confirm: { tool_call_id: pc.id, approved } });
  }, [pendingConfirm, storeApi, runStream]);

  const stop = useCallback(() => {
    abortRef.current?.abort();
    setLoading(false);
  }, []);

  return { loading, pendingConfirm, send, respondConfirm, stop } as const;
}

const API_BASE = import.meta.env.VITE_API_URL || '/api';

// ==================== 类型定义 ====================

export type AgentEventType =
  | 'session_created'
  | 'start'
  | 'chunk'
  | 'tool_call'
  | 'tool_result'
  | 'agent_handoff'
  | 'agent_progress'
  | 'widget'
  | 'confirm_required'
  | 'await_confirm'
  | 'usage'
  | 'done'
  | 'error';

export interface UsageToday {
  date: string;
  prompt_tokens: number;
  completion_tokens: number;
  total_tokens: number;
  cost_usd: number;
  calls: number;
}

export interface UsageSnapshot {
  prompt_tokens: number;
  completion_tokens: number;
  total_tokens: number;
  cost_usd: number;
  calls: number;
  by_model?: Record<string, any>;
  today?: UsageToday;
}

export interface WidgetSpec {
  type: string;
  title?: string;
  data: any;
}

export interface AgentStreamEvent {
  event: AgentEventType;
  session_id?: string;
  // chunk
  content?: string;
  // tool_call / tool_result / handoff
  id?: string;
  name?: string;
  agent?: string;
  args?: Record<string, any>;
  ok?: boolean;
  // agent_progress 透传的子事件
  inner?: any;
  // widget
  widget?: WidgetSpec;
  // confirm
  preview?: any;
  // usage
  turn?: { prompt_tokens: number; completion_tokens: number };
  cumulative?: UsageSnapshot;
  // error
  message?: string;
  reason?: string;
}

export interface PageContext {
  page: string;
  entities?: Record<string, any>;
}

export interface AgentChatParams {
  session_id?: string;
  message?: string;
  model?: string;
  confirm?: { tool_call_id: string; approved: boolean };
  page_context?: PageContext;
}

// ==================== API ====================

export const agentService = {
  /** MoneyBill 流式对话（NDJSON） */
  chat: async (
    params: AgentChatParams,
    onEvent: (event: AgentStreamEvent) => void,
    signal?: AbortSignal,
  ): Promise<void> => {
    const response = await fetch(`${API_BASE}/agent/chat`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(params),
      signal,
    });
    if (!response.ok) throw new Error(`请求失败: ${response.status}`);

    const reader = response.body?.getReader();
    if (!reader) throw new Error('无法获取响应流');

    const decoder = new TextDecoder();
    let buffer = '';

    const flush = (line: string) => {
      const trimmed = line.trim();
      if (!trimmed) return;
      try {
        onEvent(JSON.parse(trimmed) as AgentStreamEvent);
      } catch (e) {
        console.warn('解析 MoneyBill 事件失败:', trimmed, e);
      }
    };

    while (true) {
      const { done, value } = await reader.read();
      if (done) break;
      buffer += decoder.decode(value, { stream: true });
      const lines = buffer.split('\n');
      buffer = lines.pop() || '';
      for (const line of lines) flush(line);
    }
    if (buffer.trim()) flush(buffer);
  },

  getSession: async (sessionId: string) => {
    const r = await fetch(`${API_BASE}/agent/session/${sessionId}`);
    if (!r.ok) throw new Error(`请求失败: ${r.status}`);
    return r.json();
  },

  getUsage: async (): Promise<UsageSnapshot> => {
    const r = await fetch(`${API_BASE}/agent/usage`);
    if (!r.ok) throw new Error(`请求失败: ${r.status}`);
    return r.json();
  },
};

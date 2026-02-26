const API_BASE = import.meta.env.VITE_API_URL || '/api';

// ==================== 类型定义 ====================

export interface AdvisorStreamEvent {
  event: 'session_created' | 'collecting' | 'start' | 'chunk' | 'done' | 'error';
  session_id?: string;
  message?: string;
  content?: string;
  token_count?: number;
  generation_time?: number;
}

export interface AdvisorChatParams {
  symbol: string;
  session_id?: string;
  message?: string;
  enable_web_search?: boolean;
  model?: string;
}

// ==================== API ====================

export const advisorService = {
  /**
   * 流式对话（NDJSON）
   */
  chat: async (
    params: AdvisorChatParams,
    onEvent: (event: AdvisorStreamEvent) => void,
    signal?: AbortSignal,
  ): Promise<void> => {
    const response = await fetch(`${API_BASE}/advisor/chat`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(params),
      signal,
    });

    if (!response.ok) {
      throw new Error(`请求失败: ${response.status}`);
    }

    const reader = response.body?.getReader();
    if (!reader) throw new Error('无法获取响应流');

    const decoder = new TextDecoder();
    let buffer = '';

    while (true) {
      const { done, value } = await reader.read();
      if (done) break;

      buffer += decoder.decode(value, { stream: true });

      const lines = buffer.split('\n');
      buffer = lines.pop() || '';

      for (const line of lines) {
        const trimmed = line.trim();
        if (!trimmed) continue;
        try {
          const event: AdvisorStreamEvent = JSON.parse(trimmed);
          onEvent(event);
        } catch (e) {
          console.warn('解析 advisor 事件失败:', trimmed, e);
        }
      }
    }

    if (buffer.trim()) {
      try {
        const event: AdvisorStreamEvent = JSON.parse(buffer.trim());
        onEvent(event);
      } catch (e) {
        console.warn('解析最后的 advisor 事件失败:', buffer, e);
      }
    }
  },
};

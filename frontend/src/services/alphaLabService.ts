/**
 * Alpha Lab API 服务
 */
import api from './api';

const API_BASE = import.meta.env.VITE_API_URL || '/api';

// ==================== 类型定义 ====================

export interface AlphaLabStreamEvent {
  event:
    | 'session_created'
    | 'preparing_data'
    | 'data_ready'
    | 'iteration_start'
    | 'generating_code'
    | 'code_generated'
    | 'ast_check_passed'
    | 'ast_rejected'
    | 'backtest_running'
    | 'backtest_done'
    | 'backtest_failed'
    | 'evaluation_done'
    | 'iteration_complete'
    | 'iteration_error'
    | 'code_error'
    | 'phase_change'
    | 'early_stop'
    | 'session_complete'
    | 'error';
  session_id?: string;
  iteration?: number;
  model?: string;
  phase?: string;
  message?: string;
  error?: string;
  code_preview?: string;
  tokens?: number;
  train_sharpe?: number;
  val_sharpe?: number;
  overfit_score?: number;
  composite_score?: number;
  is_best?: boolean;
  overfit_warnings?: string[];
  violations?: string[];
  symbol?: string;
  best_iteration?: number;
  best_sharpe?: number;
  total_iterations?: number;
  total_cost_usd?: number;
  reason?: string;
  from?: string;
  to?: string;
}

export interface StartParams {
  target_symbols: string[];
  optimization_goal: string;
  data_start: string;
  data_end: string;
  max_iterations?: number;
  initial_capital?: number;
  constraints?: Record<string, unknown>;
}

export interface SessionSummary {
  id: string;
  target_symbols: string[];
  optimization_goal: string;
  data_start: string;
  data_end: string;
  status: string;
  total_iterations: number;
  best_iteration: number | null;
  best_sharpe: number | null;
  best_composite_score: number | null;
  cost_usd: number;
  total_tokens: number;
  created_at: string | null;
  completed_at: string | null;
}

export interface StrategySummary {
  id: string;
  iteration: number;
  code: string;
  ai_reasoning: string;
  train_metrics: Record<string, unknown>;
  val_metrics: Record<string, unknown>;
  overfit_score: number;
  composite_score: number;
  status: string;
  error_message: string | null;
  execution_time: number;
  deployed: boolean;
  created_at: string | null;
}

// ==================== API 方法 ====================

export const alphaLabService = {
  /**
   * 启动 Alpha Lab 会话（NDJSON 流式）
   */
  start: async (
    params: StartParams,
    onEvent: (event: AlphaLabStreamEvent) => void,
    signal?: AbortSignal,
  ): Promise<void> => {
    const response = await fetch(`${API_BASE}/alpha-lab/start`, {
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
          const event: AlphaLabStreamEvent = JSON.parse(trimmed);
          onEvent(event);
        } catch {
          console.warn('解析 Alpha Lab 事件失败:', trimmed);
        }
      }
    }

    if (buffer.trim()) {
      try {
        onEvent(JSON.parse(buffer.trim()));
      } catch {
        // ignore
      }
    }
  },

  /**
   * 获取会话列表
   */
  getSessions: async (status?: string): Promise<{ sessions: SessionSummary[]; total: number }> => {
    const params = status ? `?status=${status}` : '';
    return api.get(`/alpha-lab/sessions${params}`) as any;
  },

  /**
   * 获取会话详情
   */
  getSessionDetail: async (sessionId: string): Promise<SessionSummary & { strategies: StrategySummary[] }> => {
    return api.get(`/alpha-lab/sessions/${sessionId}`) as any;
  },

  /**
   * 获取策略详情
   */
  getStrategy: async (strategyId: string): Promise<StrategySummary> => {
    return api.get(`/alpha-lab/strategies/${strategyId}`) as any;
  },

  /**
   * 删除会话
   */
  deleteSession: async (sessionId: string): Promise<{ success: boolean }> => {
    return api.delete(`/alpha-lab/sessions/${sessionId}`) as any;
  },
};

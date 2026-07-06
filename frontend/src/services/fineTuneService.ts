/**
 * Fine-Tune API 服务（NDJSON 流式）
 */
import api from './api';
import { authFetch } from '../utils/authFetch';

const API_BASE = import.meta.env.VITE_API_URL || '/api';

// ==================== 类型定义 ====================

export interface FineTuneStreamEvent {
  event:
    | 'pipeline_start'
    | 'stage_start'
    | 'stage_complete'
    | 'train_start'
    | 'train_step'
    | 'val_step'
    | 'log'
    | 'train_complete'
    | 'eval_start'
    | 'eval_progress'
    | 'eval_complete'
    | 'pipeline_complete'
    | 'pipeline_error';
  // pipeline
  stages?: string[];
  stage?: string;
  stage_index?: number;
  total_stages?: number;
  data?: Record<string, any>;
  // train
  config?: Record<string, any>;
  total_iters?: number;
  iter?: number;
  train_loss?: number;
  learning_rate?: number;
  it_sec?: number;
  tokens_sec?: number | null;
  val_loss?: number;
  line?: string;
  success?: boolean;
  // evaluate
  total_tests?: number;
  index?: number;
  prompt?: string;
  quality?: {
    ast_valid: boolean;
    has_class: boolean;
    has_method: boolean;
    has_imports: boolean;
    safety_ok: boolean;
    score: number;
  };
  avg_score?: number;
  ast_pass_rate?: number;
  class_rate?: number;
  method_rate?: number;
  imports_rate?: number;
  safety_rate?: number;
  // error
  error?: string;
  message?: string;
}

export interface StartParams {
  mode?: string;         // "local" | "remote"
  iters: number;
  learning_rate: number;
  batch_size: number;
  num_tests?: number;
  skip_evaluate?: boolean;
  skip_deploy?: boolean;
}

export interface DataStats {
  train: number;
  valid: number;
  test: number;
  has_data: boolean;
}

// ==================== API 方法 ====================

export const fineTuneService = {
  /**
   * 启动 Fine-Tune Pipeline（NDJSON 流式）
   */
  start: async (
    params: StartParams,
    onEvent: (event: FineTuneStreamEvent) => void,
    signal?: AbortSignal,
  ): Promise<void> => {
    const response = await authFetch(`${API_BASE}/fine-tune/start`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(params),
      signal,
    });

    if (!response.ok) {
      const text = await response.text();
      throw new Error(`请求失败: ${response.status} ${text}`);
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
          const event: FineTuneStreamEvent = JSON.parse(trimmed);
          onEvent(event);
        } catch {
          console.warn('解析 Fine-Tune 事件失败:', trimmed);
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
   * 终止训练
   */
  stop: async (): Promise<{ stopped: boolean }> => {
    return api.post('/fine-tune/stop') as any;
  },

  /**
   * 获取训练状态
   */
  getStatus: async (): Promise<{ running: boolean }> => {
    return api.get('/fine-tune/status') as any;
  },

  /**
   * 断线重连 — 获取缓存的训练事件（刷新后恢复状态）
   */
  reconnect: async (
    onEvent: (event: FineTuneStreamEvent) => void,
  ): Promise<void> => {
    const response = await authFetch(`${API_BASE}/fine-tune/reconnect`);
    if (!response.ok) return; // 404 = 没有训练记录，忽略

    const reader = response.body?.getReader();
    if (!reader) return;

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
          const event: FineTuneStreamEvent = JSON.parse(trimmed);
          onEvent(event);
        } catch {
          // ignore
        }
      }
    }
  },

  /**
   * 获取数据统计
   */
  getDataStats: async (): Promise<DataStats> => {
    return api.get('/fine-tune/data-stats') as any;
  },
};

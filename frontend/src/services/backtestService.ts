import api from './api';
import type { BatchBacktestConfig, BatchProgressEvent, BatchBacktestSummary } from '../types';

const API_BASE = import.meta.env.VITE_API_URL || '/api';

export const backtestService = {
  // 运行回测
  runBacktest: async (params: {
    task_id?: string;
    name: string;
    strategy_type: string;
    strategy_params?: Record<string, any>;
    symbols: string[];
    start_date: string;
    end_date: string;
    initial_capital?: number;
  }) => {
    return api.post('/history/backtests', params);
  },

  // 获取回测任务列表
  getBacktestTasks: async (params?: {
    status?: string;
    strategy_type?: string;
    limit?: number;
    offset?: number;
  }) => {
    return api.get('/history/backtests', { params });
  },

  // 获取回测结果
  getBacktestResult: async (taskId: string) => {
    return api.get(`/history/backtests/${taskId}`);
  },

  // 按策略类型对比回测结果
  compareBacktestsByStrategy: async (strategyType?: string) => {
    return api.get('/history/backtests/comparison', {
      params: { strategy_type: strategyType }
    });
  },

  // 删除回测任务
  deleteBacktestTask: async (taskId: string) => {
    return api.delete(`/history/backtests/${taskId}`);
  },

  // C++ 回测：运行并持久化
  runCppBacktest: async (params: {
    symbol: string;
    strategy: string;
    params: Record<string, any>;
    initial_capital?: number;
    market?: string;
    start_date?: string;
    end_date?: string;
    name?: string;
    symbol2?: string;
    slippage_pct?: number;
    risk_config?: {
      enabled: boolean;
      stop_loss_pct: number;
      trailing_stop: boolean;
      trailing_stop_pct: number;
      max_position_pct: number;
    };
  }) => {
    return api.post('/backtest_cpp/run_and_save', params);
  },

  // 按 task_id 列表对比回测（含资金曲线）
  compareBacktestsByIds: async (taskIds: string[]) => {
    return api.post('/history/backtests/compare', { task_ids: taskIds });
  },

  // ── 批量回测 ──

  runBatchBacktest: async (
    config: BatchBacktestConfig,
    onEvent: (event: BatchProgressEvent) => void,
    signal?: AbortSignal,
  ): Promise<void> => {
    const response = await fetch(`${API_BASE}/backtest_cpp/batch`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(config),
      signal,
    });

    if (!response.ok) {
      const text = await response.text();
      let detail = text;
      try { detail = JSON.parse(text).detail || text; } catch {}
      throw new Error(detail);
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
          const event: BatchProgressEvent = JSON.parse(trimmed);
          onEvent(event);
        } catch {
          console.warn('解析批量回测事件失败:', trimmed);
        }
      }
    }

    // 处理缓冲区残余
    if (buffer.trim()) {
      try {
        const event: BatchProgressEvent = JSON.parse(buffer.trim());
        onEvent(event);
      } catch {}
    }
  },

  getBatchResult: async (batchId: string) => {
    return api.get(`/backtest_cpp/batch/${batchId}`);
  },

  listBatches: async (limit: number = 20): Promise<{ batches: BatchBacktestSummary[] }> => {
    return api.get('/backtest_cpp/batches', { params: { limit } });
  },

  // ── Walk-Forward ──

  runWalkForward: async (
    config: any,
    onEvent: (event: any) => void,
    signal?: AbortSignal,
  ): Promise<void> => {
    const response = await fetch(`${API_BASE}/walk_forward/run`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(config),
      signal,
    });

    if (!response.ok) {
      const text = await response.text();
      let detail = text;
      try { detail = JSON.parse(text).detail || text; } catch {}
      throw new Error(detail);
    }

    const reader = response.body?.getReader();
    if (!reader) throw new Error('No response stream');

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
          onEvent(JSON.parse(trimmed));
        } catch {}
      }
    }

    if (buffer.trim()) {
      try { onEvent(JSON.parse(buffer.trim())); } catch {}
    }
  },

  // ── 股票池 ──

  getPoolStocks: async (poolId: string): Promise<{ pool_id: string; name: string; count: number; stocks: { symbol: string; name: string }[] }> => {
    return api.get(`/stock_pools/pools/${poolId}`);
  },

  getIndustryList: async (): Promise<{ industries: { name: string }[]; count: number }> => {
    return api.get('/stock_pools/industries');
  },

  getIndustryStocks: async (name: string): Promise<{ industry: string; count: number; stocks: { symbol: string; name: string }[] }> => {
    return api.get(`/stock_pools/industries/${encodeURIComponent(name)}`);
  },
};

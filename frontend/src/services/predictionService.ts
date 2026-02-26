import api from './api';

const API_BASE = import.meta.env.VITE_API_URL || 'http://127.0.0.1:8000';

// ──────────────── 类型定义 ────────────────

export interface TrainProgressEvent {
  event: 'start' | 'progress' | 'complete' | 'error';
  stage?: string;
  progress?: number;
  message?: string;
  result?: TrainResult;
}

export interface TrainResult {
  symbol: string;
  lstm: { mse: number; mae: number };
  xgboost: { accuracy: number; auc: number };
  data_points: number;
  train_range: [string, string];
}

export interface ModelInfo {
  id: number;
  symbol: string;
  model_type: string;
  train_period: string;
  data_points: number;
  train_start: string | null;
  train_end: string | null;
  val_metrics: Record<string, any>;
  status: string;
  created_at: string | null;
}

export interface PredictionResult {
  symbol: string;
  prediction_date: string;
  forward_days: number;
  direction: 'UP' | 'DOWN';
  confidence: number;
  predicted_prices: number[];
  predicted_return: number;
  models: {
    lstm: { direction: string; predicted_prices: number[]; predicted_return: number; mse: number };
    xgboost: { direction: string; probability_up: number; accuracy: number };
  };
  agreement: boolean;
}

export interface PredictionRecord {
  id: number;
  symbol: string;
  prediction_date: string;
  target_date: string | null;
  forward_days: number;
  direction: string;
  confidence: number;
  predicted_prices: number[];
  predicted_return: number;
  actual_prices: number[] | null;
  actual_return: number | null;
  outcome: string | null;
  lstm_detail: any;
  xgb_detail: any;
  created_at: string | null;
}

export interface PerformanceData {
  total_predictions: number;
  evaluated: number;
  pending: number;
  overall: {
    direction_accuracy: number;
    price_mae: number | null;
    price_rmse: number | null;
    avg_confidence: number;
  } | null;
  by_model: {
    lstm: { direction_accuracy: number };
    xgboost: { direction_accuracy: number };
    ensemble: { direction_accuracy: number };
  } | null;
  confidence_calibration: Array<{
    bucket: string;
    count: number;
    actual_accuracy: number | null;
  }>;
  cumulative_return: number;
  recent_streak: { type: string; count: number } | null;
}

// ──────────────── API 方法 ────────────────

export const predictionService = {
  // 流式训练模型
  trainModel: async (
    params: { symbol: string; period: string; forward_days?: number },
    onProgress: (event: TrainProgressEvent) => void,
    signal?: AbortSignal,
  ): Promise<void> => {
    const response = await fetch(`${API_BASE}/prediction/train`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(params),
      signal,
    });

    if (!response.ok) {
      throw new Error(`训练请求失败: ${response.status}`);
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
          const event: TrainProgressEvent = JSON.parse(trimmed);
          onProgress(event);
        } catch (e) {
          console.warn('解析训练进度失败:', trimmed, e);
        }
      }
    }

    if (buffer.trim()) {
      try {
        const event: TrainProgressEvent = JSON.parse(buffer.trim());
        onProgress(event);
      } catch (e) {
        console.warn('解析最后的进度事件失败:', buffer, e);
      }
    }
  },

  // 查询已训练模型
  getModels: async (symbol?: string): Promise<{ count: number; models: ModelInfo[] }> => {
    return api.get('/prediction/models', { params: symbol ? { symbol } : {} });
  },

  // 生成预测
  predict: async (params: { symbol: string; forward_days: number }): Promise<PredictionResult> => {
    return api.post('/prediction/predict', params);
  },

  // 历史预测记录
  getHistory: async (symbol?: string, limit?: number): Promise<{ count: number; records: PredictionRecord[] }> => {
    return api.get('/prediction/history', { params: { symbol, limit } });
  },

  // 手动回填
  backfill: async (): Promise<{ total: number; filled: number; errors: number }> => {
    return api.post('/prediction/backfill', {});
  },

  // 预测表现统计
  getPerformance: async (symbol?: string): Promise<PerformanceData> => {
    return api.get('/prediction/performance', { params: symbol ? { symbol } : {} });
  },
};

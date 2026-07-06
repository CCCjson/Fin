import { authFetch } from '../utils/authFetch';
const API_BASE = import.meta.env.VITE_API_URL || '/api';

// ==================== 类型定义 ====================

export interface NewsStreamEvent {
  event: 'fetching' | 'fetched' | 'analyzing' | 'complete' | 'start' | 'chunk' | 'done' | 'error';
  message?: string;
  content?: string;
  total?: number;
  new_count?: number;
  analysis_id?: string;
  token_count?: number;
  generation_time?: number;
}

export interface NewsArticle {
  article_id: string;
  symbol: string | null;
  market: string;
  title: string;
  content: string | null;
  summary: string | null;
  source: string | null;
  url: string | null;
  image_url: string | null;
  language: string;
  published_at: string | null;
  fetched_at: string | null;
  sentiment: {
    sentiment: 'positive' | 'negative' | 'neutral';
    confidence: number;
    prob_positive: number;
    prob_negative: number;
    prob_neutral: number;
    model_used: string;
  } | null;
  // 仅 sort=importance 时后端才会算这两个字段（api/routes/news.py::get_articles）
  category?: string | null;
  score?: number;
}

export interface FetchNewsParams {
  symbol?: string;
  market: string;
}

export interface GetArticlesParams {
  symbol?: string;
  market?: string;
  sentiment?: string;
  limit?: number;
  offset?: number;
  sort?: 'recent' | 'importance';
}

export interface AnalyzeParams {
  article_id: string;
  model?: string;
}

export interface ReportParams {
  symbol?: string;
  market?: string;
  model?: string;
}

export interface NewsJobStatus {
  running: boolean;
  enabled: boolean;
  is_updating: boolean;
  interval_minutes: number;
  cache_clear_cron: string;
  next_run: string | null;
  jobs: { id: string; name: string; next_run: string | null }[];
  last_run: {
    ok?: boolean;
    started_at?: string;
    completed_at?: string;
    new_general?: number;
    new_symbol?: Record<string, number>;
    high_impact?: {
      symbol: string | null;
      category?: string | null;
      reason: string;
      title: string;
      url?: string;
    }[];
  } | null;
  cache_size: number;
}

// ==================== 流式 NDJSON 解析 ====================

async function consumeNDJSON(
  response: Response,
  onEvent: (event: NewsStreamEvent) => void,
  _signal?: AbortSignal,
): Promise<void> {
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
        const event: NewsStreamEvent = JSON.parse(trimmed);
        onEvent(event);
      } catch (e) {
        console.warn('解析 news 事件失败:', trimmed, e);
      }
    }
  }

  if (buffer.trim()) {
    try {
      const event: NewsStreamEvent = JSON.parse(buffer.trim());
      onEvent(event);
    } catch (e) {
      console.warn('解析最后的 news 事件失败:', buffer, e);
    }
  }
}

// ==================== API ====================

export const newsService = {
  /**
   * 抓取新闻 + BERT 分析（流式）
   */
  fetchNews: async (
    params: FetchNewsParams,
    onEvent: (event: NewsStreamEvent) => void,
    signal?: AbortSignal,
  ): Promise<void> => {
    const response = await authFetch(`${API_BASE}/news/fetch`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(params),
      signal,
    });
    if (!response.ok) throw new Error(`请求失败: ${response.status}`);
    await consumeNDJSON(response, onEvent, signal);
  },

  /**
   * 查询已缓存新闻（带情感）
   */
  getArticles: async (params: GetArticlesParams): Promise<{ total: number; articles: NewsArticle[] }> => {
    const query = new URLSearchParams();
    if (params.symbol) query.set('symbol', params.symbol);
    if (params.market) query.set('market', params.market);
    if (params.sentiment) query.set('sentiment', params.sentiment);
    if (params.limit) query.set('limit', String(params.limit));
    if (params.offset) query.set('offset', String(params.offset));
    if (params.sort) query.set('sort', params.sort);

    const response = await authFetch(`${API_BASE}/news/articles?${query.toString()}`);
    if (!response.ok) throw new Error(`请求失败: ${response.status}`);
    return response.json();
  },

  /**
   * OpenAI 单篇深度分析（流式）
   */
  analyzeArticle: async (
    params: AnalyzeParams,
    onEvent: (event: NewsStreamEvent) => void,
    signal?: AbortSignal,
  ): Promise<void> => {
    const response = await authFetch(`${API_BASE}/news/analyze`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ article_id: params.article_id, model: params.model || 'gpt-5.4-mini' }),
      signal,
    });
    if (!response.ok) throw new Error(`请求失败: ${response.status}`);
    await consumeNDJSON(response, onEvent, signal);
  },

  /**
   * OpenAI 综合新闻报告（流式）
   */
  generateReport: async (
    params: ReportParams,
    onEvent: (event: NewsStreamEvent) => void,
    signal?: AbortSignal,
  ): Promise<void> => {
    const response = await authFetch(`${API_BASE}/news/report`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        symbol: params.symbol,
        market: params.market || 'a_share',
        model: params.model || 'gpt-5.4-mini',
      }),
      signal,
    });
    if (!response.ok) throw new Error(`请求失败: ${response.status}`);
    await consumeNDJSON(response, onEvent, signal);
  },

  /**
   * Newnew 定时新闻任务状态
   */
  getJobStatus: async (): Promise<NewsJobStatus> => {
    const response = await authFetch(`${API_BASE}/news/job/status`);
    if (!response.ok) throw new Error(`请求失败: ${response.status}`);
    return response.json();
  },

  /**
   * 开/关 Newnew 定时新闻任务
   */
  toggleJob: async (enabled: boolean): Promise<NewsJobStatus> => {
    const response = await authFetch(`${API_BASE}/news/job/toggle`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ enabled }),
    });
    if (!response.ok) throw new Error(`请求失败: ${response.status}`);
    return response.json();
  },

  /**
   * 手动立即跑一轮（debug 用）
   */
  runJobOnce: async (): Promise<Record<string, unknown>> => {
    const response = await authFetch(`${API_BASE}/news/job/run-once`, { method: 'POST' });
    if (!response.ok) throw new Error(`请求失败: ${response.status}`);
    return response.json();
  },
};

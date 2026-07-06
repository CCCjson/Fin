import api from './api';
import { authFetch } from '../utils/authFetch';

const API_BASE = import.meta.env.VITE_API_URL || '/api';

// ====== 类型 ======
export interface KnowledgeStats {
  documents: number;
  documents_by_source: Record<string, number>;
  chunks: number;
  ideas: number;
  vectors: number;
}

export interface KnowledgeDoc {
  doc_id: string;
  source_type: string;
  title: string;
  url: string;
  language: string;
  status: string;
  chunk_count: number;
  published_at: string | null;
  fetched_at: string | null;
}

export interface SearchHit {
  chunk_id: number;
  doc_id: string;
  distance: number;
  title: string;
  source_type: string;
  url: string;
  published_at: string;
  text: string;
}

export interface SearchResult {
  summary: string;
  hits: SearchHit[];
  widget?: any;
}

export interface AlphaIdea {
  idea_id: string;
  source_doc_id: string;
  title: string;
  hypothesis: string;
  factor_definition: string;
  optimization_goal: string;
  rationale: string;
  suggested_symbols: string[];
  status: string;
  alpha_lab_session_id: string | null;
  best_composite_score: number | null;
  created_at: string | null;
}

// 慢任务（摄入/挖掘）单独给长超时，默认 api 超时 15s 不够
const SLOW = { timeout: 180000 };

// ====== 抓取进度（带 sessionID，见 knowledge_engine/browser/sessions.py） ======

export interface ScrapeStreamEvent {
  event: 'session_created' | 'progress' | 'done' | 'error';
  session_id: string;
  stage?: string;
  message?: string;
  result?: any;
  [k: string]: any;
}

export interface ScrapeSession {
  session_id: string;
  domain: string | null;
  url: string | null;
  stage: string;
  status: 'running' | 'done' | 'error';
  error: string | null;
  created_at: number;
  updated_at: number;
}

// ====== cninfo 财报批量摄入（常驻后台任务，轮询状态） ======

export interface CninfoRecentItem {
  symbol: string;
  status: 'ok' | 'failed';
  ingested: number;
  skipped: number;
  failed: number;
  at: string;
}

export interface CninfoIngestStatus {
  status: 'idle' | 'running' | 'stopping' | 'stopped' | 'done';
  total_all: number;
  done_total: number;
  failed_symbols: number;
  processed_this_run: number;
  ingested: number;
  skipped: number;
  failed_docs: number;
  current_symbol: string | null;
  rate_per_min: number;
  eta_seconds: number | null;
  elapsed_seconds: number;
  recent: CninfoRecentItem[];
  config: Record<string, any>;
}

// ====== 东财研报批量摄入（常驻后台任务，轮询状态） ======

export interface ResearchReportRecentItem {
  symbol: string;
  status: 'ok' | 'failed';
  ingested: number;
  skipped: number;
  failed: number;
  at: string;
}

export interface ResearchReportIngestStatus {
  status: 'idle' | 'running' | 'stopping' | 'stopped' | 'done';
  total_all: number;
  done_total: number;
  failed_symbols: number;
  processed_this_run: number;
  ingested: number;
  skipped: number;
  failed_docs: number;
  current_symbol: string | null;
  rate_per_min: number;
  eta_seconds: number | null;
  elapsed_seconds: number;
  recent: ResearchReportRecentItem[];
  config: Record<string, any>;
}

// ====== arXiv 论文摄入（一次性同步慢任务，官方API） ======

export interface ArxivIngestDoc {
  doc_id: string;
  title: string;
  arxiv_id: string;
  status: string;
}

export interface ArxivIngestResult {
  ingested: number;
  skipped: number;
  failed: number;
  docs: ArxivIngestDoc[];
}

export const knowledgeService = {
  getStats: () => api.get<KnowledgeStats>('/knowledge/stats'),

  getDocuments: (params: { source_type?: string; limit?: number } = {}) => {
    const q = new URLSearchParams();
    if (params.source_type) q.set('source_type', params.source_type);
    if (params.limit) q.set('limit', String(params.limit));
    const qs = q.toString();
    return api.get<KnowledgeDoc[]>(`/knowledge/documents${qs ? `?${qs}` : ''}`);
  },

  search: (body: { query: string; top_k?: number; source_type?: string }) =>
    api.post<SearchResult>('/knowledge/search', body),

  getIdeas: (status?: string) =>
    api.get<AlphaIdea[]>(`/knowledge/ideas${status && status !== 'all' ? `?status=${status}` : ''}`),

  ingestPapers: (body: { queries: string[]; max_docs?: number }) =>
    api.post<{ ingested: number; skipped: number; failed: number }>(
      '/knowledge/ingest/papers', body, SLOW),

  mineIdeas: (body: { limit?: number }) =>
    api.post<{ mined: number; skipped: number }>('/knowledge/ideas/mine', body, SLOW),

  listScrapeSessions: (limit = 50) =>
    api.get<ScrapeSession[]>(`/knowledge/scrape/sessions?limit=${limit}`),

  getCninfoIngestStatus: () => api.get<CninfoIngestStatus>('/knowledge/ingest/cninfo/status'),

  startCninfoIngest: (body: {
    categories?: string[]; per_category?: number; max_pages?: number;
    start_date?: string; end_date?: string;
  } = {}) => api.post<{ ok: boolean; message: string } & CninfoIngestStatus>(
    '/knowledge/ingest/cninfo/start', body),

  stopCninfoIngest: () => api.post<{ ok: boolean; message: string } & CninfoIngestStatus>(
    '/knowledge/ingest/cninfo/stop', {}),

  getResearchReportIngestStatus: () =>
    api.get<ResearchReportIngestStatus>('/knowledge/ingest/research_report/status'),

  startResearchReportIngest: (body: {
    limit_per_symbol?: number; full_text?: boolean;
    start_date?: string; end_date?: string;
  } = {}) => api.post<{ ok: boolean; message: string } & ResearchReportIngestStatus>(
    '/knowledge/ingest/research_report/start', body),

  stopResearchReportIngest: () => api.post<{ ok: boolean; message: string } & ResearchReportIngestStatus>(
    '/knowledge/ingest/research_report/stop', {}),

  startArxivIngest: (body: {
    categories?: string[]; keywords?: string[];
    start_date?: string; end_date?: string; max_results?: number; full_text?: boolean;
  } = {}) => api.post<ArxivIngestResult>('/knowledge/ingest/arxiv', body, SLOW),

  /** 触发一次抓取，NDJSON 流式解析（协议同 dataMonitorService.streamUpdate） */
  streamScrape: async (
    body: { url: string; want?: string; endpoint?: string; params?: Record<string, any> },
    onEvent: (event: ScrapeStreamEvent) => void,
    signal?: AbortSignal,
  ): Promise<void> => {
    const response = await authFetch(`${API_BASE}/knowledge/scrape/stream`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
      signal,
    });
    if (!response.ok || !response.body) {
      throw new Error(`抓取请求失败: ${response.status}`);
    }
    const reader = response.body.getReader();
    const decoder = new TextDecoder();
    let buffer = '';
    while (true) {
      const { done, value } = await reader.read();
      if (done) break;
      buffer += decoder.decode(value, { stream: true });
      const lines = buffer.split('\n');
      buffer = lines.pop() || '';
      for (const line of lines) {
        if (!line.trim()) continue;
        try {
          onEvent(JSON.parse(line));
        } catch (e) {
          console.warn('解析抓取事件失败:', line, e);
        }
      }
    }
  },
};

import api from './api';

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
};

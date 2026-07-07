import React, { useCallback, useEffect, useState } from 'react';
import { Card } from '../common/Card';
import { Button } from '../common/Button';
import { knowledgeService } from '../../services/knowledgeService';
import type { KnowledgeDoc, SearchHit, AlphaIdea, KnowledgeStats } from '../../services/knowledgeService';
import { openExternal } from '../../utils/openExternal';

/* ──────────────────────────────────────────────────────────────
   知识库内嵌面板（原本没有独立页面，收敛进数据监控页常驻展示）
   三个手工 tab：文档列表 / 语义检索 / Alpha 想法。只读展示，
   摄入/提炼等慢任务本次不接入。
   ────────────────────────────────────────────────────────────── */

type Tab = 'documents' | 'search' | 'ideas';

const SOURCE_TYPES = [
  { value: '', label: '全部' },
  { value: 'paper', label: '论文' },
  { value: 'research', label: '研报' },
  { value: 'financial', label: '财报' },
  { value: 'news', label: '新闻' },
  { value: 'internal', label: '内部' },
];

const IDEA_STATUS = [
  { value: 'all', label: '全部' },
  { value: 'proposed', label: '待验证' },
  { value: 'backtesting', label: '回测中' },
  { value: 'validated', label: '已验证' },
  { value: 'rejected', label: '已否决' },
];

const IDEA_STATUS_STYLE: Record<string, string> = {
  proposed: 'bg-gray-500/15 text-gray-400',
  backtesting: 'bg-primary/15 text-primary',
  validated: 'bg-bear/15 text-bear',
  rejected: 'bg-bull/15 text-bull',
};

const fmtDate = (iso: string | null) => {
  if (!iso) return '-';
  try {
    return new Date(iso).toLocaleDateString('zh-CN');
  } catch {
    return iso;
  }
};

export const KnowledgePanel: React.FC<{ stats?: KnowledgeStats | null }> = ({ stats }) => {
  const [tab, setTab] = useState<Tab>('documents');

  // 文档
  const [docSourceType, setDocSourceType] = useState('');
  const [docs, setDocs] = useState<KnowledgeDoc[]>([]);
  const [docsLoading, setDocsLoading] = useState(false);

  // 检索
  const [query, setQuery] = useState('');
  const [searchResult, setSearchResult] = useState<{ summary: string; hits: SearchHit[] } | null>(null);
  const [searching, setSearching] = useState(false);

  // ideas
  const [ideaStatus, setIdeaStatus] = useState('all');
  const [ideas, setIdeas] = useState<AlphaIdea[]>([]);
  const [ideasLoading, setIdeasLoading] = useState(false);

  const loadDocs = useCallback(async () => {
    setDocsLoading(true);
    try {
      const list = await knowledgeService.getDocuments({ source_type: docSourceType || undefined, limit: 50 });
      setDocs(list);
    } catch {
      setDocs([]);
    } finally {
      setDocsLoading(false);
    }
  }, [docSourceType]);

  const loadIdeas = useCallback(async () => {
    setIdeasLoading(true);
    try {
      const list = await knowledgeService.getIdeas(ideaStatus);
      setIdeas(list);
    } catch {
      setIdeas([]);
    } finally {
      setIdeasLoading(false);
    }
  }, [ideaStatus]);

  useEffect(() => {
    if (tab === 'documents') loadDocs();
  }, [tab, loadDocs]);

  useEffect(() => {
    if (tab === 'ideas') loadIdeas();
  }, [tab, loadIdeas]);

  const runSearch = async () => {
    if (!query.trim()) return;
    setSearching(true);
    try {
      const res = await knowledgeService.search({ query: query.trim(), top_k: 8 });
      setSearchResult(res);
    } catch {
      setSearchResult({ summary: '检索失败', hits: [] });
    } finally {
      setSearching(false);
    }
  };

  return (
    <Card id="knowledge-panel" className="p-4">
      <div className="flex items-center justify-between mb-3">
        <span className="text-sm text-white font-medium">📚 知识库</span>
        {stats && (
          <span className="text-[11px] text-gray-500">
            {stats.documents} 篇文档 · {stats.chunks} 个知识块 · {stats.ideas} 条想法
          </span>
        )}
      </div>

      <div className="flex gap-1.5 mb-3">
        {([
          ['documents', '文档'],
          ['search', '语义检索'],
          ['ideas', 'Alpha 想法'],
        ] as [Tab, string][]).map(([key, label]) => (
          <button
            key={key}
            onClick={() => setTab(key)}
            className={`px-3 py-1 text-xs rounded-full border transition-colors ${
              tab === key
                ? 'border-primary/60 text-primary bg-primary/10'
                : 'border-border text-gray-400 hover:text-gray-200'
            }`}
          >
            {label}
          </button>
        ))}
      </div>

      {tab === 'documents' && (
        <div>
          <div className="flex flex-wrap gap-1.5 mb-2">
            {SOURCE_TYPES.map((s) => (
              <button
                key={s.value}
                onClick={() => setDocSourceType(s.value)}
                className={`px-2 py-0.5 text-[11px] rounded-full border transition-colors ${
                  docSourceType === s.value
                    ? 'border-primary/60 text-primary'
                    : 'border-border text-gray-500 hover:text-gray-300'
                }`}
              >
                {s.label}
              </button>
            ))}
          </div>
          {docsLoading ? (
            <div className="text-xs text-gray-500 py-2">加载中…</div>
          ) : docs.length === 0 ? (
            <div className="text-xs text-gray-500 py-2">暂无文档</div>
          ) : (
            <div className="max-h-72 overflow-y-auto space-y-1.5 pr-1">
              {docs.map((d) => (
                <div key={d.doc_id} className="flex items-center gap-2 text-xs border-b border-border/50 pb-1.5">
                  <span className="px-1.5 py-0.5 rounded-full bg-dark-light text-gray-400 text-[10px] shrink-0">
                    {d.source_type}
                  </span>
                  <a
                    href={d.url}
                    target="_blank"
                    rel="noreferrer"
                    onClick={(e) => { e.preventDefault(); openExternal(d.url); }}
                    className="flex-1 truncate text-gray-300 hover:text-primary-light cursor-pointer"
                    title={d.title}
                  >
                    {d.title}
                  </a>
                  <span className="text-gray-600 shrink-0">{d.chunk_count} 块</span>
                  <span className="text-gray-600 shrink-0">{fmtDate(d.fetched_at)}</span>
                </div>
              ))}
            </div>
          )}
        </div>
      )}

      {tab === 'search' && (
        <div>
          <div className="flex gap-2 mb-2">
            <input
              value={query}
              onChange={(e) => setQuery(e.target.value)}
              onKeyDown={(e) => e.key === 'Enter' && runSearch()}
              placeholder="语义检索问题或关键词…"
              className="flex-1 bg-dark-light border border-border rounded-lg px-3 py-1.5 text-xs text-gray-200 placeholder-gray-600 outline-none focus:border-primary/60"
            />
            <Button variant="subtle" size="sm" loading={searching} disabled={!query.trim()} onClick={runSearch}>
              检索
            </Button>
          </div>
          {searchResult && (
            <div>
              <div className="text-xs text-gray-400 mb-2">{searchResult.summary}</div>
              <div className="max-h-72 overflow-y-auto space-y-2 pr-1">
                {searchResult.hits.map((h, i) => (
                  <div key={i} className="text-xs border-b border-border/50 pb-2">
                    <div className="flex items-center gap-2 mb-1">
                      <span className="px-1.5 py-0.5 rounded-full bg-dark-light text-gray-400 text-[10px]">
                        {h.source_type}
                      </span>
                      <a href={h.url} target="_blank" rel="noreferrer" onClick={(e) => { e.preventDefault(); openExternal(h.url); }} className="truncate text-gray-300 hover:text-primary-light cursor-pointer">
                        {h.title}
                      </a>
                    </div>
                    <p className="text-gray-500 leading-snug line-clamp-2">{h.text}</p>
                  </div>
                ))}
              </div>
            </div>
          )}
        </div>
      )}

      {tab === 'ideas' && (
        <div>
          <div className="flex flex-wrap gap-1.5 mb-2">
            {IDEA_STATUS.map((s) => (
              <button
                key={s.value}
                onClick={() => setIdeaStatus(s.value)}
                className={`px-2 py-0.5 text-[11px] rounded-full border transition-colors ${
                  ideaStatus === s.value
                    ? 'border-primary/60 text-primary'
                    : 'border-border text-gray-500 hover:text-gray-300'
                }`}
              >
                {s.label}
              </button>
            ))}
          </div>
          {ideasLoading ? (
            <div className="text-xs text-gray-500 py-2">加载中…</div>
          ) : ideas.length === 0 ? (
            <div className="text-xs text-gray-500 py-2">暂无想法</div>
          ) : (
            <div className="max-h-72 overflow-y-auto space-y-2 pr-1">
              {ideas.map((idea) => (
                <div key={idea.idea_id} className="text-xs border-b border-border/50 pb-2">
                  <div className="flex items-center gap-2 mb-1">
                    <span className="text-gray-200 font-medium truncate flex-1">{idea.title}</span>
                    <span className={`px-1.5 py-0.5 rounded-full text-[10px] shrink-0 ${IDEA_STATUS_STYLE[idea.status] || ''}`}>
                      {idea.status}
                    </span>
                  </div>
                  <p className="text-gray-500 leading-snug line-clamp-2 mb-1">{idea.hypothesis}</p>
                  <div className="flex items-center gap-3 text-gray-600">
                    <span>目标：{idea.optimization_goal}</span>
                    {idea.best_composite_score != null && <span>得分 {idea.best_composite_score.toFixed(2)}</span>}
                  </div>
                </div>
              ))}
            </div>
          )}
        </div>
      )}
    </Card>
  );
};

export default KnowledgePanel;

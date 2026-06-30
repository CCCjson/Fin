import React, { useState, useEffect } from 'react';
import { knowledgeService } from '../services/knowledgeService';
import type {
  KnowledgeStats, KnowledgeDoc, AlphaIdea, SearchHit,
} from '../services/knowledgeService';

const SOURCE_LABEL: Record<string, string> = {
  paper: '论文', research: '研报', financial: '财报', internal: '内部', news: '新闻', all: '全部',
};

const STATUS_STYLE: Record<string, string> = {
  proposed: 'bg-yellow-500/20 text-yellow-300 border-yellow-500/40',
  backtesting: 'bg-blue-500/20 text-blue-300 border-blue-500/40',
  validated: 'bg-green-500/20 text-green-300 border-green-500/40',
  rejected: 'bg-red-500/20 text-red-300 border-red-500/40',
};

export const Knowledge: React.FC = () => {
  // stats
  const [stats, setStats] = useState<KnowledgeStats | null>(null);

  // documents
  const [docs, setDocs] = useState<KnowledgeDoc[]>([]);
  const [docSource, setDocSource] = useState('all');
  const [docLoading, setDocLoading] = useState(false);

  // search
  const [query, setQuery] = useState('');
  const [searchSource, setSearchSource] = useState('all');
  const [hits, setHits] = useState<SearchHit[]>([]);
  const [searching, setSearching] = useState(false);

  // ideas
  const [ideas, setIdeas] = useState<AlphaIdea[]>([]);
  const [ideaStatus, setIdeaStatus] = useState('all');
  const [mining, setMining] = useState(false);

  // ingest
  const [ingestQuery, setIngestQuery] = useState('quantitative momentum factor strategy');
  const [ingesting, setIngesting] = useState(false);

  const [error, setError] = useState('');
  const [msg, setMsg] = useState('');

  const errOf = (e: any) => e?.response?.data?.error || e?.message || '操作失败';

  const loadStats = () => { knowledgeService.getStats().then(setStats).catch(() => {}); };

  const loadDocs = (source = docSource) => {
    setDocLoading(true);
    knowledgeService.getDocuments({ source_type: source, limit: 100 })
      .then(setDocs).catch((e: any) => setError(errOf(e))).finally(() => setDocLoading(false));
  };

  const loadIdeas = (status = ideaStatus) => {
    knowledgeService.getIdeas(status).then(setIdeas).catch((e: any) => setError(errOf(e)));
  };

  useEffect(() => { loadStats(); loadDocs('all'); loadIdeas('all'); /* eslint-disable-next-line */ }, []);

  const runSearch = async () => {
    if (!query.trim()) return;
    setSearching(true); setError('');
    try {
      const r = await knowledgeService.search({ query, top_k: 6, source_type: searchSource });
      setHits(r.hits || []);
    } catch (e: any) { setError(errOf(e)); } finally { setSearching(false); }
  };

  const runIngest = async () => {
    const queries = ingestQuery.split(/[,，]/).map((s) => s.trim()).filter(Boolean);
    if (!queries.length) return;
    setIngesting(true); setError(''); setMsg('');
    try {
      const r = await knowledgeService.ingestPapers({ queries, max_docs: 5 });
      setMsg(`摄入完成：新增 ${r.ingested}，跳过 ${r.skipped}，失败 ${r.failed}`);
      loadStats(); loadDocs();
    } catch (e: any) { setError(errOf(e)); } finally { setIngesting(false); }
  };

  const runMine = async () => {
    setMining(true); setError(''); setMsg('');
    try {
      const r = await knowledgeService.mineIdeas({ limit: 5 });
      setMsg(`提炼完成：新增 ${r.mined} 个 alpha 思路（跳过 ${r.skipped}）`);
      loadStats(); loadIdeas();
    } catch (e: any) { setError(errOf(e)); } finally { setMining(false); }
  };

  const statCards = stats ? [
    { label: '文档', value: stats.documents },
    { label: '切片', value: stats.chunks },
    { label: '向量', value: stats.vectors },
    { label: 'Alpha 思路', value: stats.ideas },
  ] : [];

  return (
    <div className="p-4 md:p-6 max-w-6xl mx-auto">
      <div className="flex items-center justify-between flex-wrap gap-2 mb-4">
        <h1 className="text-xl font-bold text-white">📚 外置金融大脑 · 知识库</h1>
        <div className="text-xs text-gray-500">论文/研报/财报/内部数据的语义检索 + alpha 提炼</div>
      </div>

      {error && <div className="mb-3 p-3 rounded-lg bg-red-900/40 border border-red-600/40 text-red-200 text-sm">{error}</div>}
      {msg && <div className="mb-3 p-3 rounded-lg bg-green-900/30 border border-green-600/40 text-green-200 text-sm">{msg}</div>}

      {/* stats */}
      <div className="grid grid-cols-2 md:grid-cols-4 gap-3 mb-4">
        {statCards.map((c) => (
          <div key={c.label} className="bg-dark-card border border-border rounded-xl p-3">
            <div className="text-gray-500 text-xs">{c.label}</div>
            <div className="text-2xl font-bold text-white">{c.value}</div>
          </div>
        ))}
        {!stats && <div className="text-gray-500 text-sm col-span-4">加载统计中…</div>}
      </div>

      {/* 检索测试框 */}
      <div className="bg-dark-card border border-border rounded-xl p-4 mb-4">
        <div className="text-sm font-semibold text-gray-300 mb-2">🔍 语义检索</div>
        <div className="flex flex-wrap gap-2">
          <input
            className="flex-1 min-w-[200px] px-3 py-1.5 rounded-lg bg-dark-light border border-border text-white text-sm"
            placeholder="问点啥，如：动量因子 / 平安银行 ROE"
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            onKeyDown={(e) => { if (e.key === 'Enter') runSearch(); }}
          />
          <select className="px-3 py-1.5 rounded-lg bg-dark-light border border-border text-white text-sm"
            value={searchSource} onChange={(e) => setSearchSource(e.target.value)}>
            {['all', 'paper', 'research', 'financial', 'internal', 'news'].map((s) =>
              <option key={s} value={s}>{SOURCE_LABEL[s] || s}</option>)}
          </select>
          <button onClick={runSearch} disabled={searching}
            className="px-4 py-1.5 rounded-lg bg-primary text-dark font-semibold text-sm disabled:opacity-50">
            {searching ? '检索中…' : '检索'}
          </button>
        </div>
        {hits.length > 0 && (
          <div className="mt-3 space-y-2">
            {hits.map((h, i) => (
              <div key={h.chunk_id} className="bg-dark-light rounded-lg p-3">
                <div className="flex items-center gap-2 flex-wrap mb-1">
                  <span className="text-xs font-bold text-blue-300">[{i + 1}]</span>
                  {h.url
                    ? <a href={h.url} target="_blank" rel="noreferrer" className="text-sm text-gray-200 hover:text-primary font-medium">{h.title}</a>
                    : <span className="text-sm text-gray-200 font-medium">{h.title}</span>}
                  <span className="text-[10px] px-1.5 py-0.5 rounded bg-blue-500/15 text-blue-300 border border-blue-500/30">
                    {SOURCE_LABEL[h.source_type] || h.source_type}
                  </span>
                  <span className="text-[10px] text-gray-600">距离 {h.distance.toFixed(3)}</span>
                </div>
                <div className="text-xs text-gray-400 line-clamp-3">{h.text.slice(0, 240)}…</div>
              </div>
            ))}
          </div>
        )}
      </div>

      {/* Alpha Ideas 看板 */}
      <div className="bg-dark-card border border-border rounded-xl p-4 mb-4">
        <div className="flex items-center justify-between flex-wrap gap-2 mb-3">
          <div className="text-sm font-semibold text-gray-300">💡 Alpha 思路（从论文提炼）</div>
          <div className="flex items-center gap-2">
            <select className="px-2 py-1 rounded-lg bg-dark-light border border-border text-white text-xs"
              value={ideaStatus} onChange={(e) => { setIdeaStatus(e.target.value); loadIdeas(e.target.value); }}>
              {['all', 'proposed', 'backtesting', 'validated', 'rejected'].map((s) =>
                <option key={s} value={s}>{s}</option>)}
            </select>
            <button onClick={runMine} disabled={mining}
              className="px-3 py-1 rounded-lg bg-dark-light border border-border text-gray-200 text-xs disabled:opacity-50">
              {mining ? '提炼中…' : '提炼新思路'}
            </button>
          </div>
        </div>
        {ideas.length === 0
          ? <div className="text-gray-600 text-sm py-4 text-center">暂无 alpha 思路。先摄入论文，再点「提炼新思路」。</div>
          : (
            <div className="grid grid-cols-1 md:grid-cols-2 gap-3">
              {ideas.map((it) => (
                <div key={it.idea_id} className="bg-dark-light rounded-lg p-3 border border-border">
                  <div className="flex items-start justify-between gap-2 mb-1">
                    <span className="text-sm text-white font-semibold">{it.title}</span>
                    <span className={`text-[10px] px-1.5 py-0.5 rounded border shrink-0 ${STATUS_STYLE[it.status] || ''}`}>{it.status}</span>
                  </div>
                  <div className="text-xs text-gray-400 mb-1 line-clamp-2">{it.hypothesis}</div>
                  <div className="text-[11px] text-gray-500 line-clamp-2">🎯 {it.optimization_goal}</div>
                  {it.best_composite_score != null && (
                    <div className="text-[11px] text-green-300 mt-1">回测得分 {it.best_composite_score.toFixed(3)}</div>
                  )}
                </div>
              ))}
            </div>
          )}
        <div className="text-[10px] text-gray-600 mt-2">注：回测开销大，需在 Alpha Lab 显式发起，本页不直接触发。</div>
      </div>

      {/* 文档列表 + 摄入 */}
      <div className="bg-dark-card border border-border rounded-xl p-4">
        <div className="flex items-center justify-between flex-wrap gap-2 mb-3">
          <div className="text-sm font-semibold text-gray-300">📄 文档库</div>
          <div className="flex items-center gap-2 flex-wrap">
            <input
              className="px-3 py-1 rounded-lg bg-dark-light border border-border text-white text-xs min-w-[220px]"
              placeholder="摄入论文：搜索词，逗号分隔"
              value={ingestQuery} onChange={(e) => setIngestQuery(e.target.value)} />
            <button onClick={runIngest} disabled={ingesting}
              className="px-3 py-1 rounded-lg bg-primary text-dark font-semibold text-xs disabled:opacity-50">
              {ingesting ? '摄入中…（慢）' : '抓论文入库'}
            </button>
            <select className="px-2 py-1 rounded-lg bg-dark-light border border-border text-white text-xs"
              value={docSource} onChange={(e) => { setDocSource(e.target.value); loadDocs(e.target.value); }}>
              {['all', 'paper', 'research', 'financial', 'news'].map((s) =>
                <option key={s} value={s}>{SOURCE_LABEL[s] || s}</option>)}
            </select>
          </div>
        </div>
        <div className="overflow-x-auto">
          <table className="w-full text-sm">
            <thead className="bg-dark-light text-gray-400 text-xs">
              <tr>
                <th className="px-3 py-2 text-left">标题</th>
                <th className="px-3 py-2 text-left">来源</th>
                <th className="px-3 py-2 text-center">切片</th>
                <th className="px-3 py-2 text-center">状态</th>
              </tr>
            </thead>
            <tbody>
              {docLoading
                ? <tr><td colSpan={4} className="text-center text-gray-600 py-8">加载中…</td></tr>
                : docs.length === 0
                  ? <tr><td colSpan={4} className="text-center text-gray-600 py-8">暂无文档</td></tr>
                  : docs.map((d) => (
                    <tr key={d.doc_id} className="border-t border-border/50 hover:bg-dark-light/50">
                      <td className="px-3 py-2 text-gray-200 max-w-[360px] truncate">
                        {d.url ? <a href={d.url} target="_blank" rel="noreferrer" className="hover:text-primary">{d.title}</a> : d.title}
                      </td>
                      <td className="px-3 py-2 text-gray-400">{SOURCE_LABEL[d.source_type] || d.source_type}</td>
                      <td className="px-3 py-2 text-center text-gray-400">{d.chunk_count}</td>
                      <td className="px-3 py-2 text-center text-gray-400">{d.status}</td>
                    </tr>
                  ))}
            </tbody>
          </table>
        </div>
      </div>
    </div>
  );
};

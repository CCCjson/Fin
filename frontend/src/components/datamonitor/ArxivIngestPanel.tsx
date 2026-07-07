import React, { useState } from 'react';
import { Card } from '../common/Card';
import { Button } from '../common/Button';
import { knowledgeService } from '../../services/knowledgeService';
import type { ArxivIngestResult } from '../../services/knowledgeService';

/* ──────────────────────────────────────────────────────────────
   arXiv 论文摄入（数据监控页 · 知识库板块）
   官方API是分钟级同步任务（分页请求+3秒限速），不像研报/cninfo那样要跑几小时，
   所以不需要后台线程+进度轮询那一套，点一下按钮等结果即可。
   ────────────────────────────────────────────────────────────── */

const DEFAULT_CATEGORIES = 'q-fin.PM,q-fin.ST,q-fin.TR,q-fin.CP,q-fin.RM';
const DEFAULT_KEYWORDS = 'alpha factor,stock prediction,quantitative trading,factor investing,portfolio optimization';

export const ArxivIngestPanel: React.FC = () => {
  const [categories, setCategories] = useState(DEFAULT_CATEGORIES);
  const [keywords, setKeywords] = useState(DEFAULT_KEYWORDS);
  const [startDate, setStartDate] = useState('20220101');
  const [endDate, setEndDate] = useState('20261231');
  const [maxResults, setMaxResults] = useState(300);
  const [busy, setBusy] = useState(false);
  const [result, setResult] = useState<ArxivIngestResult | null>(null);
  const [error, setError] = useState<string | null>(null);

  const handleStart = async () => {
    setBusy(true);
    setError(null);
    try {
      const res = await knowledgeService.startArxivIngest({
        categories: categories.split(',').map(s => s.trim()).filter(Boolean),
        keywords: keywords.split(',').map(s => s.trim()).filter(Boolean),
        start_date: startDate, end_date: endDate, max_results: maxResults,
      });
      setResult(res);
    } catch (e: any) {
      setError(e?.message || '摄入失败');
    } finally {
      setBusy(false);
    }
  };

  return (
    <Card className="p-4">
      <div className="flex items-center justify-between mb-3">
        <span className="text-sm text-white font-medium">📚 arXiv 论文摄入（官方API）</span>
      </div>

      <div className="grid grid-cols-1 sm:grid-cols-2 gap-2 mb-2">
        <input
          type="text"
          value={categories}
          onChange={e => setCategories(e.target.value)}
          disabled={busy}
          placeholder="分类，逗号分隔，如 q-fin.PM,q-fin.ST"
          className="px-2 py-1 text-xs bg-dark-light text-gray-300 rounded border border-border focus:border-primary outline-none disabled:opacity-50"
        />
        <input
          type="text"
          value={keywords}
          onChange={e => setKeywords(e.target.value)}
          disabled={busy}
          placeholder="叠加关键词，逗号分隔（留空=只按分类）"
          className="px-2 py-1 text-xs bg-dark-light text-gray-300 rounded border border-border focus:border-primary outline-none disabled:opacity-50"
        />
      </div>

      <div className="flex flex-wrap items-center gap-2 mb-3">
        <input
          type="text"
          value={startDate}
          onChange={e => setStartDate(e.target.value)}
          disabled={busy}
          placeholder="起始日期 YYYYMMDD"
          className="w-28 px-2 py-1 text-xs bg-dark-light text-gray-300 rounded border border-border focus:border-primary outline-none disabled:opacity-50"
        />
        <span className="text-gray-600 text-xs">~</span>
        <input
          type="text"
          value={endDate}
          onChange={e => setEndDate(e.target.value)}
          disabled={busy}
          placeholder="截止日期 YYYYMMDD"
          className="w-28 px-2 py-1 text-xs bg-dark-light text-gray-300 rounded border border-border focus:border-primary outline-none disabled:opacity-50"
        />
        <input
          type="number"
          value={maxResults}
          onChange={e => setMaxResults(Number(e.target.value) || 300)}
          disabled={busy}
          placeholder="最多篇数"
          className="w-24 px-2 py-1 text-xs bg-dark-light text-gray-300 rounded border border-border focus:border-primary outline-none disabled:opacity-50"
        />
        <Button variant="subtle" size="sm" loading={busy} onClick={handleStart}>
          开始摄入
        </Button>
      </div>

      {error && <div className="text-[11px] text-bull mb-2">{error}</div>}

      {result && (
        <>
          <div className="grid grid-cols-3 gap-2 mb-2">
            <div className="bg-dark-light rounded-lg px-2 py-1.5">
              <div className="text-sm text-bear font-semibold">{result.ingested}</div>
              <div className="text-[10px] text-gray-500">新增文档</div>
            </div>
            <div className="bg-dark-light rounded-lg px-2 py-1.5">
              <div className="text-sm text-gray-300 font-semibold">{result.skipped}</div>
              <div className="text-[10px] text-gray-500">跳过(已存在)</div>
            </div>
            <div className="bg-dark-light rounded-lg px-2 py-1.5">
              <div className="text-sm text-bull font-semibold">{result.failed}</div>
              <div className="text-[10px] text-gray-500">失败</div>
            </div>
          </div>
          {result.docs.length > 0 && (
            <div className="max-h-40 overflow-y-auto space-y-1">
              {result.docs.map((d, i) => (
                <div key={`${d.arxiv_id}-${i}`} className="text-[11px] text-gray-400 truncate">
                  <span className="font-mono text-gray-600">{d.arxiv_id}</span> {d.title}
                </div>
              ))}
            </div>
          )}
        </>
      )}
    </Card>
  );
};

export default ArxivIngestPanel;

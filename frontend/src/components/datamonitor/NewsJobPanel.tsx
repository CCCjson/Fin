import React, { useCallback, useEffect, useRef, useState } from 'react';
import { Card } from '../common/Card';
import { Button } from '../common/Button';
import { newsService } from '../../services/newsService';
import type { NewsArticle, NewsJobStatus } from '../../services/newsService';
import { openExternal } from '../../utils/openExternal';

const ARTICLES_LIMIT = 50;

/* ──────────────────────────────────────────────────────────────
   Newnew 新闻定时任务监控（数据监控页）
   常驻后台任务：开关走 POST /news/job/toggle，进度靠轮询 GET /news/job/status
   （跟 CninfoIngestPanel/ScrapeMonitorPanel 同一套模式）。
   ────────────────────────────────────────────────────────────── */

const POLL_MS = 15000;

// 跟后端 news_engine/news_scheduler.py::_CATEGORY_LABEL 保持一致
const CATEGORY_LABEL: Record<string, string> = {
  financial_risk: '财务风险',
  urgent: '突发',
  geopolitical: '地缘政治',
};

const fmtTime = (s: string | null | undefined): string => {
  if (!s) return '—';
  try {
    return new Date(s).toLocaleString('zh-CN', { hour12: false });
  } catch {
    return s;
  }
};

// A股配色：bull=红=涨/利好，bear=绿=跌/利空（跟 tailwind.config.js 的涨跌色一致）
const SENTIMENT_DOT: Record<string, string> = {
  positive: 'bg-bull',
  negative: 'bg-bear',
  neutral: 'bg-gray-500',
};

const NewsRow: React.FC<{
  symbol: string | null;
  title: string;
  url?: string | null;
  source?: string | null;
  category?: string | null;
  sentiment?: 'positive' | 'negative' | 'neutral' | null;
}> = ({ symbol, title, url, source, category, sentiment }) => (
  <div className="flex items-center gap-2 px-1.5 py-1.5 rounded hover:bg-dark-light/60 transition-colors">
    <span
      className={`shrink-0 w-2 h-2 rounded-full ${sentiment ? SENTIMENT_DOT[sentiment] : 'bg-gray-700'}`}
      title={sentiment ? `情绪：${sentiment}` : '无情绪数据'}
    />
    <span className="shrink-0 font-mono text-[11px] text-gray-500 max-w-[64px] truncate">
      {symbol || '综合'}
    </span>
    {category && (
      <span className="shrink-0 px-1 rounded text-[11px] bg-amber-500/15 text-amber-400">
        {CATEGORY_LABEL[category] || category}
      </span>
    )}
    {url ? (
      <a
        href={url}
        target="_blank"
        rel="noreferrer"
        title={title}
        onClick={(e) => { e.preventDefault(); openExternal(url); }}
        className="flex-1 min-w-0 truncate text-[13px] text-gray-300 underline decoration-dotted decoration-gray-600 hover:text-white hover:decoration-gray-400 cursor-pointer"
      >
        {title}
      </a>
    ) : (
      <span
        title={`${title}（无原文链接）`}
        className="flex-1 min-w-0 truncate text-[13px] text-gray-600 cursor-default"
      >
        {title}
      </span>
    )}
    {source && <span className="shrink-0 max-w-[88px] truncate text-[11px] text-gray-600">{source}</span>}
  </div>
);

export const NewsJobPanel: React.FC = () => {
  const [status, setStatus] = useState<NewsJobStatus | null>(null);
  const [busy, setBusy] = useState(false);
  const intervalRef = useRef<ReturnType<typeof setInterval> | undefined>(undefined);

  // 新闻标题列表（详情展开）：只要标题+链接，不展示摘要/正文
  const [articlesOpen, setArticlesOpen] = useState(false);
  const [articles, setArticles] = useState<NewsArticle[]>([]);
  const [articlesLoading, setArticlesLoading] = useState(false);

  const poll = useCallback(async () => {
    try {
      const s = await newsService.getJobStatus();
      setStatus(s);
    } catch {
      /* 静默失败，不打断轮询 */
    }
  }, []);

  useEffect(() => {
    poll();
    intervalRef.current = setInterval(poll, POLL_MS);
    return () => {
      if (intervalRef.current) clearInterval(intervalRef.current);
    };
  }, [poll]);

  const handleToggle = async () => {
    if (!status) return;
    setBusy(true);
    try {
      const s = await newsService.toggleJob(!status.enabled);
      setStatus(s);
    } finally {
      setBusy(false);
    }
  };

  const handleRunOnce = async () => {
    setBusy(true);
    try {
      await newsService.runJobOnce();
      await poll();
    } finally {
      setBusy(false);
    }
  };

  const loadArticles = useCallback(async () => {
    setArticlesLoading(true);
    try {
      const { articles: rows } = await newsService.getArticles({
        limit: ARTICLES_LIMIT,
        sort: 'importance',
      });
      setArticles(rows);
    } catch {
      /* 静默失败，列表保持原状 */
    } finally {
      setArticlesLoading(false);
    }
  }, []);

  const toggleArticles = () => {
    const next = !articlesOpen;
    setArticlesOpen(next);
    if (next && articles.length === 0) {
      loadArticles();
    }
  };

  const lastRun = status?.last_run;
  const newSymbolCount = lastRun?.new_symbol ? Object.keys(lastRun.new_symbol).length : 0;
  const highImpactCount = lastRun?.high_impact?.length ?? 0;

  return (
    <Card className="p-4">
      <div className="flex items-center justify-between mb-3">
        <span className="text-sm text-white font-medium">📰 Newnew 新闻定时任务</span>
        {status && (
          <span
            className={`px-1.5 py-0.5 rounded-full text-[11px] ${
              status.enabled ? 'bg-primary/15 text-primary' : 'bg-gray-500/15 text-gray-400'
            }`}
          >
            {status.running ? (status.enabled ? '运行中·开' : '运行中·关') : '未启动'}
          </span>
        )}
      </div>

      <div className="flex items-center gap-2 mb-3">
        <Button variant="subtle" size="sm" loading={busy} onClick={handleToggle}>
          {status?.enabled ? '关闭自动抓取' : '开启自动抓取'}
        </Button>
        <Button variant="subtle" size="sm" loading={busy} onClick={handleRunOnce}>
          立即跑一轮
        </Button>
        <Button variant="subtle" size="sm" loading={articlesLoading} onClick={toggleArticles}>
          {articlesOpen ? '收起新闻列表' : '查看新闻列表'}
        </Button>
        <span className="text-[13px] text-gray-500">
          每 {status?.interval_minutes ?? '—'} 分钟 · 下次运行 {fmtTime(status?.next_run)}
        </span>
      </div>

      <div className="grid grid-cols-3 sm:grid-cols-4 gap-2 mb-3">
        <div className="bg-dark-light rounded-lg px-2 py-1.5">
          <div className="text-sm text-bear font-semibold">{lastRun?.new_general ?? 0}</div>
          <div className="text-[11px] text-gray-500">综合新增</div>
        </div>
        <div className="bg-dark-light rounded-lg px-2 py-1.5">
          <div className="text-sm text-gray-300 font-semibold">{newSymbolCount}</div>
          <div className="text-[11px] text-gray-500">个股有新消息</div>
        </div>
        <div className="bg-dark-light rounded-lg px-2 py-1.5">
          <div className={`text-sm font-semibold ${highImpactCount > 0 ? 'text-bull' : 'text-gray-300'}`}>
            {highImpactCount}
          </div>
          <div className="text-[11px] text-gray-500">高影响预警</div>
        </div>
        <div className="bg-dark-light rounded-lg px-2 py-1.5">
          <div className="text-sm text-gray-300 font-semibold">{status?.cache_size ?? 0}</div>
          <div className="text-[11px] text-gray-500">去重缓存条数</div>
        </div>
      </div>

      {lastRun?.completed_at && (
        <div className="text-[13px] text-gray-500 mb-2">
          上轮运行: {lastRun.ok === false ? '有步骤失败' : '正常'} · {fmtTime(lastRun.completed_at)}
        </div>
      )}

      {highImpactCount > 0 && lastRun?.high_impact && (
        <div className="max-h-32 overflow-y-auto space-y-0.5 mb-1">
          {lastRun.high_impact.map((h, i) => (
            <NewsRow
              key={`${h.symbol ?? h.category ?? 'general'}-${i}`}
              symbol={h.symbol}
              title={h.title}
              url={h.url}
              category={h.category}
            />
          ))}
        </div>
      )}

      {articlesOpen && (
        <div className="mt-3 pt-3 border-t border-dark-light">
          <div className="text-[13px] text-gray-500 mb-1.5">
            最近 {articles.length} 条新闻 · 按重要度排序（点标题跳原文，不展示摘要）
          </div>
          <div className="max-h-64 overflow-y-auto space-y-0.5">
            {articles.length === 0 && !articlesLoading && (
              <div className="text-[13px] text-gray-500">暂无新闻</div>
            )}
            {articles.map((a) => (
              <NewsRow
                key={a.article_id}
                symbol={a.symbol}
                title={a.title}
                url={a.url}
                source={a.source}
                category={a.category}
                sentiment={a.sentiment?.sentiment}
              />
            ))}
          </div>
        </div>
      )}
    </Card>
  );
};

export default NewsJobPanel;

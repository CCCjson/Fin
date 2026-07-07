import React, { useCallback, useEffect, useRef, useState } from 'react';
import { Card } from '../common/Card';
import { Button } from '../common/Button';
import { knowledgeService } from '../../services/knowledgeService';
import type { ScrapeStreamEvent, ScrapeSession } from '../../services/knowledgeService';

/* ──────────────────────────────────────────────────────────────
   抓取任务独立监控区（数据监控页专用，与知识库面板物理分开）
   顶部：手动测试抓取小表单，实时展示当前这次抓取的 stage + sessionID
   下方：轮询列表，展示所有抓取会话（含 MoneyBill 聊天里触发的）
   ────────────────────────────────────────────────────────────── */

const STAGE_LABEL: Record<string, string> = {
  pending: '等待中',
  browser_launch: '启动浏览器',
  goto: '打开页面',
  sniffing: '嗅探接口',
  endpoints_found: '发现接口',
  config_saved: '已存配置',
  resolved: '解析目标',
  fetching: '拉取数据',
  retry_403: '刷新凭证重试',
  done: '完成',
};

const STATUS_STYLE: Record<string, string> = {
  running: 'bg-primary/15 text-primary',
  done: 'bg-bear/15 text-bear',
  error: 'bg-bull/15 text-bull',
};

const STATUS_LABEL: Record<string, string> = {
  running: '运行中',
  done: '完成',
  error: '失败',
};

const fmtShortId = (id: string) => (id.length > 14 ? `${id.slice(0, 14)}…` : id);

const fmtElapsed = (createdAt: number, updatedAt: number) => {
  const s = Math.max(0, updatedAt - createdAt);
  return s < 60 ? `${s.toFixed(1)}s` : `${Math.floor(s / 60)}分${Math.round(s % 60)}秒`;
};

export const ScrapeMonitorPanel: React.FC = () => {
  const [url, setUrl] = useState('');
  const [want, setWant] = useState('');
  const [running, setRunning] = useState(false);
  const [currentSessionId, setCurrentSessionId] = useState<string | null>(null);
  const [currentStage, setCurrentStage] = useState<string>('');
  const [currentError, setCurrentError] = useState<string | null>(null);

  const [sessions, setSessions] = useState<ScrapeSession[]>([]);
  const intervalRef = useRef<ReturnType<typeof setInterval> | undefined>(undefined);
  const abortRef = useRef<AbortController | null>(null);

  const fetchSessions = useCallback(async () => {
    try {
      const list = await knowledgeService.listScrapeSessions(50);
      setSessions(list);
    } catch {
      /* 静默失败，不打断轮询 */
    }
  }, []);

  useEffect(() => {
    fetchSessions();
    intervalRef.current = setInterval(fetchSessions, 4000);
    return () => {
      if (intervalRef.current) clearInterval(intervalRef.current);
      abortRef.current?.abort();
    };
  }, [fetchSessions]);

  const onEvent = useCallback((ev: ScrapeStreamEvent) => {
    if (ev.event === 'session_created') {
      setCurrentSessionId(ev.session_id);
      setCurrentStage('pending');
    } else if (ev.event === 'progress') {
      setCurrentStage(ev.stage || '');
    } else if (ev.event === 'done') {
      setCurrentStage('done');
      setRunning(false);
      fetchSessions();
    } else if (ev.event === 'error') {
      setCurrentError(ev.message || '抓取失败');
      setRunning(false);
      fetchSessions();
    }
  }, [fetchSessions]);

  const startScrape = async () => {
    if (!url.trim() || running) return;
    setRunning(true);
    setCurrentError(null);
    setCurrentSessionId(null);
    setCurrentStage('');
    const ac = new AbortController();
    abortRef.current = ac;
    try {
      await knowledgeService.streamScrape({ url: url.trim(), want: want.trim() || undefined }, onEvent, ac.signal);
    } catch (e: any) {
      setCurrentError(e?.message || '抓取请求失败');
      setRunning(false);
    }
  };

  return (
    <Card className="p-4">
      <div className="flex items-center justify-between mb-3">
        <span className="text-sm text-white font-medium">🕸️ 抓取任务监控</span>
        {currentSessionId && (
          <span className="text-[11px] font-mono text-gray-500" title={currentSessionId}>
            {running ? '抓取中' : currentError ? '出错' : '已完成'} · {fmtShortId(currentSessionId)}
          </span>
        )}
      </div>

      {/* 手动测试抓取 */}
      <div className="flex flex-wrap items-center gap-2 mb-3">
        <input
          value={url}
          onChange={(e) => setUrl(e.target.value)}
          placeholder="网页 URL，如 https://xueqiu.com/S/SH600519"
          className="flex-1 min-w-[220px] bg-dark-light border border-border rounded-lg px-3 py-1.5 text-xs text-gray-200 placeholder-gray-600 outline-none focus:border-primary/60"
        />
        <input
          value={want}
          onChange={(e) => setWant(e.target.value)}
          placeholder="想要什么数据（可选）"
          className="w-40 bg-dark-light border border-border rounded-lg px-3 py-1.5 text-xs text-gray-200 placeholder-gray-600 outline-none focus:border-primary/60"
        />
        <Button variant="subtle" size="sm" loading={running} disabled={!url.trim()} onClick={startScrape}>
          抓取
        </Button>
      </div>

      {(running || currentStage || currentError) && (
        <div className="text-xs text-gray-400 mb-3">
          {currentError ? (
            <span className="text-bull">❌ {currentError}</span>
          ) : (
            <span>{running ? '⏳' : '✅'} {STAGE_LABEL[currentStage] || currentStage || '准备中…'}</span>
          )}
        </div>
      )}

      {/* 会话列表（含聊天里触发的） */}
      {sessions.length === 0 ? (
        <div className="text-xs text-gray-500 py-2">暂无抓取记录</div>
      ) : (
        <div className="overflow-x-auto">
          <table className="w-full text-xs">
            <thead>
              <tr className="text-gray-500 text-left">
                <th className="font-normal pb-1.5 pr-3">会话</th>
                <th className="font-normal pb-1.5 pr-3">域名 / URL</th>
                <th className="font-normal pb-1.5 pr-3">阶段</th>
                <th className="font-normal pb-1.5 pr-3">状态</th>
                <th className="font-normal pb-1.5">耗时</th>
              </tr>
            </thead>
            <tbody>
              {sessions.map((s) => (
                <tr key={s.session_id} className="border-t border-border/60 text-gray-300">
                  <td
                    className="py-1.5 pr-3 font-mono text-gray-500 cursor-pointer hover:text-gray-300"
                    title={`点击复制：${s.session_id}`}
                    onClick={() => navigator.clipboard?.writeText(s.session_id)}
                  >
                    {fmtShortId(s.session_id)}
                  </td>
                  <td className="py-1.5 pr-3 truncate max-w-[220px]">{s.domain || s.url || '-'}</td>
                  <td className="py-1.5 pr-3">{STAGE_LABEL[s.stage] || s.stage}</td>
                  <td className="py-1.5 pr-3">
                    <span className={`px-1.5 py-0.5 rounded-full text-[10px] ${STATUS_STYLE[s.status] || ''}`}>
                      {STATUS_LABEL[s.status] || s.status}
                    </span>
                  </td>
                  <td className="py-1.5">{fmtElapsed(s.created_at, s.updated_at)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </Card>
  );
};

export default ScrapeMonitorPanel;

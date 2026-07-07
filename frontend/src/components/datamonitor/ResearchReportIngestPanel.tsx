import React, { useCallback, useEffect, useRef, useState } from 'react';
import { Card } from '../common/Card';
import { Button } from '../common/Button';
import { knowledgeService } from '../../services/knowledgeService';
import type { ResearchReportIngestStatus } from '../../services/knowledgeService';

/* ──────────────────────────────────────────────────────────────
   全市场东财研报批量摄入监控（数据监控页 · 知识库板块）
   常驻后台任务：起停走 POST，进度靠轮询 GET /knowledge/ingest/research_report/status
   （跟 CninfoIngestPanel 同一套模式）。

   full_text 一旦开始摄入即固化到 doc_id（native_id=PDF链接），同批次股票没法
   免费切换到另一种模式——所以参数表单只在未跑之前可编辑，开始后禁用并给出提示。
   ────────────────────────────────────────────────────────────── */

const STATUS_LABEL: Record<string, string> = {
  idle: '空闲', running: '运行中', stopping: '停止中', stopped: '已停止', done: '已跑完',
};

const STATUS_STYLE: Record<string, string> = {
  idle: 'bg-gray-500/15 text-gray-400',
  running: 'bg-primary/15 text-primary',
  stopping: 'bg-yellow-500/15 text-yellow-400',
  stopped: 'bg-gray-500/15 text-gray-400',
  done: 'bg-bear/15 text-bear',
};

const fmtEta = (s: number | null): string => {
  if (s === null || s === undefined) return '计算中…';
  if (s < 60) return `${Math.round(s)} 秒`;
  if (s < 3600) return `${Math.round(s / 60)} 分钟`;
  return `${(s / 3600).toFixed(1)} 小时`;
};

const POLL_MS = 3000;
// 连续几次轮询失败才判定「后端不可达」（避免单次网络抖动就误报）。
const OFFLINE_AFTER_FAILS = 2;

export const ResearchReportIngestPanel: React.FC = () => {
  const [status, setStatus] = useState<ResearchReportIngestStatus | null>(null);
  const [busy, setBusy] = useState(false);
  const [offline, setOffline] = useState(false);
  const [fullText, setFullText] = useState(true);
  const [startDate, setStartDate] = useState('20220101');
  const [endDate, setEndDate] = useState('20261231');
  const intervalRef = useRef<ReturnType<typeof setInterval> | undefined>(undefined);
  const failCountRef = useRef(0);

  const poll = useCallback(async () => {
    try {
      const s = await knowledgeService.getResearchReportIngestStatus();
      failCountRef.current = 0;
      setOffline(false);
      setStatus(s);
    } catch {
      // 之前这里完全静默——后端不可达时面板会冻结在最后一次拿到的状态，
      // 用户看不出「后端挂了」和「就是这个状态」的区别。现在计数上报，
      // 不再默默吞掉：offline 后 status 里的「运行中/停止中」不能再当真。
      failCountRef.current += 1;
      if (failCountRef.current >= OFFLINE_AFTER_FAILS) setOffline(true);
    }
  }, []);

  useEffect(() => {
    poll();
    intervalRef.current = setInterval(poll, POLL_MS);
    return () => {
      if (intervalRef.current) clearInterval(intervalRef.current);
    };
  }, [poll]);

  const handleStart = async () => {
    setBusy(true);
    try {
      await knowledgeService.startResearchReportIngest({
        full_text: fullText, start_date: startDate, end_date: endDate,
      });
      await poll();
    } finally {
      setBusy(false);
    }
  };

  const handleStop = async () => {
    setBusy(true);
    try {
      await knowledgeService.stopResearchReportIngest();
      await poll();
    } finally {
      setBusy(false);
    }
  };

  // offline 时最后一次拿到的 status 已经不可信，不能再拿它锁死「开始」按钮——
  // 否则一旦后端重启慢或网络抖动，用户会看着一个无法交互的「运行中/停止中」干等。
  const isRunning = !offline && (status?.status === 'running' || status?.status === 'stopping');
  const hasStarted = !!status && status.done_total > 0;
  const pct = status && status.total_all > 0 ? (status.done_total / status.total_all) * 100 : 0;

  return (
    <Card className="p-4">
      <div className="flex items-center justify-between mb-3">
        <span className="text-sm text-white font-medium">📑 全市场东财研报批量摄入</span>
        {offline ? (
          <span className="px-1.5 py-0.5 rounded-full text-[10px] bg-yellow-500/15 text-yellow-400">
            后端未连接，重试中…
          </span>
        ) : status && (
          <span className={`px-1.5 py-0.5 rounded-full text-[10px] ${STATUS_STYLE[status.status] || ''}`}>
            {STATUS_LABEL[status.status] || status.status}
          </span>
        )}
      </div>

      <div className="flex flex-wrap items-center gap-2 mb-2">
        <Button variant="subtle" size="sm" loading={busy} disabled={isRunning} onClick={handleStart}>
          {hasStarted ? '继续摄入' : '开始摄入'}
        </Button>
        <Button variant="subtle" size="sm" loading={busy} disabled={!isRunning} onClick={handleStop}>
          停止
        </Button>
        <label className="flex items-center gap-1 text-[11px] text-gray-400">
          <input
            type="checkbox"
            checked={fullText}
            disabled={isRunning || hasStarted}
            onChange={e => setFullText(e.target.checked)}
          />
          抓全文PDF
        </label>
        <input
          type="text"
          value={startDate}
          onChange={e => setStartDate(e.target.value)}
          disabled={isRunning || hasStarted}
          placeholder="起始日期 YYYYMMDD"
          className="w-28 px-2 py-1 text-xs bg-dark-light text-gray-300 rounded border border-border focus:border-primary outline-none disabled:opacity-50"
        />
        <span className="text-gray-600 text-xs">~</span>
        <input
          type="text"
          value={endDate}
          onChange={e => setEndDate(e.target.value)}
          disabled={isRunning || hasStarted}
          placeholder="截止日期 YYYYMMDD"
          className="w-28 px-2 py-1 text-xs bg-dark-light text-gray-300 rounded border border-border focus:border-primary outline-none disabled:opacity-50"
        />
      </div>
      <div className="text-[10px] text-yellow-500/80 mb-3">
        ⚠️ 全文/元数据模式一旦开始摄入，同批股票后续无法免费切换（doc_id 已固化），请确认后再开始。
      </div>

      {status && status.total_all > 0 && (
        <>
          <div className="w-full h-2.5 bg-dark-light rounded-full overflow-hidden mb-1">
            <div
              className="h-full bg-primary transition-all"
              style={{ width: `${Math.min(100, pct)}%` }}
            />
          </div>
          <div className="flex items-center justify-between text-[11px] text-gray-500 mb-3">
            <span>{pct.toFixed(1)}%（{status.done_total} / {status.total_all}）</span>
            <span>
              {status.current_symbol ? `正在处理: ${status.current_symbol}` : isRunning ? '处理中…' : '空闲'}
            </span>
          </div>

          <div className="grid grid-cols-3 sm:grid-cols-5 gap-2 mb-3">
            <div className="bg-dark-light rounded-lg px-2 py-1.5">
              <div className="text-sm text-bear font-semibold">{status.ingested}</div>
              <div className="text-[10px] text-gray-500">新增文档</div>
            </div>
            <div className="bg-dark-light rounded-lg px-2 py-1.5">
              <div className="text-sm text-gray-300 font-semibold">{status.skipped}</div>
              <div className="text-[10px] text-gray-500">跳过(已存在)</div>
            </div>
            <div className="bg-dark-light rounded-lg px-2 py-1.5">
              <div className="text-sm text-bull font-semibold">{status.failed_symbols}</div>
              <div className="text-[10px] text-gray-500">整只失败</div>
            </div>
            <div className="bg-dark-light rounded-lg px-2 py-1.5">
              <div className="text-sm text-gray-300 font-semibold">{status.rate_per_min}</div>
              <div className="text-[10px] text-gray-500">只/分钟</div>
            </div>
            <div className="bg-dark-light rounded-lg px-2 py-1.5 col-span-1">
              <div className="text-sm text-gray-300 font-semibold">{fmtEta(status.eta_seconds)}</div>
              <div className="text-[10px] text-gray-500">预计剩余</div>
            </div>
          </div>

          {status.recent.length > 0 && (
            <div className="max-h-40 overflow-y-auto space-y-1">
              {status.recent.map((r, i) => (
                <div key={`${r.symbol}-${i}`} className="flex items-center gap-2 text-[11px] text-gray-400">
                  <span className="text-gray-600 shrink-0">{r.at}</span>
                  <span className="shrink-0 font-mono">{r.symbol}</span>
                  <span className={r.status === 'ok' ? 'text-bear' : 'text-bull'}>
                    {r.status === 'ok' ? `新增${r.ingested}/跳过${r.skipped}` : '失败'}
                  </span>
                </div>
              ))}
            </div>
          )}
        </>
      )}
    </Card>
  );
};

export default ResearchReportIngestPanel;

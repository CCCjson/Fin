import React, { useCallback, useEffect, useRef, useState } from 'react';
import { Card } from '../common/Card';
import { Button } from '../common/Button';
import { deepHistoryService } from '../../services/deepHistoryService';
import type { AShareDeepHistoryStatus } from '../../services/deepHistoryService';

/* ──────────────────────────────────────────────────────────────
   A股深历史日线回补监控（数据监控页）
   常驻后台任务：起停走 POST，进度靠轮询 GET /deep-history/a-share/status
   （跟 CninfoIngestPanel 同一套模式）。
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

// 输入框内容解析：纯数字 -> limit；含逗号/字母 -> 显式代码列表；空 -> 全量
function parseTestInput(raw: string): { symbols?: string[]; limit?: number } {
  const v = raw.trim();
  if (!v) return {};
  if (/^\d+$/.test(v)) return { limit: parseInt(v, 10) };
  return { symbols: v.split(',').map(s => s.trim()).filter(Boolean) };
}

const POLL_MS = 3000;
// 连续几次轮询失败才判定「后端不可达」（避免单次网络抖动就误报）。
const OFFLINE_AFTER_FAILS = 2;

interface Props {
  /** true 时不渲染外层 Card（给合并面板 DeepHistoryPanel 内嵌用） */
  bare?: boolean;
}

export const AShareDeepHistoryPanel: React.FC<Props> = ({ bare }) => {
  const [status, setStatus] = useState<AShareDeepHistoryStatus | null>(null);
  const [busy, setBusy] = useState(false);
  const [offline, setOffline] = useState(false);
  const [testInput, setTestInput] = useState('');
  const intervalRef = useRef<ReturnType<typeof setInterval> | undefined>(undefined);
  const failCountRef = useRef(0);

  const poll = useCallback(async () => {
    try {
      const s = await deepHistoryService.getAShareStatus();
      failCountRef.current = 0;
      setOffline(false);
      setStatus(s);
    } catch {
      // 后端不可达时不再静默冻结——计数上报，offline 后 status 不可信。
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
      await deepHistoryService.startAShare(parseTestInput(testInput));
      await poll();
    } finally {
      setBusy(false);
    }
  };

  const handleStop = async () => {
    setBusy(true);
    try {
      await deepHistoryService.stopAShare();
      await poll();
    } finally {
      setBusy(false);
    }
  };

  const isRunning = !offline && (status?.status === 'running' || status?.status === 'stopping');
  const pct = status && status.total_all > 0 ? (status.done_total / status.total_all) * 100 : 0;

  const Wrapper = bare ? 'div' : Card;
  const wrapperProps = bare ? {} : { className: 'p-4' };

  return (
    <Wrapper {...wrapperProps}>
      <div className="flex items-center justify-between mb-3">
        <span className="text-sm text-white font-medium">📈 A股深历史日线回补（补到 1990-01-01）</span>
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

      <div className="flex flex-wrap items-center gap-2 mb-3">
        <Button variant="subtle" size="sm" loading={busy} disabled={isRunning} onClick={handleStart}>
          {status && status.done_total > 0 ? '继续回补' : '开始回补'}
        </Button>
        <Button variant="subtle" size="sm" loading={busy} disabled={!isRunning} onClick={handleStop}>
          停止
        </Button>
        <input
          type="text"
          value={testInput}
          onChange={e => setTestInput(e.target.value)}
          disabled={isRunning}
          placeholder="测试模式：代码(逗号分隔)或数量，留空=全量"
          className="flex-1 min-w-[220px] px-2 py-1 text-xs bg-dark-light text-gray-300 rounded border border-border focus:border-primary outline-none disabled:opacity-50"
        />
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
              <div className="text-sm text-bear font-semibold">{status.success}</div>
              <div className="text-[10px] text-gray-500">成功</div>
            </div>
            <div className="bg-dark-light rounded-lg px-2 py-1.5">
              <div className="text-sm text-bull font-semibold">{status.failed}</div>
              <div className="text-[10px] text-gray-500">失败(待重试)</div>
            </div>
            <div className="bg-dark-light rounded-lg px-2 py-1.5">
              <div className="text-sm text-gray-300 font-semibold">{status.new_records.toLocaleString()}</div>
              <div className="text-[10px] text-gray-500">新增行数</div>
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
                  <span className="shrink-0 text-gray-500">{r.name}</span>
                  <span className={r.status === 'ok' ? 'text-bear' : 'text-bull'}>
                    {r.status === 'ok' ? `+${r.rows}行` : '失败'}
                  </span>
                </div>
              ))}
            </div>
          )}
        </>
      )}
    </Wrapper>
  );
};

export default AShareDeepHistoryPanel;

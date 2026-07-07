import React, { useCallback, useEffect, useRef, useState } from 'react';
import { Card } from '../common/Card';
import { Button } from '../common/Button';
import { deepHistoryService } from '../../services/deepHistoryService';
import type { OverseasDeepHistoryStatus } from '../../services/deepHistoryService';

/* ──────────────────────────────────────────────────────────────
   港股/美股深历史日线回补监控（数据监控页）
   后端是同一个单例任务，同一时刻只能跑一个 market（避免 Yahoo 限速叠加）。
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

export const OverseasDeepHistoryPanel: React.FC<Props> = ({ bare }) => {
  const [market, setMarket] = useState<'hk_stock' | 'us_stock'>('hk_stock');
  const [status, setStatus] = useState<OverseasDeepHistoryStatus | null>(null);
  const [busy, setBusy] = useState(false);
  const [offline, setOffline] = useState(false);
  const [testInput, setTestInput] = useState('');
  const [batchSize, setBatchSize] = useState(50);
  const intervalRef = useRef<ReturnType<typeof setInterval> | undefined>(undefined);
  const failCountRef = useRef(0);

  const poll = useCallback(async () => {
    try {
      const s = await deepHistoryService.getOverseasStatus();
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

  const isRunning = !offline && (status?.status === 'running' || status?.status === 'stopping');
  // 面板打开时如果任务正跑着，市场切换跟着当前实际在跑的走（后端同一时刻只能跑一个）
  useEffect(() => {
    if (status?.market && isRunning) setMarket(status.market);
  }, [status?.market, isRunning]);

  const handleStart = async () => {
    setBusy(true);
    try {
      await deepHistoryService.startOverseas({
        market, batch_size: batchSize, ...parseTestInput(testInput),
      });
      await poll();
    } finally {
      setBusy(false);
    }
  };

  const handleStop = async () => {
    setBusy(true);
    try {
      await deepHistoryService.stopOverseas();
      await poll();
    } finally {
      setBusy(false);
    }
  };

  const pct = status && status.total_all > 0 ? (status.done_total / status.total_all) * 100 : 0;
  const showingOtherMarket = status?.market && status.market !== market;

  const Wrapper = bare ? 'div' : Card;
  const wrapperProps = bare ? {} : { className: 'p-4' };

  return (
    <Wrapper {...wrapperProps}>
      <div className="flex items-center justify-between mb-3">
        <span className="text-sm text-white font-medium">🌏 港股/美股深历史日线回补（0 到有）</span>
        {offline ? (
          <span className="px-1.5 py-0.5 rounded-full text-[10px] bg-yellow-500/15 text-yellow-400">
            后端未连接，重试中…
          </span>
        ) : status && (
          <span className={`px-1.5 py-0.5 rounded-full text-[10px] ${STATUS_STYLE[status.status] || ''}`}>
            {STATUS_LABEL[status.status] || status.status}{status.market ? ` · ${status.market === 'hk_stock' ? '港股' : '美股'}` : ''}
          </span>
        )}
      </div>

      <div className="flex items-center gap-1.5 mb-3">
        <button
          onClick={() => setMarket('hk_stock')}
          disabled={isRunning}
          className={`px-2.5 py-1 text-xs rounded-lg transition-colors disabled:opacity-50 ${market === 'hk_stock' ? 'bg-primary/15 text-primary' : 'bg-dark-light text-gray-400'}`}
        >港股（不过滤，全量）</button>
        <button
          onClick={() => setMarket('us_stock')}
          disabled={isRunning}
          className={`px-2.5 py-1 text-xs rounded-lg transition-colors disabled:opacity-50 ${market === 'us_stock' ? 'bg-primary/15 text-primary' : 'bg-dark-light text-gray-400'}`}
        >美股（已过滤债券/杠杆ETF）</button>
      </div>

      <div className="flex flex-wrap items-center gap-2 mb-3">
        <Button variant="subtle" size="sm" loading={busy} disabled={isRunning} onClick={handleStart}>
          {status && status.done_total > 0 && status.market === market ? '继续回补' : '开始回补'}
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
          className="flex-1 min-w-[200px] px-2 py-1 text-xs bg-dark-light text-gray-300 rounded border border-border focus:border-primary outline-none disabled:opacity-50"
        />
        <input
          type="number"
          value={batchSize}
          onChange={e => setBatchSize(Math.max(1, parseInt(e.target.value, 10) || 50))}
          disabled={isRunning}
          title="每批股票数"
          className="w-20 px-2 py-1 text-xs bg-dark-light text-gray-300 rounded border border-border focus:border-primary outline-none disabled:opacity-50"
        />
      </div>

      {status && status.market === 'us_stock' && Object.keys(status.excluded).length > 0 && (
        <div className="text-[11px] text-gray-500 mb-2">
          已排除：债券/票据 {status.excluded.excluded_bond_note || 0} 只，杠杆/反向ETF {status.excluded.excluded_leveraged_etf || 0} 只
        </div>
      )}

      {showingOtherMarket && (
        <div className="text-[11px] text-yellow-500 mb-2">
          当前展示的是上次跑的 {status!.market === 'hk_stock' ? '港股' : '美股'} 进度，切换市场后点"开始回补"会覆盖
        </div>
      )}

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
              {status.current_batch.length > 0
                ? `正在处理批次: ${status.current_batch.slice(0, 3).join(', ')}${status.current_batch.length > 3 ? '...' : ''}`
                : isRunning ? '处理中…' : '空闲'}
            </span>
          </div>

          <div className="grid grid-cols-3 sm:grid-cols-5 gap-2 mb-3">
            <div className="bg-dark-light rounded-lg px-2 py-1.5">
              <div className="text-sm text-bear font-semibold">{status.success}</div>
              <div className="text-[10px] text-gray-500">成功</div>
            </div>
            <div className="bg-dark-light rounded-lg px-2 py-1.5">
              <div className="text-sm text-gray-300 font-semibold">{status.no_data}</div>
              <div className="text-[10px] text-gray-500">确认无数据</div>
            </div>
            <div className="bg-dark-light rounded-lg px-2 py-1.5">
              <div className="text-sm text-bull font-semibold">{status.failed_batches}</div>
              <div className="text-[10px] text-gray-500">失败批次(待重试)</div>
            </div>
            <div className="bg-dark-light rounded-lg px-2 py-1.5">
              <div className="text-sm text-gray-300 font-semibold">{status.new_records.toLocaleString()}</div>
              <div className="text-[10px] text-gray-500">新增行数</div>
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
                  <span className={r.status === 'ok' ? 'text-bear' : 'text-gray-500'}>
                    {r.status === 'ok' ? `+${r.rows}行` : '无数据'}
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

export default OverseasDeepHistoryPanel;

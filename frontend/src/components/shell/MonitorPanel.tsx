import React, { useCallback, useEffect, useRef, useState } from 'react';
import { useLocation } from 'react-router-dom';
import { useMonitorStore } from '../../store/monitorStore';
import type { MonitorCall } from '../../store/monitorStore';
import { useClickOutside } from '../../hooks/useClickOutside';

/* ================================================================
   MonitorPanel —— 工具调用流程监控悬浮面板（MoneyBill 缩小态风格，可拖动）
   关注「每个调用是否有用」：质量徽标 有效✓/空/失败✕/拦截，非 token 计费。
   默认定位：聊天主界面在左上角；其他页面贴 TokenMonitor 右侧。
   拖动后按页型分别记住落点（localStorage）。
   ================================================================ */

const POS_KEY_MAIN = 'monitor-panel-pos-main';
const POS_KEY_TOOLS = 'monitor-panel-pos-tools';
const MARGIN = 8;
const DRAG_THRESHOLD = 4;

type Pos = { left: number; top: number };

function loadPos(key: string): Pos | null {
  try {
    const raw = localStorage.getItem(key);
    if (!raw) return null;
    const p = JSON.parse(raw) as Pos;
    if (typeof p?.left !== 'number' || typeof p?.top !== 'number') return null;
    return {
      left: Math.max(MARGIN, Math.min(p.left, window.innerWidth - 60)),
      top: Math.max(MARGIN, Math.min(p.top, window.innerHeight - 40)),
    };
  } catch {
    return null;
  }
}

/** 工具页默认位：贴 TokenMonitor 右侧（找不到就退回右上角偏左）。 */
function defaultToolsPos(): Pos {
  const tm = document.querySelector('[data-token-monitor]');
  if (tm) {
    const r = tm.getBoundingClientRect();
    return {
      left: Math.min(r.right + MARGIN, window.innerWidth - 160),
      top: Math.max(MARGIN, r.top),
    };
  }
  return { left: window.innerWidth - 320, top: 12 };
}

/** 聊天主界面默认位：贴侧边栏右侧、主区域左上角（与右上角 TokenMonitor 同一行）。 */
function defaultChatPos(): Pos {
  const sb = document.querySelector('[data-sidebar]');
  const left = sb ? sb.getBoundingClientRect().right + 24 : 280;
  return { left, top: 12 };
}

const VERDICT_BADGE: Record<string, { label: string; cls: string }> = {
  ok: { label: '✓', cls: 'text-bull' },
  empty: { label: '空', cls: 'text-accent-orange' },
  error: { label: '✕', cls: 'text-bear' },
  duplicate: { label: '拦', cls: 'text-gray-500' },
  meta: { label: '组', cls: 'text-accent-cyan' },
  // 工具自己声明的「诚实的否」（如风控正确拒单）——终态答案，不是失败，
  // 不该落回 ok 的绿勾，也不该跟 empty 的橙混在一起
  negative: { label: '拒', cls: 'text-accent-purple' },
};

function Badge({ call }: { call: MonitorCall }) {
  if (call.running) {
    return (
      <svg className="animate-spin h-3 w-3 text-primary shrink-0" viewBox="0 0 24 24" fill="none">
        <circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4" />
        <path className="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4z" />
      </svg>
    );
  }
  const v = VERDICT_BADGE[call.verdict || (call.ok === false ? 'error' : 'ok')] || VERDICT_BADGE.ok;
  return <span className={`${v.cls} font-semibold shrink-0 w-4 text-center`}>{v.label}</span>;
}

const fmtMs = (ms?: number) => (ms == null || ms < 1000 ? '' : `${(ms / 1000).toFixed(1)}s`);

export const MonitorPanel: React.FC = () => {
  const streaming = useMonitorStore((s) => s.streaming);
  const rounds = useMonitorStore((s) => s.rounds);
  const calls = useMonitorStore((s) => s.calls);
  const interventions = useMonitorStore((s) => s.interventions);
  const lastSummary = useMonitorStore((s) => s.lastSummary);

  const { pathname } = useLocation();
  const isChatPage = pathname === '/' || pathname === '/app';
  const posKey = isChatPage ? POS_KEY_MAIN : POS_KEY_TOOLS;

  const [open, setOpen] = useState(false);
  const [pos, setPos] = useState<Pos | null>(null);

  const rootRef = useRef<HTMLDivElement>(null);
  const draggingRef = useRef(false);
  const lastPosRef = useRef<Pos | null>(null);
  const listRef = useRef<HTMLDivElement>(null);

  // 切页型时重算定位：拖过用记住的落点，否则用该页型默认位
  useEffect(() => {
    const saved = loadPos(posKey);
    if (saved) {
      setPos(saved);
    } else {
      setPos(isChatPage ? defaultChatPos() : defaultToolsPos());
    }
  }, [posKey, isChatPage]);

  // 流式中新调用进来，展开态明细自动滚到底
  useEffect(() => {
    if (open && listRef.current) listRef.current.scrollTop = listRef.current.scrollHeight;
  }, [open, calls.length]);

  // 点击面板外部或按 Escape 自动收起（rootRef 含悬浮球本身，不与「再点一次关闭」冲突）
  const closeOnOutside = useCallback(() => setOpen(false), []);
  useClickOutside(rootRef, closeOnOutside, open);

  // 拖动：照 TokenMonitor 的成熟模式（rect 起步、阈值区分点击/拖动、原生 window 监听）
  const onPointerDown = (e: React.PointerEvent) => {
    const el = rootRef.current;
    if (!el) return;
    const rect = el.getBoundingClientRect();
    const startX = e.clientX;
    const startY = e.clientY;
    const ox = rect.left;
    const oy = rect.top;
    const w = rect.width || 140;
    const h = rect.height || 32;
    draggingRef.current = false;

    const onMove = (ev: PointerEvent) => {
      if (!draggingRef.current && Math.hypot(ev.clientX - startX, ev.clientY - startY) < DRAG_THRESHOLD) return;
      draggingRef.current = true;
      let left = ox + (ev.clientX - startX);
      let top = oy + (ev.clientY - startY);
      left = Math.max(MARGIN, Math.min(left, window.innerWidth - w - MARGIN));
      top = Math.max(MARGIN, Math.min(top, window.innerHeight - h - MARGIN));
      lastPosRef.current = { left, top };
      setPos({ left, top });
    };
    const onUp = () => {
      window.removeEventListener('pointermove', onMove);
      window.removeEventListener('pointerup', onUp);
      if (draggingRef.current && lastPosRef.current) {
        try { localStorage.setItem(posKey, JSON.stringify(lastPosRef.current)); } catch { /* ignore */ }
      } else if (!draggingRef.current) {
        setOpen((v) => !v);
      }
    };
    window.addEventListener('pointermove', onMove);
    window.addEventListener('pointerup', onUp);
  };

  const hasIntervention = interventions.length > 0;

  // 胶囊文案：流式中显示实时进度；空闲显示上一 turn 的质量统计
  let pill: React.ReactNode;
  if (streaming) {
    pill = (
      <>
        <span className="relative flex h-2 w-2">
          <span className="animate-ping absolute inline-flex h-full w-full rounded-full bg-accent-cyan opacity-60" />
          <span className="relative inline-flex rounded-full h-2 w-2 bg-accent-cyan" />
        </span>
        <span className="text-gray-300">第 {Math.max(rounds, 0) + 1} 轮 · {calls.length} 调用</span>
      </>
    );
  } else if (lastSummary) {
    const v = lastSummary.verdicts || {};
    pill = (
      <>
        <span>🩺</span>
        <span className="text-bull">{v.ok ?? 0}✓</span>
        {(v.empty ?? 0) > 0 && <span className="text-accent-orange">{v.empty}空</span>}
        {(v.error ?? 0) > 0 && <span className="text-bear">{v.error}✕</span>}
        {(v.duplicate ?? 0) > 0 && <span className="text-gray-500">{v.duplicate}拦</span>}
        {(v.negative ?? 0) > 0 && <span className="text-accent-purple">{v.negative}拒</span>}
      </>
    );
  } else {
    pill = (
      <>
        <span>🩺</span>
        <span className="text-gray-500">监控</span>
      </>
    );
  }

  return (
    <div ref={rootRef} className="fixed z-40" style={pos ? { left: pos.left, top: pos.top } : { left: 12, top: 12 }}>
      <button
        onPointerDown={onPointerDown}
        style={{ touchAction: 'none' }}
        className={`flex items-center gap-2 bg-dark-card/90 backdrop-blur border rounded-full px-3 py-1.5 text-xs shadow-card transition-colors cursor-grab active:cursor-grabbing hover:border-primary/50 ${
          hasIntervention ? 'border-accent-orange/70' : 'border-border'
        }`}
        title="工具调用流程监控（点开看每个调用的质量；可拖动）"
      >
        {pill}
      </button>

      {open && (
        <div className="absolute left-0 mt-2 w-72 bg-dark-card border border-border rounded-xl shadow-card p-3 text-xs z-50">
          <div className="flex items-center justify-between mb-2">
            <span className="text-primary-light font-semibold">🩺 本轮工具调用</span>
            <span className="text-[10px] text-gray-500">
              {streaming ? `进行中 · 第 ${Math.max(rounds, 0) + 1} 轮` : '已结束'}
            </span>
          </div>

          {calls.length === 0 ? (
            <div className="text-gray-500 py-2">还没有工具调用</div>
          ) : (
            <div ref={listRef} className="max-h-56 overflow-y-auto space-y-1 pr-1">
              {calls.map((c, i) => (
                <div key={`${c.id}-${i}`} className="flex items-center gap-2 text-gray-300">
                  <Badge call={c} />
                  <span className="truncate flex-1">{c.name}</span>
                  <span className="text-gray-600 text-[10px] shrink-0">{fmtMs(c.elapsedMs)}</span>
                </div>
              ))}
            </div>
          )}

          {interventions.length > 0 && (
            <div className="border-t border-border mt-2 pt-2 space-y-1">
              <div className="text-accent-orange font-semibold">⚡ 监控干预</div>
              {interventions.map((iv, i) => (
                <div key={i} className="text-accent-orange/90 leading-snug">· {iv.text}</div>
              ))}
            </div>
          )}

          {lastSummary && !streaming && (
            <div className="border-t border-border mt-2 pt-2 text-gray-400 leading-snug">
              {lastSummary.rounds} 轮 · {lastSummary.calls} 次调用
              {' · '}{Math.round(lastSummary.elapsedMsTotal / 1000)}s
              {' · '}{Math.round(((lastSummary.tokens?.prompt ?? 0) + (lastSummary.tokens?.completion ?? 0)) / 1000)}k tokens
            </div>
          )}
        </div>
      )}
    </div>
  );
};

export default MonitorPanel;

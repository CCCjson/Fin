import React, { useCallback, useEffect, useRef, useState } from 'react';
import { useLocation } from 'react-router-dom';
import { useUsageStore } from '../../store/usageStore';
import { agentService } from '../../services/agentService';
import { useClickOutside } from '../../hooks/useClickOutside';

const POS_KEY = 'moneybill-token-pos';
const MARGIN = 8;
const DRAG_THRESHOLD = 4; // 移动超过它才算拖动，否则当点击

type Pos = { left: number; top: number };

/** 读取上次拖动位置并夹到当前视口内。 */
function loadPos(): Pos | null {
  try {
    const raw = localStorage.getItem(POS_KEY);
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

/* Token / 成本监视器 —— 累计 OpenAI 接口 usage，按模型单价折算美元。
   聊天页固定右上；工具页默认顶部居中、可拖动（避开页面右上按钮的遮挡）。 */
export const TokenMonitor: React.FC = () => {
  const baseTokens = useUsageStore((s) => s.totalTokens);
  const liveTokens = useUsageStore((s) => s.liveTokens);
  const costUsd = useUsageStore((s) => s.costUsd);
  const calls = useUsageStore((s) => s.calls);
  const byModel = useUsageStore((s) => s.byModel);
  const today = useUsageStore((s) => s.today);
  const [open, setOpen] = useState(false);
  const [pos, setPos] = useState<Pos | null>(() => loadPos());

  const { pathname } = useLocation();
  const isChatPage = pathname === '/' || pathname === '/app';

  const rootRef = useRef<HTMLDivElement>(null);
  const draggingRef = useRef(false);
  const lastPosRef = useRef<Pos | null>(null);

  const totalTokens = baseTokens + liveTokens;

  // 挂载时拉一次后端累计快照
  useEffect(() => {
    agentService.getUsage().then(useUsageStore.getState().setSnapshot).catch(() => {});
  }, []);

  // 点击面板外部或按 Escape 自动收起
  const closeOnOutside = useCallback(() => setOpen(false), []);
  useClickOutside(rootRef, closeOnOutside, open);

  const fmtTokens = (n: number) => (n >= 1000 ? `${(n / 1000).toFixed(1)}k` : `${n}`);

  // 拖动（仅工具页）：pointerdown 时挂 window 原生监听，超阈值才算拖，松手区分点击/拖动
  const onPointerDown = (e: React.PointerEvent) => {
    if (isChatPage) return;
    const el = rootRef.current;
    if (!el) return;
    const rect = el.getBoundingClientRect(); // 用视觉位置起步（居中态含 translate，offsetLeft 会差半宽导致跳动）
    const startX = e.clientX;
    const startY = e.clientY;
    const ox = rect.left;
    const oy = rect.top;
    const w = rect.width || 120;
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
        // 存 ref 里的实时落点（不读 offsetLeft——那时 React 可能还没重渲染，会存到旧位置）
        try { localStorage.setItem(POS_KEY, JSON.stringify(lastPosRef.current)); } catch { /* ignore */ }
      } else if (!draggingRef.current) {
        setOpen((v) => !v); // 几乎没动 → 当点击，展开/收起明细
      }
    };
    window.addEventListener('pointermove', onMove);
    window.addEventListener('pointerup', onUp);
  };

  // 定位：聊天页固定右上；工具页默认顶部居中，拖动后用内联像素坐标
  const rootClass = isChatPage
    ? 'fixed top-3 right-4 z-40'
    : `fixed z-40${pos ? '' : ' top-3 left-1/2 -translate-x-1/2'}`;
  const rootStyle: React.CSSProperties = !isChatPage && pos
    ? { left: pos.left, top: pos.top }
    : {};

  return (
    <div ref={rootRef} data-token-monitor className={rootClass} style={rootStyle}>
      <button
        onClick={isChatPage ? () => setOpen((v) => !v) : undefined}
        onPointerDown={onPointerDown}
        style={isChatPage ? undefined : { touchAction: 'none' }}
        className={`flex items-center gap-2 bg-dark-card/90 backdrop-blur border border-border hover:border-primary/50 rounded-full px-3 py-1.5 text-xs shadow-card transition-colors ${isChatPage ? '' : 'cursor-grab active:cursor-grabbing'}`}
        title={isChatPage ? '本次运行累计 token 与成本（点开看明细）' : '本次运行累计 token 与成本（点开看明细；可拖动）'}
      >
        <span className="text-accent-cyan">🪙 {fmtTokens(totalTokens)}</span>
        <span className="text-bull font-semibold">${costUsd.toFixed(4)}</span>
      </button>

      {open && (
        <div className="absolute right-0 mt-2 w-64 bg-dark-card border border-border rounded-xl shadow-card p-3 text-xs z-50">
          {/* 今日整体用量（跨重启累计） */}
          <div className="rounded-lg bg-primary/10 border border-primary/30 p-2.5 mb-3">
            <div className="flex items-center justify-between mb-1.5">
              <span className="text-primary-light font-semibold">📅 今日用量</span>
              <span className="text-[10px] text-gray-500">{today?.date ?? '—'}</span>
            </div>
            <div className="flex items-end justify-between">
              <span className="text-lg font-bold text-bull leading-none">
                ${(today?.cost_usd ?? 0).toFixed(4)}
              </span>
              <span className="text-gray-400">
                {fmtTokens(today?.total_tokens ?? 0)} tokens · {today?.calls ?? 0} 次
              </span>
            </div>
          </div>

          <div className="text-gray-500 mb-1.5 text-[11px]">本次运行累计</div>
          <div className="flex justify-between text-gray-300 mb-1">
            <span className="text-gray-500">累计成本</span>
            <span className="text-bull font-semibold">${costUsd.toFixed(6)}</span>
          </div>
          <div className="flex justify-between text-gray-300 mb-1">
            <span className="text-gray-500">总 tokens</span>
            <span>{totalTokens.toLocaleString()}</span>
          </div>
          <div className="flex justify-between text-gray-300 mb-2">
            <span className="text-gray-500">调用次数</span>
            <span>{calls}</span>
          </div>
          {Object.keys(byModel).length > 0 && (
            <div className="border-t border-border pt-2 space-y-1">
              <div className="text-gray-500 mb-1">按模型</div>
              {Object.entries(byModel).map(([m, v]: [string, any]) => (
                <div key={m} className="flex justify-between text-gray-400">
                  <span className="truncate mr-2">{m}</span>
                  <span>${Number(v.cost).toFixed(4)}</span>
                </div>
              ))}
            </div>
          )}
          <div className="text-[10px] text-gray-600 mt-2 leading-snug">
            单价默认为占位值，按实际计费用环境变量 AGENT_PRICE__&lt;model&gt; 设置后才准确。
          </div>
        </div>
      )}
    </div>
  );
};

export default TokenMonitor;

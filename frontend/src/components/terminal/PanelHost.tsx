import React, { useCallback, useEffect, useMemo, useState } from 'react';

/**
 * 面板系统（S7，裁决 15：**不写死布局**）。
 *
 * 核心是「**注册 widget 即出现**」：往调用方的 `panels` 数组里加一项，终端上就多一块，
 * 不用改布局代码、不用调 CSS。折叠 / 上下移 / 显隐由 Jason 定，布局存 localStorage。
 *
 * ⛔ **刻意不做完整拖拽网格**（Jason 2026-07-31 拍板）：裁决 15 的核心用按钮同样成立，
 * 而裁决 16 说真正的价值在「每个数字自解释」，不在拖拽手感 —— 何况 Mac 触控板上
 * 拖拽未必比按钮好用。
 *
 * ⚠️ 布局只存**顺序和折叠/隐藏状态**，不存内容。新增面板时老布局里没有它的记录，
 * 一律按「显示 + 展开 + 排在末尾」处理 —— 否则加一块新面板对老用户是隐形的。
 */

export interface PanelDef {
  /** 稳定 key。⚠️ 改了它等于新面板，Jason 调好的布局会丢。 */
  key: string;
  title: string;
  /** 标题右侧的一句摘要（可选）。裁决 16：折叠状态下也要能看出「所以我该干嘛」。 */
  subtitle?: React.ReactNode;
  /** 徽章（如待确认单条数）。折叠时仍显示 —— 这是「有事发生」的唯一信号。 */
  badge?: React.ReactNode;
  /** 不允许隐藏（如急停相关）。 */
  pinned?: boolean;
  /**
   * ⚠️ 这是个**组件**，会被当 `<C />` 挂载，不是当普通函数调。
   * 早先写成 `{p.render()}`，看着没事（返的都是元素），但那样它的 hook 归属的是
   * `PanelHost` —— 面板一折叠，PanelHost 这一次渲染的 hook 数量就变了，React 直接崩。
   */
  render: React.FC;
}

interface LayoutEntry {
  key: string;
  collapsed: boolean;
  hidden: boolean;
}

const STORAGE_KEY = 'fin.terminal.layout.v1';

function loadLayout(): LayoutEntry[] {
  try {
    const raw = localStorage.getItem(STORAGE_KEY);
    const arr = raw ? JSON.parse(raw) : null;
    return Array.isArray(arr) ? arr : [];
  } catch {
    return [];   // 存坏了就当没存过，别让终端打不开
  }
}

/**
 * 已存布局 + 面板定义 → 实际顺序。
 *
 * ⭐ **新面板一律排末尾且可见**：老布局里查不到的 key 说明它是新加的，
 * 默认隐藏的话 Jason 永远不知道多了一块东西。
 */
function mergeLayout(saved: LayoutEntry[], panels: PanelDef[]): LayoutEntry[] {
  const known = new Set(panels.map((p) => p.key));
  const kept = saved.filter((e) => known.has(e.key));
  const seen = new Set(kept.map((e) => e.key));
  const added = panels
    .filter((p) => !seen.has(p.key))
    .map((p) => ({ key: p.key, collapsed: false, hidden: false }));
  return [...kept, ...added];
}

export const PanelHost: React.FC<{ panels: PanelDef[] }> = ({ panels }) => {
  const [saved, setSaved] = useState<LayoutEntry[]>(loadLayout);
  const [editing, setEditing] = useState(false);

  // ⚠️ 合并在**渲染时**算，不放 effect：面板集合是从 props 推导出来的，
  // 用 effect 同步会多一次级联渲染，eslint 的 `set-state-in-effect` 也会咬。
  // （`mergeLayout` 是纯函数，重复算不花钱。）
  const layout = useMemo(() => mergeLayout(saved, panels), [saved, panels]);

  useEffect(() => {
    // `layout` 每次轮询都会因为 `panels` 换引用而重算成新数组，内容却一模一样 ——
    // 比一下再写，免得 60 秒往 localStorage 抄一遍同样的东西。
    try {
      const next = JSON.stringify(layout);
      if (localStorage.getItem(STORAGE_KEY) !== next) localStorage.setItem(STORAGE_KEY, next);
    } catch { /* 存不下就算了 */ }
  }, [layout]);

  const byKey = useMemo(
    () => Object.fromEntries(panels.map((p) => [p.key, p])) as Record<string, PanelDef>,
    [panels]);

  // 写回 `saved`：以合并后的结果为基准，这样对新加进来的面板做的调整也存得住。
  // ⚠️ 用函数式更新（`prev =>`）而不是直接闭包捕获 `layout` —— 后者忽略前值，
  // 连点两下时要靠 React 的同步 flush 才不丢更新，是个脆的写法。
  const update = useCallback((key: string, patch: Partial<LayoutEntry>) => {
    setSaved((prev) => mergeLayout(prev, panels)
      .map((e) => (e.key === key ? { ...e, ...patch } : e)));
  }, [panels]);

  /**
   * 上下移。
   *
   * 🔴 **必须在「可见序列」里找相邻项**，不能用完整 layout 的下标。
   * 早先用的是完整下标，于是中间隔着一块隐藏面板时，交换的是「可见项 ↔ 隐藏项」——
   * 屏幕上纹丝不动，看起来就是「按钮点了没反应，多点几下才动一格」。
   */
  const move = useCallback((key: string, dir: -1 | 1) => {
    setSaved((prev) => {
      const merged = mergeLayout(prev, panels);
      const vis = merged.map((e, i) => ({ e, i })).filter(({ e }) => !e.hidden && byKey[e.key]);
      const at = vis.findIndex(({ e }) => e.key === key);
      const to = at + dir;
      if (at < 0 || to < 0 || to >= vis.length) return merged;
      const next = [...merged];
      const [a, b] = [vis[at].i, vis[to].i];
      [next[a], next[b]] = [next[b], next[a]];
      return next;
    });
  }, [panels, byKey]);

  const visible = layout.filter((e) => !e.hidden && byKey[e.key]);
  const hidden = layout.filter((e) => e.hidden && byKey[e.key]);

  return (
    <div className="space-y-3">
      <div className="flex items-center justify-end gap-2">
        {hidden.length > 0 && !editing && (
          <span className="text-xs text-gray-500">{hidden.length} 块已隐藏</span>
        )}
        <button
          onClick={() => setEditing((v) => !v)}
          className={`text-xs px-2 py-1 rounded border transition-colors ${
            editing ? 'border-primary text-primary' : 'border-border text-gray-400 hover:text-gray-200'
          }`}
        >
          {editing ? '完成' : '调整布局'}
        </button>
      </div>

      {visible.map((e, idx) => {
        const p = byKey[e.key];
        const Body = p.render;
        return (
          <section key={e.key}
            className="rounded-xl border border-border bg-dark-card overflow-hidden">
            <header className="flex items-center gap-2 px-4 py-2.5 bg-dark-light/30">
              <button
                onClick={() => update(e.key, { collapsed: !e.collapsed })}
                className="text-gray-500 hover:text-gray-200 text-xs w-4"
                title={e.collapsed ? '展开' : '折叠'}
              >
                {e.collapsed ? '▸' : '▾'}
              </button>
              <span className="text-white font-semibold text-sm">{p.title}</span>
              {p.badge}
              {/* 折叠状态下摘要仍要看得见 —— 否则折起来就等于「这块不存在」 */}
              {p.subtitle && (
                <span className="text-xs text-gray-400 truncate">{p.subtitle}</span>
              )}
              {editing && (
                <span className="ml-auto flex items-center gap-1">
                  <button onClick={() => move(e.key, -1)} disabled={idx === 0}
                    className="text-xs px-1.5 py-0.5 rounded border border-border text-gray-400
                               hover:text-gray-100 disabled:opacity-30">↑</button>
                  <button onClick={() => move(e.key, 1)} disabled={idx === visible.length - 1}
                    className="text-xs px-1.5 py-0.5 rounded border border-border text-gray-400
                               hover:text-gray-100 disabled:opacity-30">↓</button>
                  {!p.pinned && (
                    <button onClick={() => update(e.key, { hidden: true })}
                      className="text-xs px-1.5 py-0.5 rounded border border-border text-gray-400
                                 hover:text-red-400">隐藏</button>
                  )}
                </span>
              )}
            </header>
            {!e.collapsed && <div className="p-4"><Body /></div>}
          </section>
        );
      })}

      {editing && hidden.length > 0 && (
        <div className="rounded-xl border border-dashed border-border p-3 space-y-2">
          <div className="text-xs text-gray-500">已隐藏</div>
          {hidden.map((e) => (
            <div key={e.key} className="flex items-center gap-2 text-sm">
              <span className="text-gray-400">{byKey[e.key].title}</span>
              <button onClick={() => update(e.key, { hidden: false })}
                className="ml-auto text-xs px-1.5 py-0.5 rounded border border-border
                           text-gray-400 hover:text-primary">显示</button>
            </div>
          ))}
        </div>
      )}
    </div>
  );
};

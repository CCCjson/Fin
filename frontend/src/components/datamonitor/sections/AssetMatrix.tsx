import React, { useState } from 'react';
import { Card } from '../../common/Card';
import { Button } from '../../common/Button';
import type { AssetCell, AssetMatrix as Matrix } from '../../../services/dataMonitorService';
import {
  GROUP_META,
  GROUP_ORDER,
  HEALTH_DOT,
  HEALTH_LABEL,
  HEALTH_STYLE,
  MARKET_META,
  MARKET_ORDER,
  fmtDur,
  fmtNum,
  fmtTime,
} from './shared';

/* ────────────────────────────────────────────────────────────────
   资产矩阵 —— 行=资产组，列=市场。

   ## 为什么是二维

   原来是一维的 7 张卡（日线/实时/财报/估值/新闻/涨停/知识库），**每张卡都只
   统计 A 股**。港美股的健康状态整页只有一行新鲜度条带，crypto 一个胶囊，
   它们各自的衍生数据压根没有位置。改成二维之后，「美股日线落后了」是一眼可见
   的，不用逐张卡去读数字。

   跨市场资产（信号/追踪/决策后验/新闻）没有 market，单独一行「全局」。
   ──────────────────────────────────────────────────────────────── */

interface Props {
  matrix: Matrix | null;
  runningKeys: Set<string>;
  onRunOne: (key: string) => void;
}

const CellBox: React.FC<{
  cell: AssetCell | undefined;
  running: boolean;
  onClick: () => void;
}> = ({ cell, running, onClick }) => {
  if (!cell) {
    return (
      <div className="rounded-lg border border-border/40 bg-dark-light/20 px-2.5 py-2 text-center">
        <span className="text-[11px] text-gray-700">—</span>
      </div>
    );
  }
  return (
    <button
      type="button"
      onClick={onClick}
      title={`${cell.hint}\n${cell.health_reason}`}
      className={`w-full text-left rounded-lg border px-2.5 py-2 transition-colors hover:border-primary/50 ${
        cell.enabled ? HEALTH_STYLE[cell.health] : 'bg-dark-light/30 border-border/40'
      }`}
    >
      <div className="flex items-center justify-between gap-1">
        <span className="text-[11px] text-gray-200 truncate">{cell.label}</span>
        {running ? (
          <span className="text-[10px] text-primary shrink-0">⏳</span>
        ) : (
          <span className={`w-1.5 h-1.5 rounded-full shrink-0 ${HEALTH_DOT[cell.health]}`} />
        )}
      </div>
      <div className="text-xs font-medium text-white tabular-nums mt-0.5">
        {cell.latest_date?.slice(5) || (cell.count_at_latest ? '—' : '无数据')}
      </div>
      <div className="text-[10px] text-gray-500 truncate">{cell.detail}</div>
      {!cell.enabled && (
        <div className="text-[10px] text-gray-600 mt-0.5">已关闭</div>
      )}
    </button>
  );
};

/** 下钻抽屉：点格子展开，看该资产的完整状态 + 单独更新 */
const Drawer: React.FC<{
  cell: AssetCell;
  running: boolean;
  onRun: () => void;
  onClose: () => void;
}> = ({ cell, running, onRun, onClose }) => (
  <div className="mt-3 rounded-lg border border-primary/30 bg-dark-light/40 p-3">
    <div className="flex items-center justify-between flex-wrap gap-2 mb-2">
      <div className="text-sm text-white font-medium flex items-center gap-2">
        {cell.market && MARKET_META[cell.market] && (
          <span>{MARKET_META[cell.market].icon}</span>
        )}
        {cell.label}
        <span
          className={`text-[10px] px-1.5 py-0.5 rounded-full border ${HEALTH_STYLE[cell.health]}`}
        >
          {HEALTH_LABEL[cell.health]}
        </span>
      </div>
      <div className="flex items-center gap-2">
        <Button
          variant="subtle"
          size="sm"
          loading={running}
          disabled={running || !cell.enabled}
          onClick={onRun}
        >
          {running ? '更新中' : '只更新这一项'}
        </Button>
        <button
          type="button"
          onClick={onClose}
          className="text-xs text-gray-500 hover:text-gray-300"
        >
          收起 ↑
        </button>
      </div>
    </div>

    <div className="text-[11px] text-gray-400 mb-2">{cell.hint}</div>

    <div className="grid grid-cols-2 md:grid-cols-4 gap-2 text-[11px]">
      <div className="bg-dark rounded px-2 py-1.5">
        <div className="text-gray-600">最新日期</div>
        <div className="text-gray-200 tabular-nums">{cell.latest_date || '—'}</div>
      </div>
      <div className="bg-dark rounded px-2 py-1.5">
        <div className="text-gray-600">当前量</div>
        <div className="text-gray-200 tabular-nums">{fmtNum(cell.count_at_latest)}</div>
      </div>
      <div className="bg-dark rounded px-2 py-1.5">
        <div className="text-gray-600">落后交易日</div>
        <div className="text-gray-200 tabular-nums">
          {cell.behind_trading_days === null ? '不适用' : cell.behind_trading_days}
        </div>
      </div>
      <div className="bg-dark rounded px-2 py-1.5">
        <div className="text-gray-600">缺口</div>
        <div className="text-gray-200 tabular-nums">
          {cell.supports_gap_fill
            ? `${cell.gaps_certain} 确认 / ${cell.gaps_suspected} 疑似`
            : '不参与'}
        </div>
      </div>
    </div>

    {cell.last_run && (
      <div className="mt-2 text-[11px] text-gray-500">
        上次运行：{cell.last_run.status} · {fmtTime(cell.last_run.completed_at)} ·{' '}
        {fmtNum(cell.last_run.records)} 条 · 耗时 {fmtDur(cell.last_run.duration_seconds)}
      </div>
    )}
    <div className="mt-1 text-[11px] text-gray-600">判定：{cell.health_reason}</div>
  </div>
);

export const AssetMatrix: React.FC<Props> = ({ matrix, runningKeys, onRunOne }) => {
  const [openKey, setOpenKey] = useState<string | null>(null);

  if (!matrix) return null;

  const byGroup = (group: string) => matrix.cells.filter((c) => c.group === group);
  const globalCells = matrix.cells.filter((c) => c.market === null);
  const openCell = openKey ? matrix.cells.find((c) => c.key === openKey) : null;

  return (
    <Card className="p-4">
      <div className="text-sm text-white font-medium mb-3">
        🗂️ 数据资产矩阵
        <span className="text-xs text-gray-500 font-normal ml-2">
          点任意格子看详情 / 单独更新
        </span>
      </div>

      {/* 表头：市场列 */}
      <div className="overflow-x-auto">
        <div className="min-w-[680px]">
          <div className="grid grid-cols-[80px_repeat(4,1fr)] gap-2 mb-2">
            <div />
            {MARKET_ORDER.map((m) => (
              <div key={m} className="text-xs text-gray-400 text-center">
                {MARKET_META[m].icon} {MARKET_META[m].label}
              </div>
            ))}
          </div>

          {/* 每个资产组一行块 */}
          {GROUP_ORDER.map((group) => {
            const cells = byGroup(group).filter((c) => c.market !== null);
            if (!cells.length) return null;
            // 同一组里可能有多个资产共用一个市场（少见），按资产逐行铺
            const keysInGroup = Array.from(new Set(cells.map((c) => c.label)));
            return (
              <div key={group} className="mb-2">
                {keysInGroup.map((label, idx) => (
                  <div
                    key={label}
                    className="grid grid-cols-[80px_repeat(4,1fr)] gap-2 mb-1.5 items-stretch"
                  >
                    <div className="text-[11px] text-gray-500 flex items-center">
                      {idx === 0 && (
                        <span>
                          {GROUP_META[group].icon} {GROUP_META[group].label}
                        </span>
                      )}
                    </div>
                    {MARKET_ORDER.map((m) => {
                      const cell = cells.find((c) => c.market === m && c.label === label);
                      return (
                        <CellBox
                          key={m}
                          cell={cell}
                          running={!!cell && runningKeys.has(cell.key)}
                          onClick={() =>
                            cell && setOpenKey(openKey === cell.key ? null : cell.key)
                          }
                        />
                      );
                    })}
                  </div>
                ))}
              </div>
            );
          })}

          {/* 跨市场资产单独一行 */}
          {globalCells.length > 0 && (
            <div className="mt-3 pt-3 border-t border-border/50">
              <div className="text-[11px] text-gray-500 mb-1.5">🌐 跨市场</div>
              <div className="grid grid-cols-2 md:grid-cols-4 gap-2">
                {globalCells.map((cell) => (
                  <CellBox
                    key={cell.key}
                    cell={cell}
                    running={runningKeys.has(cell.key)}
                    onClick={() => setOpenKey(openKey === cell.key ? null : cell.key)}
                  />
                ))}
              </div>
            </div>
          )}
        </div>
      </div>

      {openCell && (
        <Drawer
          cell={openCell}
          running={runningKeys.has(openCell.key)}
          onRun={() => onRunOne(openCell.key)}
          onClose={() => setOpenKey(null)}
        />
      )}
    </Card>
  );
};

export default AssetMatrix;

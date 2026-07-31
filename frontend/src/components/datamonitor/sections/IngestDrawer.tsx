import React, { useState } from 'react';
import { Card } from '../../common/Card';
import { ScrapeMonitorPanel } from '../ScrapeMonitorPanel';
import { CninfoIngestPanel } from '../CninfoIngestPanel';
import { ResearchReportIngestPanel } from '../ResearchReportIngestPanel';
import { ArxivIngestPanel } from '../ArxivIngestPanel';
import { NewsJobPanel } from '../NewsJobPanel';
import { DeepHistoryPanel } from '../DeepHistoryPanel';
import { KnowledgePanel } from '../KnowledgePanel';
import type { KnowledgeStats } from '../../../services/knowledgeService';

/* ────────────────────────────────────────────────────────────────
   摄入任务折叠区 —— 7 个长任务面板，**默认收起**。

   原来这 7 个面板和 7 张资产卡一样竖着堆在主视图里，视觉权重完全相同，于是
   「看数据健康」和「跑摄入任务」两类事混在一页，一屏看不到全貌，要滚很久。

   ⛔ **面板内部实现一行没改**，只是搬进折叠容器 —— 它们各自带着起停 + 轮询 +
   断点续跑的逻辑，重构布局不是动它们的时机。

   这里的任务都是**小时级**的，而且深历史和港美股日线抢同一把 Yahoo 锁，
   所以它们不在「更新全部」里（2026-07-27 Jason 确认），只能单独触发。
   ──────────────────────────────────────────────────────────────── */

interface Props {
  knowledgeStats: KnowledgeStats | null;
}

const SECTIONS: { id: string; label: string; hint: string }[] = [
  { id: 'deep', label: '🗄️ 深历史回补', hint: 'A股+港股+美股 全历史日线，小时级' },
  { id: 'cninfo', label: '📑 巨潮财报摄入', hint: '全市场 A 股法定财报，常驻后台' },
  { id: 'rr', label: '📈 东财研报摄入', hint: '全市场研报，常驻后台' },
  { id: 'arxiv', label: '🎓 arXiv 论文', hint: '官方 API，一次性同步' },
  { id: 'news', label: '📰 新闻定时任务', hint: '15 分钟抓取 + 分析' },
  { id: 'scrape', label: '🕷️ 抓取任务监控', hint: '含聊天里触发的抓取' },
  { id: 'knowledge', label: '📚 知识库', hint: 'bge-m3 向量 + 想法挖掘' },
];

export const IngestDrawer: React.FC<Props> = ({ knowledgeStats }) => {
  const [open, setOpen] = useState(false);
  const [active, setActive] = useState<string>('deep');

  return (
    <Card className="p-4">
      <button
        type="button"
        onClick={() => setOpen(!open)}
        className="w-full flex items-center justify-between gap-2 text-left"
      >
        <div>
          <div className="text-sm text-white font-medium">
            {open ? '▾' : '▸'} 摄入任务
            <span className="text-xs text-gray-500 font-normal ml-2">
              深历史 / 财报 / 研报 / 论文 / 新闻 / 抓取 / 知识库
            </span>
          </div>
          {!open && (
            <div className="text-[11px] text-gray-600 mt-0.5">
              小时级长任务，不参与「更新全部」，按需单独跑
            </div>
          )}
        </div>
        <span className="text-xs text-gray-500 shrink-0">{open ? '收起' : '展开'}</span>
      </button>

      {open && (
        <div className="mt-3">
          {/* 标签页而不是全部堆出来 —— 7 个面板各自有轮询，同时挂载会打 7 路请求 */}
          <div className="flex flex-wrap gap-1 mb-3">
            {SECTIONS.map((s) => (
              <button
                key={s.id}
                type="button"
                onClick={() => setActive(s.id)}
                title={s.hint}
                className={`text-xs px-2.5 py-1 rounded-lg transition-colors ${
                  active === s.id
                    ? 'bg-primary/20 text-primary'
                    : 'text-gray-500 hover:text-gray-300 hover:bg-dark-light'
                }`}
              >
                {s.label}
              </button>
            ))}
          </div>

          {active === 'deep' && <DeepHistoryPanel />}
          {active === 'cninfo' && <CninfoIngestPanel />}
          {active === 'rr' && <ResearchReportIngestPanel />}
          {active === 'arxiv' && <ArxivIngestPanel />}
          {active === 'news' && <NewsJobPanel />}
          {active === 'scrape' && <ScrapeMonitorPanel />}
          {active === 'knowledge' && <KnowledgePanel stats={knowledgeStats} />}
        </div>
      )}
    </Card>
  );
};

export default IngestDrawer;

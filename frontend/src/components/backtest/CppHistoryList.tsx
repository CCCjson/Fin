import React from 'react';

interface HistoryItem {
  task_id: string;
  name: string;
  strategy_type: string;
  symbols: string[];
  status: string;
  created_at: string;
  metrics?: {
    total_return_pct?: number;
  };
}

interface CppHistoryListProps {
  items: HistoryItem[];
  selectedId: string | null;
  checkedIds: Set<string>;
  onSelect: (item: HistoryItem) => void;
  onCheck: (taskId: string) => void;
  onDelete: (taskId: string) => void;
  onRefresh: () => void;
}

export const CppHistoryList: React.FC<CppHistoryListProps> = ({
  items, selectedId, checkedIds, onSelect, onCheck, onDelete, onRefresh,
}) => {
  return (
    <div className="bg-gradient-card border border-border shadow-card p-3 md:p-4 rounded-xl">
      <div className="flex justify-between items-center mb-3">
        <h2 className="text-base md:text-lg font-semibold text-white">回测记录</h2>
        <button onClick={onRefresh}
          className="text-xs text-primary-light hover:text-primary transition-colors">
          刷新
        </button>
      </div>

      <div className="space-y-2 max-h-[600px] overflow-y-auto pr-1">
        {items.length === 0 ? (
          <div className="text-center text-gray-500 py-8 text-sm">暂无记录</div>
        ) : (
          items.map(item => (
            <div
              key={item.task_id}
              onClick={() => onSelect(item)}
              className={`p-3 rounded-lg border cursor-pointer transition-all ${
                selectedId === item.task_id
                  ? 'bg-primary/20 border-primary shadow-glow-blue'
                  : 'bg-dark-light border-border hover:border-border-light'
              }`}
            >
              <div className="flex items-start gap-2">
                <input
                  type="checkbox"
                  checked={checkedIds.has(item.task_id)}
                  onChange={(e) => { e.stopPropagation(); onCheck(item.task_id); }}
                  onClick={(e) => e.stopPropagation()}
                  className="mt-1 rounded border-border bg-dark-light"
                />
                <div className="flex-1 min-w-0">
                  <div className="flex justify-between items-start mb-1">
                    <h3 className="font-medium text-white text-sm truncate flex-1 mr-2">
                      {item.name || '未命名'}
                    </h3>
                    <div className="flex items-center gap-1.5 flex-shrink-0">
                      <span className={`px-1.5 py-0.5 rounded text-[10px] font-semibold ${
                        item.status === 'completed'
                          ? 'bg-bull/20 text-bull'
                          : item.status === 'failed'
                          ? 'bg-bear/20 text-bear'
                          : 'bg-gray-700 text-gray-300'
                      }`}>
                        {item.status === 'completed' ? '完成' : item.status === 'failed' ? '失败' : item.status}
                      </span>
                      <button
                        onClick={(e) => { e.stopPropagation(); onDelete(item.task_id); }}
                        className="text-gray-500 hover:text-bear transition-colors text-xs p-0.5"
                        title="删除"
                      >
                        x
                      </button>
                    </div>
                  </div>
                  <div className="text-xs text-gray-400 space-y-0.5">
                    <div className="flex justify-between">
                      <span>{item.strategy_type?.replace('CPP_', '') || '-'}</span>
                      <span>{item.symbols?.join(', ') || '-'}</span>
                    </div>
                    <div className="text-[10px]">{item.created_at?.slice(0, 16) || '-'}</div>
                  </div>
                </div>
              </div>
            </div>
          ))
        )}
      </div>
    </div>
  );
};

import React, { useState, useEffect, useCallback } from 'react';
import { portfolioService } from '../services/portfolioService';
import type {
  ClosedTrade,
  ClosedTradeSummary,
  ClosedTradeListParams,
} from '../services/portfolioService';

// ==================== 辅助函数 ====================

const formatMoney = (v: number | null | undefined) => {
  if (v === null || v === undefined) return '-';
  return v.toLocaleString('zh-CN', { minimumFractionDigits: 2, maximumFractionDigits: 2 });
};

const formatPct = (v: number | null | undefined) => {
  if (v === null || v === undefined) return '-';
  const sign = v >= 0 ? '+' : '';
  return `${sign}${v.toFixed(2)}%`;
};

const pnlColor = (v: number | null | undefined) => {
  if (v === null || v === undefined) return 'text-gray-500';
  if (v > 0) return 'text-bull';
  if (v < 0) return 'text-bear';
  return 'text-gray-400';
};

const ENV_LABELS: Record<string, { text: string; color: string }> = {
  bullish: { text: '牛市', color: 'bg-red-500/20 text-red-400' },
  neutral: { text: '震荡', color: 'bg-yellow-500/20 text-yellow-400' },
  bearish: { text: '熊市', color: 'bg-green-500/20 text-green-400' },
  unknown: { text: '未知', color: 'bg-gray-500/20 text-gray-400' },
};

const REASON_LABELS: Record<string, { text: string; color: string }> = {
  take_profit: { text: '止盈', color: 'bg-red-500/20 text-red-400' },
  stop_loss: { text: '止损', color: 'bg-green-500/20 text-green-400' },
  manual_close: { text: '主动平仓', color: 'bg-blue-500/20 text-blue-400' },
};

// ==================== 组件 ====================

export const ClosedTradesTab: React.FC = () => {
  const [trades, setTrades] = useState<ClosedTrade[]>([]);
  const [summary, setSummary] = useState<ClosedTradeSummary | null>(null);
  const [total, setTotal] = useState(0);
  const [loading, setLoading] = useState(false);
  const [rebuilding, setRebuilding] = useState(false);

  // 筛选
  const [filterSymbol, setFilterSymbol] = useState('');
  const [filterReason, setFilterReason] = useState('');
  const [filterEnv, setFilterEnv] = useState('');
  const [filterStartDate, setFilterStartDate] = useState('');
  const [filterEndDate, setFilterEndDate] = useState('');
  const [page, setPage] = useState(1);
  const pageSize = 20;

  const loadData = useCallback(async (targetPage?: number) => {
    setLoading(true);
    try {
      const p = targetPage ?? page;
      const params: ClosedTradeListParams = {
        limit: pageSize,
        offset: (p - 1) * pageSize,
        sort_by: 'sell_date',
        sort_order: 'desc',
      };
      if (filterSymbol) params.symbol = filterSymbol;
      if (filterReason) params.sell_reason = filterReason;
      if (filterEnv) params.market_env = filterEnv;
      if (filterStartDate) params.start_date = filterStartDate;
      if (filterEndDate) params.end_date = filterEndDate;

      const data = await portfolioService.getClosedTrades(params);
      setTrades(data.trades);
      setTotal(data.total);
      setSummary(data.summary);
    } catch (e) {
      console.error('Failed to load closed trades:', e);
    } finally {
      setLoading(false);
    }
  }, [page, filterSymbol, filterReason, filterEnv, filterStartDate, filterEndDate]);

  useEffect(() => {
    loadData();
  }, []);

  const handleSearch = () => {
    setPage(1);
    loadData(1);
  };

  const handleRebuild = async () => {
    if (!confirm('确认重建全部已平仓记录？这将清空现有数据并重新生成。')) return;
    setRebuilding(true);
    try {
      const result = await portfolioService.rebuildClosedTrades();
      alert(result.message);
      loadData(1);
    } catch (e) {
      console.error('Rebuild failed:', e);
      alert('重建失败，请查看后端日志');
    } finally {
      setRebuilding(false);
    }
  };

  const totalPages = Math.ceil(total / pageSize);

  return (
    <div className="space-y-4">
      {/* 顶部统计卡片 */}
      {summary && (
        <div className="grid grid-cols-2 md:grid-cols-4 gap-3">
          <StatCard
            title="总笔数 / 胜率"
            value={`${summary.count} 笔`}
            sub={`胜率 ${summary.win_rate}%`}
            subColor={summary.win_rate >= 50 ? 'text-bull' : 'text-bear'}
          />
          <StatCard
            title="总盈亏 / 平均收益"
            value={formatMoney(summary.total_pnl)}
            valueColor={pnlColor(summary.total_pnl)}
            sub={`平均 ${formatPct(summary.avg_pnl_pct)}`}
            subColor={pnlColor(summary.avg_pnl_pct)}
          />
          <StatCard
            title="平均持仓天数"
            value={`${summary.avg_holding_days} 天`}
          />
          <StatCard
            title="平均超额收益"
            value={summary.avg_excess_return_pct !== null ? formatPct(summary.avg_excess_return_pct) : '-'}
            valueColor={pnlColor(summary.avg_excess_return_pct)}
            sub={summary.avg_benchmark_return_pct !== null ? `沪深300 ${formatPct(summary.avg_benchmark_return_pct)}` : ''}
          />
        </div>
      )}

      {/* 筛选栏 + 重建按钮 */}
      <div className="bg-card-dark rounded-lg p-3 md:p-4">
        <div className="flex flex-wrap items-end gap-2 md:gap-3">
          <div className="flex-1 min-w-[120px]">
            <label className="block text-xs text-gray-400 mb-1">股票代码</label>
            <input
              type="text"
              value={filterSymbol}
              onChange={e => setFilterSymbol(e.target.value.toUpperCase())}
              placeholder="如 600519.SH"
              className="w-full bg-gray-800 border border-gray-700 rounded px-2 py-1.5 text-sm text-white"
            />
          </div>
          <div className="min-w-[100px]">
            <label className="block text-xs text-gray-400 mb-1">卖出原因</label>
            <select
              value={filterReason}
              onChange={e => setFilterReason(e.target.value)}
              className="w-full bg-gray-800 border border-gray-700 rounded px-2 py-1.5 text-sm text-white"
            >
              <option value="">全部</option>
              <option value="take_profit">止盈</option>
              <option value="stop_loss">止损</option>
              <option value="manual_close">主动平仓</option>
            </select>
          </div>
          <div className="min-w-[100px]">
            <label className="block text-xs text-gray-400 mb-1">大盘环境</label>
            <select
              value={filterEnv}
              onChange={e => setFilterEnv(e.target.value)}
              className="w-full bg-gray-800 border border-gray-700 rounded px-2 py-1.5 text-sm text-white"
            >
              <option value="">全部</option>
              <option value="bullish">牛市</option>
              <option value="neutral">震荡</option>
              <option value="bearish">熊市</option>
            </select>
          </div>
          <div className="min-w-[120px]">
            <label className="block text-xs text-gray-400 mb-1">开始日期</label>
            <input
              type="date"
              value={filterStartDate}
              onChange={e => setFilterStartDate(e.target.value)}
              className="w-full bg-gray-800 border border-gray-700 rounded px-2 py-1.5 text-sm text-white"
            />
          </div>
          <div className="min-w-[120px]">
            <label className="block text-xs text-gray-400 mb-1">结束日期</label>
            <input
              type="date"
              value={filterEndDate}
              onChange={e => setFilterEndDate(e.target.value)}
              className="w-full bg-gray-800 border border-gray-700 rounded px-2 py-1.5 text-sm text-white"
            />
          </div>
          <button
            onClick={handleSearch}
            className="bg-blue-600 hover:bg-blue-500 text-white px-4 py-1.5 rounded text-sm font-medium"
          >
            查询
          </button>
          <button
            onClick={handleRebuild}
            disabled={rebuilding}
            className="bg-orange-600 hover:bg-orange-500 disabled:opacity-50 text-white px-4 py-1.5 rounded text-sm font-medium"
          >
            {rebuilding ? '重建中...' : '重建'}
          </button>
        </div>
      </div>

      {/* 数据表格 — 桌面端 */}
      <div className="hidden md:block bg-card-dark rounded-lg overflow-hidden">
        <div className="overflow-x-auto">
          <table className="w-full text-sm">
            <thead>
              <tr className="border-b border-gray-700 text-gray-400">
                <th className="px-3 py-2 text-left">股票</th>
                <th className="px-3 py-2 text-left">买入日</th>
                <th className="px-3 py-2 text-right">买入价</th>
                <th className="px-3 py-2 text-left">策略</th>
                <th className="px-3 py-2 text-center">强度</th>
                <th className="px-3 py-2 text-center">大盘</th>
                <th className="px-3 py-2 text-left">卖出日</th>
                <th className="px-3 py-2 text-right">卖出价</th>
                <th className="px-3 py-2 text-center">原因</th>
                <th className="px-3 py-2 text-right">天数</th>
                <th className="px-3 py-2 text-right">收益率</th>
                <th className="px-3 py-2 text-right">沪深300</th>
              </tr>
            </thead>
            <tbody>
              {loading ? (
                <tr>
                  <td colSpan={12} className="text-center py-8 text-gray-500">加载中...</td>
                </tr>
              ) : trades.length === 0 ? (
                <tr>
                  <td colSpan={12} className="text-center py-8 text-gray-500">
                    暂无已平仓记录，点击"重建"生成历史数据
                  </td>
                </tr>
              ) : (
                trades.map(t => {
                  const env = ENV_LABELS[t.market_env || 'unknown'] || ENV_LABELS.unknown;
                  const reason = REASON_LABELS[t.sell_reason || 'manual_close'] || REASON_LABELS.manual_close;
                  return (
                    <tr key={t.id} className="border-b border-gray-800 hover:bg-gray-800/50">
                      <td className="px-3 py-2">
                        <div className="font-medium text-white">{t.symbol}</div>
                        <div className="text-xs text-gray-500">{t.name}</div>
                      </td>
                      <td className="px-3 py-2 text-gray-300">{t.buy_date}</td>
                      <td className="px-3 py-2 text-right text-gray-300">{t.buy_price?.toFixed(2)}</td>
                      <td className="px-3 py-2 text-gray-400 text-xs max-w-[100px] truncate">{t.buy_signal_strategy || '-'}</td>
                      <td className="px-3 py-2 text-center">
                        {t.buy_signal_strength != null ? (
                          <span className={`text-xs font-medium ${t.buy_signal_strength >= 0.7 ? 'text-bull' : t.buy_signal_strength >= 0.4 ? 'text-yellow-400' : 'text-gray-400'}`}>
                            {(t.buy_signal_strength * 100).toFixed(0)}
                          </span>
                        ) : '-'}
                      </td>
                      <td className="px-3 py-2 text-center">
                        <span className={`text-xs px-1.5 py-0.5 rounded ${env.color}`}>{env.text}</span>
                      </td>
                      <td className="px-3 py-2 text-gray-300">{t.sell_date}</td>
                      <td className="px-3 py-2 text-right text-gray-300">{t.sell_price?.toFixed(2)}</td>
                      <td className="px-3 py-2 text-center">
                        <span className={`text-xs px-1.5 py-0.5 rounded ${reason.color}`}>{reason.text}</span>
                      </td>
                      <td className="px-3 py-2 text-right text-gray-400">{t.holding_days ?? '-'}</td>
                      <td className={`px-3 py-2 text-right font-medium ${pnlColor(t.pnl_pct)}`}>
                        {formatPct(t.pnl_pct)}
                      </td>
                      <td className={`px-3 py-2 text-right ${pnlColor(t.benchmark_return_pct)}`}>
                        {formatPct(t.benchmark_return_pct)}
                      </td>
                    </tr>
                  );
                })
              )}
            </tbody>
          </table>
        </div>
      </div>

      {/* 数据卡片 — 移动端 */}
      <div className="md:hidden space-y-2">
        {loading ? (
          <div className="text-center py-8 text-gray-500">加载中...</div>
        ) : trades.length === 0 ? (
          <div className="text-center py-8 text-gray-500">
            暂无已平仓记录，点击"重建"生成历史数据
          </div>
        ) : (
          trades.map(t => {
            const env = ENV_LABELS[t.market_env || 'unknown'] || ENV_LABELS.unknown;
            const reason = REASON_LABELS[t.sell_reason || 'manual_close'] || REASON_LABELS.manual_close;
            return (
              <div key={t.id} className="bg-card-dark rounded-lg p-3">
                <div className="flex justify-between items-start mb-2">
                  <div>
                    <span className="font-medium text-white">{t.symbol}</span>
                    <span className="text-xs text-gray-500 ml-1">{t.name}</span>
                  </div>
                  <span className={`text-sm font-bold ${pnlColor(t.pnl_pct)}`}>{formatPct(t.pnl_pct)}</span>
                </div>
                <div className="grid grid-cols-2 gap-x-4 gap-y-1 text-xs">
                  <div className="text-gray-400">买入: {t.buy_date} @ {t.buy_price?.toFixed(2)}</div>
                  <div className="text-gray-400">卖出: {t.sell_date} @ {t.sell_price?.toFixed(2)}</div>
                  <div className="text-gray-400">持仓: {t.holding_days ?? '-'} 天</div>
                  <div className="text-gray-400">盈亏: <span className={pnlColor(t.pnl)}>{formatMoney(t.pnl)}</span></div>
                  <div className="flex gap-1 items-center">
                    <span className={`px-1.5 py-0.5 rounded ${env.color}`}>{env.text}</span>
                    <span className={`px-1.5 py-0.5 rounded ${reason.color}`}>{reason.text}</span>
                  </div>
                  <div className="text-gray-400">
                    沪深300: <span className={pnlColor(t.benchmark_return_pct)}>{formatPct(t.benchmark_return_pct)}</span>
                  </div>
                </div>
              </div>
            );
          })
        )}
      </div>

      {/* 分页 */}
      {totalPages > 1 && (
        <div className="flex justify-center items-center gap-2 text-sm">
          <button
            onClick={() => { setPage(p => Math.max(1, p - 1)); loadData(Math.max(1, page - 1)); }}
            disabled={page <= 1}
            className="px-3 py-1 rounded bg-gray-800 text-gray-300 disabled:opacity-40 hover:bg-gray-700"
          >
            上一页
          </button>
          <span className="text-gray-400">
            {page} / {totalPages} （共 {total} 条）
          </span>
          <button
            onClick={() => { setPage(p => Math.min(totalPages, p + 1)); loadData(Math.min(totalPages, page + 1)); }}
            disabled={page >= totalPages}
            className="px-3 py-1 rounded bg-gray-800 text-gray-300 disabled:opacity-40 hover:bg-gray-700"
          >
            下一页
          </button>
        </div>
      )}
    </div>
  );
};

// ==================== 统计卡片子组件 ====================

const StatCard: React.FC<{
  title: string;
  value: string;
  valueColor?: string;
  sub?: string;
  subColor?: string;
}> = ({ title, value, valueColor, sub, subColor }) => (
  <div className="bg-card-dark rounded-lg p-3">
    <div className="text-xs text-gray-400 mb-1">{title}</div>
    <div className={`text-lg font-bold ${valueColor || 'text-white'}`}>{value}</div>
    {sub && <div className={`text-xs mt-0.5 ${subColor || 'text-gray-400'}`}>{sub}</div>}
  </div>
);

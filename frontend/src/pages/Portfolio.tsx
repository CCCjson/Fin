import React, { useState, useEffect, useCallback } from 'react';
import { StockSymbolInput } from '../components/common/StockSymbolInput';
import { portfolioService } from '../services/portfolioService';
import { reportService } from '../services/reportService';
import type {
  ManualTrade,
  PortfolioPosition,
  PortfolioStats,
  ReportRecommendation,
  TradeCreateParams,
} from '../services/portfolioService';
import type { ReportSummary } from '../services/reportService';

// ==================== 辅助函数 ====================

const todayStr = () => new Date().toISOString().slice(0, 10);

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

/** A 股佣金自动计算 */
const calcCommission = (side: 'BUY' | 'SELL', amount: number) => {
  const base = Math.max(amount * 0.00025, 5); // 万2.5，最低5元
  if (side === 'SELL') {
    return parseFloat((base + amount * 0.001).toFixed(2)); // + 千1 印花税
  }
  return parseFloat(base.toFixed(2));
};

// ==================== 组件 ====================

export const Portfolio: React.FC = () => {
  // --- data ---
  const [stats, setStats] = useState<PortfolioStats | null>(null);
  const [positions, setPositions] = useState<PortfolioPosition[]>([]);
  const [trades, setTrades] = useState<ManualTrade[]>([]);
  const [totalTrades, setTotalTrades] = useState(0);
  const [loading, setLoading] = useState(false);

  // --- filters ---
  const [filterSymbol, setFilterSymbol] = useState('');
  const [filterSide, setFilterSide] = useState('');
  const [filterStartDate, setFilterStartDate] = useState('');
  const [filterEndDate, setFilterEndDate] = useState('');
  const [page, setPage] = useState(1);
  const pageSize = 50;

  // --- trade modal ---
  const [showTradeModal, setShowTradeModal] = useState(false);
  const [editingTrade, setEditingTrade] = useState<ManualTrade | null>(null);
  const [tradeForm, setTradeForm] = useState({
    symbol: '',
    name: '',
    side: 'BUY' as 'BUY' | 'SELL',
    price: '',
    quantity: '',
    commission: '',
    trade_date: todayStr(),
    note: '',
    autoCommission: true,
  });
  const [submitting, setSubmitting] = useState(false);

  // --- total capital ---
  const [totalCapital, setTotalCapital] = useState<number>(200000);
  const [editingCapital, setEditingCapital] = useState(false);
  const [capitalInput, setCapitalInput] = useState('');

  // --- import modal ---
  const [showImportModal, setShowImportModal] = useState(false);
  const [reports, setReports] = useState<ReportSummary[]>([]);
  const [selectedReportId, setSelectedReportId] = useState('');
  const [recommendations, setRecommendations] = useState<ReportRecommendation[]>([]);
  const [selectedRecs, setSelectedRecs] = useState<Record<string, boolean>>({});
  const [importPrices, setImportPrices] = useState<Record<string, { price: string; quantity: string }>>({});
  const [importStep, setImportStep] = useState<1 | 2 | 3>(1);
  const [importing, setImporting] = useState(false);

  // --- load data ---
  const loadSettings = useCallback(async () => {
    try {
      const data = await portfolioService.getSettings();
      if (data.total_capital) {
        setTotalCapital(parseFloat(data.total_capital.value) || 200000);
      }
    } catch (e) {
      console.error('Failed to load settings:', e);
    }
  }, []);

  const loadStats = useCallback(async () => {
    try {
      const data = await portfolioService.getStats();
      setStats(data);
    } catch (e) {
      console.error('Failed to load stats:', e);
    }
  }, []);

  const loadPositions = useCallback(async () => {
    try {
      const data = await portfolioService.getPositions();
      setPositions(data.positions);
    } catch (e) {
      console.error('Failed to load positions:', e);
    }
  }, []);

  const loadTrades = useCallback(async (targetPage?: number) => {
    try {
      setLoading(true);
      const p = targetPage ?? page;
      const data = await portfolioService.getTrades({
        symbol: filterSymbol || undefined,
        side: filterSide || undefined,
        start_date: filterStartDate || undefined,
        end_date: filterEndDate || undefined,
        limit: pageSize,
        offset: (p - 1) * pageSize,
      });
      setTrades(data.trades);
      setTotalTrades(data.total);
    } catch (e) {
      console.error('Failed to load trades:', e);
    } finally {
      setLoading(false);
    }
  }, [page, filterSymbol, filterSide, filterStartDate, filterEndDate]);

  useEffect(() => {
    loadSettings();
    loadStats();
    loadPositions();
    loadTrades();
  }, []);

  const refreshAll = async () => {
    await Promise.all([loadStats(), loadPositions(), loadTrades(1)]);
    setPage(1);
  };

  const handleSaveCapital = async () => {
    const val = parseFloat(capitalInput);
    if (isNaN(val) || val <= 0) return;
    try {
      await portfolioService.updateSetting('total_capital', String(val));
      setTotalCapital(val);
      setEditingCapital(false);
    } catch (e) {
      console.error('Failed to update total_capital:', e);
      alert('保存失败，请重试');
    }
  };

  // --- trade modal logic ---
  const openTradeModal = (prefill?: { symbol?: string; name?: string; side?: 'BUY' | 'SELL' }) => {
    setEditingTrade(null);
    setTradeForm({
      symbol: prefill?.symbol || '',
      name: prefill?.name || '',
      side: prefill?.side || 'BUY',
      price: '',
      quantity: '',
      commission: '',
      trade_date: todayStr(),
      note: '',
      autoCommission: true,
    });
    setShowTradeModal(true);
  };

  const openEditModal = (trade: ManualTrade) => {
    setEditingTrade(trade);
    setTradeForm({
      symbol: trade.symbol,
      name: trade.name || '',
      side: trade.side,
      price: String(trade.price),
      quantity: String(trade.quantity),
      commission: String(trade.commission),
      trade_date: trade.trade_date || todayStr(),
      note: trade.note || '',
      autoCommission: false,
    });
    setShowTradeModal(true);
  };

  const handleTradeFormChange = (field: string, value: string | boolean) => {
    setTradeForm(prev => {
      const next = { ...prev, [field]: value };
      // auto calc commission
      if (next.autoCommission && (field === 'price' || field === 'quantity' || field === 'side' || field === 'autoCommission')) {
        const p = parseFloat(next.price);
        const q = parseInt(next.quantity);
        if (!isNaN(p) && !isNaN(q) && p > 0 && q > 0) {
          next.commission = String(calcCommission(next.side, p * q));
        }
      }
      return next;
    });
  };

  const handleSubmitTrade = async () => {
    const price = parseFloat(tradeForm.price);
    const quantity = parseInt(tradeForm.quantity);
    if (isNaN(price) || isNaN(quantity) || !tradeForm.symbol || !tradeForm.trade_date) return;

    setSubmitting(true);
    try {
      if (editingTrade) {
        await portfolioService.updateTrade(editingTrade.id, {
          symbol: tradeForm.symbol,
          name: tradeForm.name || undefined,
          side: tradeForm.side,
          price,
          quantity,
          commission: parseFloat(tradeForm.commission) || 0,
          trade_date: tradeForm.trade_date,
          note: tradeForm.note || undefined,
        });
      } else {
        await portfolioService.createTrade({
          symbol: tradeForm.symbol,
          name: tradeForm.name || undefined,
          side: tradeForm.side,
          price,
          quantity,
          commission: parseFloat(tradeForm.commission) || 0,
          trade_date: tradeForm.trade_date,
          note: tradeForm.note || undefined,
        });
      }
      setShowTradeModal(false);
      await refreshAll();
    } catch (e) {
      console.error('Submit trade failed:', e);
      alert('提交失败，请重试');
    } finally {
      setSubmitting(false);
    }
  };

  const handleDeleteTrade = async (id: number) => {
    if (!confirm('确认删除这条交易记录？')) return;
    try {
      await portfolioService.deleteTrade(id);
      await refreshAll();
    } catch (e) {
      console.error('Delete trade failed:', e);
    }
  };

  // --- import modal logic ---
  const openImportModal = async () => {
    setImportStep(1);
    setSelectedReportId('');
    setRecommendations([]);
    setSelectedRecs({});
    setImportPrices({});
    setShowImportModal(true);
    try {
      const data = await reportService.getReports({ limit: 50 });
      setReports(data.reports.filter(r => r.status === 'completed'));
    } catch (e) {
      console.error('Failed to load reports:', e);
    }
  };

  const handleSelectReport = async (reportId: string) => {
    setSelectedReportId(reportId);
    if (!reportId) return;
    try {
      const data = await portfolioService.getReportRecommendations(reportId);
      setRecommendations(data.recommendations);
      setSelectedRecs({});
      setImportPrices({});
      setImportStep(2);
    } catch (e) {
      console.error('Failed to load recommendations:', e);
    }
  };

  const toggleRec = (symbol: string) => {
    setSelectedRecs(prev => ({ ...prev, [symbol]: !prev[symbol] }));
  };

  const goToStep3 = () => {
    const selected = recommendations.filter(r => selectedRecs[r.symbol]);
    if (selected.length === 0) return;
    const prices: Record<string, { price: string; quantity: string }> = {};
    for (const rec of selected) {
      prices[rec.symbol] = { price: String(rec.price || ''), quantity: '100' };
    }
    setImportPrices(prices);
    setImportStep(3);
  };

  const handleImport = async () => {
    setImporting(true);
    try {
      const selected = recommendations.filter(r => selectedRecs[r.symbol]);
      const tradeList: TradeCreateParams[] = selected.map(rec => {
        const { price: priceStr, quantity: qtyStr } = importPrices[rec.symbol] || {};
        const price = parseFloat(priceStr) || rec.price || 0;
        const quantity = parseInt(qtyStr) || 100;
        return {
          symbol: rec.symbol,
          name: rec.name,
          side: 'BUY' as const,
          price,
          quantity,
          commission: calcCommission('BUY', price * quantity),
          trade_date: todayStr(),
          ai_recommended_price: rec.price ?? undefined,
          ai_stop_loss: rec.stop_loss ?? undefined,
          ai_take_profit: rec.take_profit ?? undefined,
          ai_composite_score: rec.score ?? undefined,
          ai_strategy: rec.strategy ?? undefined,
        };
      });

      await portfolioService.importFromReport(selectedReportId, tradeList);
      setShowImportModal(false);
      await refreshAll();
    } catch (e) {
      console.error('Import failed:', e);
      alert('导入失败，请重试');
    } finally {
      setImporting(false);
    }
  };

  // --- pagination ---
  const totalPages = Math.max(1, Math.ceil(totalTrades / pageSize));
  const handlePageChange = (p: number) => {
    if (p < 1 || p > totalPages || p === page) return;
    setPage(p);
    loadTrades(p);
  };

  const handleSearch = () => {
    setPage(1);
    loadTrades(1);
  };

  // ==================== RENDER ====================

  const renderStatCard = (label: string, value: string | number, sub?: string, color?: string) => (
    <div className="bg-gradient-card p-6 rounded-xl border border-border shadow-card hover:shadow-glow-blue transition-all">
      <div className="text-gray-400 text-sm mb-2">{label}</div>
      <div className={`text-2xl font-bold ${color || 'text-primary-light'}`}>{value}</div>
      {sub && <div className="text-xs text-gray-500 mt-1">{sub}</div>}
    </div>
  );

  return (
    <div className="min-h-screen bg-gradient-dark p-6">
      <div className="max-w-7xl mx-auto space-y-6">
        {/* Header */}
        <div className="flex items-center justify-between">
          <h1 className="text-3xl font-bold text-white">交易记录</h1>
          <div className="flex items-center gap-3">
            <button
              onClick={openImportModal}
              className="px-5 py-2 bg-gradient-to-r from-amber-500 to-orange-600 text-white rounded-xl hover:from-amber-600 hover:to-orange-700 shadow-lg shadow-amber-500/25 transition-all flex items-center gap-2"
            >
              <svg className="h-5 w-5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M4 16v1a3 3 0 003 3h10a3 3 0 003-3v-1m-4-4l-4 4m0 0l-4-4m4 4V4" />
              </svg>
              从报告导入
            </button>
            <button
              onClick={() => openTradeModal()}
              className="px-5 py-2 bg-gradient-to-r from-primary to-blue-600 text-white rounded-xl hover:from-blue-600 hover:to-blue-700 shadow-lg shadow-primary/25 transition-all flex items-center gap-2"
            >
              <svg className="h-5 w-5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M12 4v16m8-8H4" />
              </svg>
              录入交易
            </button>
          </div>
        </div>

        {/* Stats Cards */}
        {stats && (
          <div className="grid grid-cols-1 md:grid-cols-5 gap-4">
            {/* 总资金卡片（可编辑） */}
            <div className="bg-gradient-card p-6 rounded-xl border border-border shadow-card hover:shadow-glow-blue transition-all">
              <div className="flex items-center justify-between mb-2">
                <span className="text-gray-400 text-sm">总资金</span>
                <button
                  onClick={() => {
                    setCapitalInput(String(totalCapital));
                    setEditingCapital(true);
                  }}
                  className="text-gray-500 hover:text-primary-light transition-colors"
                  title="修改总资金"
                >
                  <svg className="h-4 w-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                    <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M15.232 5.232l3.536 3.536m-2.036-5.036a2.5 2.5 0 113.536 3.536L6.5 21.036H3v-3.572L16.732 3.732z" />
                  </svg>
                </button>
              </div>
              {editingCapital ? (
                <div className="flex items-center gap-2">
                  <input
                    type="number"
                    value={capitalInput}
                    onChange={e => setCapitalInput(e.target.value)}
                    onKeyDown={e => e.key === 'Enter' && handleSaveCapital()}
                    className="w-full px-2 py-1 bg-dark-light text-white text-lg font-bold rounded-lg border border-primary focus:ring-2 focus:ring-primary/20 outline-none"
                    autoFocus
                  />
                  <button onClick={handleSaveCapital} className="text-bull hover:text-green-400 transition-colors" title="保存">
                    <svg className="h-5 w-5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                      <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M5 13l4 4L19 7" />
                    </svg>
                  </button>
                  <button onClick={() => setEditingCapital(false)} className="text-gray-500 hover:text-gray-300 transition-colors" title="取消">
                    <svg className="h-5 w-5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                      <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M6 18L18 6M6 6l12 12" />
                    </svg>
                  </button>
                </div>
              ) : (
                <div className="text-2xl font-bold text-primary-light">{formatMoney(totalCapital)}</div>
              )}
              <div className="text-xs text-gray-500 mt-1">
                仓位: {stats.current_value > 0 && totalCapital > 0
                  ? `${(stats.current_value / totalCapital * 100).toFixed(1)}%`
                  : '0%'}
                {' '}| 投入: {formatMoney(stats.total_cost_holding)}
              </div>
            </div>
            {renderStatCard(
              '当前市值',
              formatMoney(stats.current_value),
              `持仓成本: ${formatMoney(stats.total_cost_holding)}`,
            )}
            <div className="bg-gradient-card p-6 rounded-xl border border-border shadow-card hover:shadow-glow-blue transition-all">
              <div className="text-gray-400 text-sm mb-2">总盈亏</div>
              <div className={`text-2xl font-bold ${pnlColor(stats.total_pnl)}`}>
                {stats.total_pnl >= 0 ? '+' : ''}{formatMoney(stats.total_pnl)}
              </div>
              <div className={`text-sm font-medium mt-0.5 ${pnlColor(stats.total_pnl_pct)}`}>
                {formatPct(stats.total_pnl_pct)}
              </div>
              <div className="text-xs text-gray-500 mt-1">
                已实现: {formatMoney(stats.realized_pnl)} | 未实现: {formatMoney(stats.unrealized_pnl)}
              </div>
            </div>
            {renderStatCard(
              '胜率',
              stats.total_closed_trades > 0 ? `${stats.win_rate}%` : '-',
              stats.total_closed_trades > 0
                ? `${stats.win_count}胜 / ${stats.loss_count}负 | 盈亏比 ${stats.profit_loss_ratio}`
                : `共 ${stats.total_trades} 笔交易`,
              stats.win_rate >= 50 ? 'text-bull' : stats.total_closed_trades > 0 ? 'text-bear' : 'text-primary-light',
            )}
            {renderStatCard(
              '佣金累计',
              formatMoney(stats.total_commission),
              `共 ${stats.total_trades} 笔交易`,
            )}
          </div>
        )}

        {/* Current Positions */}
        <div className="bg-gradient-card border border-border shadow-card p-6 rounded-xl">
          <h2 className="text-xl font-semibold text-white mb-4">当前持仓</h2>
          <div className="overflow-x-auto rounded-lg border border-border">
            <table className="w-full text-sm">
              <thead className="bg-dark-light text-gray-300 border-b border-border">
                <tr>
                  <th className="px-4 py-3 text-left">股票</th>
                  <th className="px-4 py-3 text-right">持股</th>
                  <th className="px-4 py-3 text-right">均价</th>
                  <th className="px-4 py-3 text-right">现价</th>
                  <th className="px-4 py-3 text-right">市值</th>
                  <th className="px-4 py-3 text-right">成本</th>
                  <th className="px-4 py-3 text-right">浮盈亏</th>
                  <th className="px-4 py-3 text-right">浮盈亏%</th>
                  <th className="px-4 py-3 text-center">操作</th>
                </tr>
              </thead>
              <tbody className="text-gray-300">
                {positions.length === 0 ? (
                  <tr>
                    <td colSpan={9} className="px-4 py-8 text-center text-gray-500">
                      暂无持仓
                    </td>
                  </tr>
                ) : (
                  positions.map(pos => (
                    <tr key={pos.symbol} className="border-b border-border hover:bg-dark-light transition-colors">
                      <td className="px-4 py-3">
                        <div className="font-medium text-white">{pos.name}</div>
                        <div className="text-xs text-gray-500">{pos.symbol}</div>
                      </td>
                      <td className="px-4 py-3 text-right">{pos.quantity.toLocaleString()}</td>
                      <td className="px-4 py-3 text-right">{pos.avg_cost.toFixed(2)}</td>
                      <td className="px-4 py-3 text-right text-primary-light">
                        {pos.current_price !== null ? pos.current_price.toFixed(2) : '-'}
                      </td>
                      <td className="px-4 py-3 text-right">{formatMoney(pos.market_value)}</td>
                      <td className="px-4 py-3 text-right">{formatMoney(pos.total_cost)}</td>
                      <td className={`px-4 py-3 text-right font-medium ${pnlColor(pos.unrealized_pnl)}`}>
                        {pos.unrealized_pnl !== null ? `${pos.unrealized_pnl >= 0 ? '+' : ''}${formatMoney(pos.unrealized_pnl)}` : '-'}
                      </td>
                      <td className={`px-4 py-3 text-right font-medium ${pnlColor(pos.unrealized_pnl_pct)}`}>
                        {formatPct(pos.unrealized_pnl_pct)}
                      </td>
                      <td className="px-4 py-3 text-center">
                        <button
                          onClick={() => openTradeModal({ symbol: pos.symbol, name: pos.name, side: 'SELL' })}
                          className="px-3 py-1 text-xs bg-bear/20 text-bear border border-bear/30 rounded-lg hover:bg-bear/30 transition-all"
                        >
                          卖出
                        </button>
                      </td>
                    </tr>
                  ))
                )}
              </tbody>
            </table>
          </div>
        </div>

        {/* Filters */}
        <div className="bg-gradient-card border border-border shadow-card p-6 rounded-xl">
          <div className="grid grid-cols-1 md:grid-cols-5 gap-4">
            <div>
              <label className="block text-sm font-medium text-gray-300 mb-2">股票代码</label>
              <StockSymbolInput
                value={filterSymbol}
                onChange={(symbol) => setFilterSymbol(symbol)}
                placeholder="如 600519.SH"
              />
            </div>
            <div>
              <label className="block text-sm font-medium text-gray-300 mb-2">方向</label>
              <select
                value={filterSide}
                onChange={e => setFilterSide(e.target.value)}
                className="w-full px-3 py-2 bg-dark-light text-white rounded-lg border border-border focus:border-primary focus:ring-2 focus:ring-primary/20 outline-none transition-all"
              >
                <option value="">全部</option>
                <option value="BUY">买入</option>
                <option value="SELL">卖出</option>
              </select>
            </div>
            <div>
              <label className="block text-sm font-medium text-gray-300 mb-2">开始日期</label>
              <input
                type="date"
                value={filterStartDate}
                onChange={e => setFilterStartDate(e.target.value)}
                className="w-full px-3 py-2 bg-dark-light text-white rounded-lg border border-border focus:border-primary focus:ring-2 focus:ring-primary/20 outline-none transition-all"
              />
            </div>
            <div>
              <label className="block text-sm font-medium text-gray-300 mb-2">结束日期</label>
              <input
                type="date"
                value={filterEndDate}
                onChange={e => setFilterEndDate(e.target.value)}
                className="w-full px-3 py-2 bg-dark-light text-white rounded-lg border border-border focus:border-primary focus:ring-2 focus:ring-primary/20 outline-none transition-all"
              />
            </div>
            <div className="flex items-end">
              <button
                onClick={handleSearch}
                disabled={loading}
                className="w-full px-4 py-2 bg-primary text-white rounded-xl hover:bg-primary-dark shadow-glow-blue transition-all disabled:opacity-50"
              >
                {loading ? '查询中...' : '查询'}
              </button>
            </div>
          </div>
        </div>

        {/* Trade History Table */}
        <div className="bg-gradient-card border border-border shadow-card p-6 rounded-xl">
          <div className="flex items-center justify-between mb-4">
            <h2 className="text-xl font-semibold text-white">交易记录</h2>
            {totalTrades > 0 && (
              <span className="text-sm text-gray-400">
                共 <span className="text-white font-medium">{totalTrades}</span> 条
              </span>
            )}
          </div>
          <div className="overflow-x-auto rounded-lg border border-border">
            <table className="w-full text-sm">
              <thead className="bg-dark-light text-gray-300 border-b border-border">
                <tr>
                  <th className="px-3 py-3 text-left">日期</th>
                  <th className="px-3 py-3 text-left">股票</th>
                  <th className="px-3 py-3 text-center">方向</th>
                  <th className="px-3 py-3 text-right">价格</th>
                  <th className="px-3 py-3 text-right">数量</th>
                  <th className="px-3 py-3 text-right">金额</th>
                  <th className="px-3 py-3 text-right">佣金</th>
                  <th className="px-3 py-3 text-left">备注</th>
                  <th className="px-3 py-3 text-right">AI价</th>
                  <th className="px-3 py-3 text-right">差价</th>
                  <th className="px-3 py-3 text-center">AI评分</th>
                  <th className="px-3 py-3 text-center">操作</th>
                </tr>
              </thead>
              <tbody className="text-gray-300">
                {trades.length === 0 ? (
                  <tr>
                    <td colSpan={12} className="px-4 py-8 text-center text-gray-500">
                      {loading ? '加载中...' : '暂无交易记录'}
                    </td>
                  </tr>
                ) : (
                  trades.map(t => {
                    // 差价：买入时实际价-AI价（正=买贵了），卖出时AI价-实际价（正=卖便宜了）
                    const priceDiff = t.ai_recommended_price != null
                      ? (t.side === 'BUY'
                          ? t.price - t.ai_recommended_price
                          : t.ai_recommended_price - t.price)
                      : null;
                    return (
                      <tr key={t.id} className="border-b border-border hover:bg-dark-light transition-colors">
                        <td className="px-3 py-3 whitespace-nowrap">{t.trade_date}</td>
                        <td className="px-3 py-3">
                          <div className="font-medium text-white">{t.name || t.symbol}</div>
                          {t.name && <div className="text-xs text-gray-500">{t.symbol}</div>}
                        </td>
                        <td className="px-3 py-3 text-center">
                          <span className={`px-2 py-0.5 rounded-lg text-xs font-semibold ${
                            t.side === 'BUY'
                              ? 'bg-bull/20 text-bull border border-bull/30'
                              : 'bg-bear/20 text-bear border border-bear/30'
                          }`}>
                            {t.side === 'BUY' ? '买入' : '卖出'}
                          </span>
                        </td>
                        <td className="px-3 py-3 text-right">{t.price.toFixed(2)}</td>
                        <td className="px-3 py-3 text-right">{t.quantity.toLocaleString()}</td>
                        <td className="px-3 py-3 text-right">{formatMoney(t.amount)}</td>
                        <td className="px-3 py-3 text-right text-gray-400">{t.commission > 0 ? formatMoney(t.commission) : '-'}</td>
                        <td className="px-3 py-3 text-gray-400 max-w-[120px] truncate" title={t.note || ''}>
                          {t.note || '-'}
                        </td>
                        <td className="px-3 py-3 text-right text-accent-cyan">
                          {t.ai_recommended_price != null ? t.ai_recommended_price.toFixed(2) : '-'}
                        </td>
                        <td className={`px-3 py-3 text-right font-medium ${
                          priceDiff === null ? 'text-gray-500'
                            : priceDiff < 0 ? 'text-bull'
                            : priceDiff > 0 ? 'text-bear'
                            : 'text-gray-400'
                        }`}>
                          {priceDiff !== null ? `${priceDiff >= 0 ? '+' : ''}${priceDiff.toFixed(2)}` : '-'}
                        </td>
                        <td className="px-3 py-3 text-center">
                          {t.ai_composite_score != null ? (
                            <span className="text-accent-purple font-medium">{t.ai_composite_score.toFixed(1)}</span>
                          ) : '-'}
                        </td>
                        <td className="px-3 py-3 text-center">
                          <div className="flex items-center justify-center gap-1">
                            <button
                              onClick={() => openEditModal(t)}
                              className="px-2 py-1 text-xs bg-dark-light text-gray-300 border border-border rounded-lg hover:bg-border transition-all"
                            >
                              编辑
                            </button>
                            <button
                              onClick={() => handleDeleteTrade(t.id)}
                              className="px-2 py-1 text-xs bg-red-500/10 text-red-400 border border-red-500/20 rounded-lg hover:bg-red-500/20 transition-all"
                            >
                              删除
                            </button>
                          </div>
                        </td>
                      </tr>
                    );
                  })
                )}
              </tbody>
            </table>
          </div>

          {/* Pagination */}
          {totalTrades > pageSize && (
            <div className="flex items-center justify-between mt-4 pt-4 border-t border-border">
              <span className="text-sm text-gray-400">
                第 {(page - 1) * pageSize + 1}-{Math.min(page * pageSize, totalTrades)} 条，共 {totalTrades} 条
              </span>
              <div className="flex items-center gap-2">
                <button onClick={() => handlePageChange(1)} disabled={page === 1 || loading} className="px-3 py-1.5 text-sm bg-dark-light text-gray-300 rounded-lg border border-border hover:bg-border disabled:opacity-30 disabled:cursor-not-allowed transition-all">首页</button>
                <button onClick={() => handlePageChange(page - 1)} disabled={page === 1 || loading} className="px-3 py-1.5 text-sm bg-dark-light text-gray-300 rounded-lg border border-border hover:bg-border disabled:opacity-30 disabled:cursor-not-allowed transition-all">上一页</button>
                {(() => {
                  const pages: number[] = [];
                  let start = Math.max(1, page - 2);
                  let end = Math.min(totalPages, page + 2);
                  if (end - start < 4) {
                    if (start === 1) end = Math.min(totalPages, start + 4);
                    else start = Math.max(1, end - 4);
                  }
                  for (let i = start; i <= end; i++) pages.push(i);
                  return pages.map(p => (
                    <button
                      key={p}
                      onClick={() => handlePageChange(p)}
                      disabled={loading}
                      className={`px-3 py-1.5 text-sm rounded-lg border transition-all ${
                        p === page
                          ? 'bg-primary text-white border-primary shadow-glow-blue'
                          : 'bg-dark-light text-gray-300 border-border hover:bg-border'
                      } disabled:cursor-not-allowed`}
                    >
                      {p}
                    </button>
                  ));
                })()}
                <button onClick={() => handlePageChange(page + 1)} disabled={page === totalPages || loading} className="px-3 py-1.5 text-sm bg-dark-light text-gray-300 rounded-lg border border-border hover:bg-border disabled:opacity-30 disabled:cursor-not-allowed transition-all">下一页</button>
                <button onClick={() => handlePageChange(totalPages)} disabled={page === totalPages || loading} className="px-3 py-1.5 text-sm bg-dark-light text-gray-300 rounded-lg border border-border hover:bg-border disabled:opacity-30 disabled:cursor-not-allowed transition-all">末页</button>
              </div>
            </div>
          )}
        </div>
      </div>

      {/* ==================== Trade Modal ==================== */}
      {showTradeModal && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/60 backdrop-blur-sm">
          <div className="bg-dark-card border border-border rounded-2xl shadow-2xl w-full max-w-lg mx-4 p-6">
            <div className="flex items-center justify-between mb-6">
              <h3 className="text-xl font-bold text-white">
                {editingTrade ? '编辑交易' : '录入交易'}
              </h3>
              <button onClick={() => setShowTradeModal(false)} className="text-gray-400 hover:text-white transition-colors">
                <svg className="h-6 w-6" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                  <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M6 18L18 6M6 6l12 12" />
                </svg>
              </button>
            </div>

            <div className="space-y-4">
              {/* Side Toggle */}
              <div className="flex gap-2">
                <button
                  onClick={() => handleTradeFormChange('side', 'BUY')}
                  className={`flex-1 py-2.5 rounded-xl font-semibold text-sm transition-all ${
                    tradeForm.side === 'BUY'
                      ? 'bg-bull text-white shadow-lg shadow-bull/25'
                      : 'bg-dark-light text-gray-400 border border-border hover:text-white'
                  }`}
                >
                  买入
                </button>
                <button
                  onClick={() => handleTradeFormChange('side', 'SELL')}
                  className={`flex-1 py-2.5 rounded-xl font-semibold text-sm transition-all ${
                    tradeForm.side === 'SELL'
                      ? 'bg-bear text-white shadow-lg shadow-bear/25'
                      : 'bg-dark-light text-gray-400 border border-border hover:text-white'
                  }`}
                >
                  卖出
                </button>
              </div>

              {/* Symbol + Name */}
              <div className="grid grid-cols-2 gap-3">
                <div>
                  <label className="block text-sm text-gray-300 mb-1">股票代码</label>
                  <StockSymbolInput
                    value={tradeForm.symbol}
                    onChange={(symbol, name) => {
                      handleTradeFormChange('symbol', symbol);
                      if (name) handleTradeFormChange('name', name);
                    }}
                    placeholder="输入代码或名称搜索"
                  />
                </div>
                <div>
                  <label className="block text-sm text-gray-300 mb-1">股票名称</label>
                  <input
                    type="text"
                    value={tradeForm.name}
                    onChange={e => handleTradeFormChange('name', e.target.value)}
                    placeholder="自动填充"
                    className="w-full px-3 py-2 bg-dark-light text-white rounded-lg border border-border focus:border-primary focus:ring-2 focus:ring-primary/20 outline-none transition-all"
                  />
                </div>
              </div>

              {/* Price + Quantity */}
              <div className="grid grid-cols-2 gap-3">
                <div>
                  <label className="block text-sm text-gray-300 mb-1">成交价</label>
                  <input
                    type="number"
                    step="0.01"
                    value={tradeForm.price}
                    onChange={e => handleTradeFormChange('price', e.target.value)}
                    placeholder="0.00"
                    className="w-full px-3 py-2 bg-dark-light text-white rounded-lg border border-border focus:border-primary focus:ring-2 focus:ring-primary/20 outline-none transition-all"
                  />
                </div>
                <div>
                  <label className="block text-sm text-gray-300 mb-1">数量（股）</label>
                  <input
                    type="number"
                    step="100"
                    value={tradeForm.quantity}
                    onChange={e => handleTradeFormChange('quantity', e.target.value)}
                    placeholder="100"
                    className="w-full px-3 py-2 bg-dark-light text-white rounded-lg border border-border focus:border-primary focus:ring-2 focus:ring-primary/20 outline-none transition-all"
                  />
                </div>
              </div>

              {/* Amount display */}
              {tradeForm.price && tradeForm.quantity && (
                <div className="text-sm text-gray-400">
                  成交金额: <span className="text-white font-medium">
                    {formatMoney(parseFloat(tradeForm.price) * parseInt(tradeForm.quantity) || 0)}
                  </span>
                </div>
              )}

              {/* Commission */}
              <div>
                <div className="flex items-center justify-between mb-1">
                  <label className="text-sm text-gray-300">手续费</label>
                  <label className="flex items-center gap-1.5 text-xs text-gray-400 cursor-pointer">
                    <input
                      type="checkbox"
                      checked={tradeForm.autoCommission}
                      onChange={e => handleTradeFormChange('autoCommission', e.target.checked)}
                      className="rounded border-border bg-dark-light"
                    />
                    自动计算
                  </label>
                </div>
                <input
                  type="number"
                  step="0.01"
                  value={tradeForm.commission}
                  onChange={e => {
                    setTradeForm(prev => ({ ...prev, commission: e.target.value, autoCommission: false }));
                  }}
                  placeholder="0.00"
                  className="w-full px-3 py-2 bg-dark-light text-white rounded-lg border border-border focus:border-primary focus:ring-2 focus:ring-primary/20 outline-none transition-all"
                />
              </div>

              {/* Date + Note */}
              <div className="grid grid-cols-2 gap-3">
                <div>
                  <label className="block text-sm text-gray-300 mb-1">成交日期</label>
                  <input
                    type="date"
                    value={tradeForm.trade_date}
                    onChange={e => handleTradeFormChange('trade_date', e.target.value)}
                    className="w-full px-3 py-2 bg-dark-light text-white rounded-lg border border-border focus:border-primary focus:ring-2 focus:ring-primary/20 outline-none transition-all"
                  />
                </div>
                <div>
                  <label className="block text-sm text-gray-300 mb-1">备注</label>
                  <input
                    type="text"
                    value={tradeForm.note}
                    onChange={e => handleTradeFormChange('note', e.target.value)}
                    placeholder="可选"
                    className="w-full px-3 py-2 bg-dark-light text-white rounded-lg border border-border focus:border-primary focus:ring-2 focus:ring-primary/20 outline-none transition-all"
                  />
                </div>
              </div>

              {/* Submit */}
              <button
                onClick={handleSubmitTrade}
                disabled={submitting || !tradeForm.symbol || !tradeForm.price || !tradeForm.quantity}
                className={`w-full py-3 rounded-xl font-semibold text-white transition-all disabled:opacity-50 disabled:cursor-not-allowed ${
                  tradeForm.side === 'BUY'
                    ? 'bg-gradient-to-r from-bull to-green-600 shadow-lg shadow-bull/25 hover:from-green-600 hover:to-green-700'
                    : 'bg-gradient-to-r from-bear to-red-600 shadow-lg shadow-bear/25 hover:from-red-600 hover:to-red-700'
                }`}
              >
                {submitting ? '提交中...' : editingTrade ? '保存修改' : `确认${tradeForm.side === 'BUY' ? '买入' : '卖出'}`}
              </button>
            </div>
          </div>
        </div>
      )}

      {/* ==================== Import Modal ==================== */}
      {showImportModal && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/60 backdrop-blur-sm">
          <div className="bg-dark-card border border-border rounded-2xl shadow-2xl w-full max-w-2xl mx-4 p-6 max-h-[85vh] overflow-y-auto">
            <div className="flex items-center justify-between mb-6">
              <h3 className="text-xl font-bold text-white">
                从 AI 报告导入交易
                <span className="text-sm text-gray-400 ml-2">步骤 {importStep}/3</span>
              </h3>
              <button onClick={() => setShowImportModal(false)} className="text-gray-400 hover:text-white transition-colors">
                <svg className="h-6 w-6" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                  <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M6 18L18 6M6 6l12 12" />
                </svg>
              </button>
            </div>

            {/* Step 1: Select Report */}
            {importStep === 1 && (
              <div className="space-y-4">
                <label className="block text-sm font-medium text-gray-300 mb-2">选择报告</label>
                {reports.length === 0 ? (
                  <div className="text-gray-500 text-center py-8">暂无已完成的报告</div>
                ) : (
                  <div className="space-y-2 max-h-[50vh] overflow-y-auto">
                    {reports.map(r => (
                      <button
                        key={r.report_id}
                        onClick={() => handleSelectReport(r.report_id)}
                        className={`w-full text-left p-4 rounded-xl border transition-all ${
                          selectedReportId === r.report_id
                            ? 'border-primary bg-primary/10'
                            : 'border-border bg-dark-light hover:border-gray-600'
                        }`}
                      >
                        <div className="font-medium text-white text-sm">{r.title}</div>
                        <div className="text-xs text-gray-400 mt-1">
                          {r.report_type === 'daily' ? '日报' : r.report_type === 'weekly' ? '周报' : '月报'} | {r.created_at ? new Date(r.created_at).toLocaleDateString('zh-CN') : '-'}
                        </div>
                      </button>
                    ))}
                  </div>
                )}
              </div>
            )}

            {/* Step 2: Select Recommendations */}
            {importStep === 2 && (
              <div className="space-y-4">
                <div className="text-sm text-gray-400 mb-2">
                  勾选要导入的推荐标的（共 {recommendations.length} 只）
                </div>
                {recommendations.length === 0 ? (
                  <div className="text-gray-500 text-center py-8">该报告无推荐标的数据</div>
                ) : (
                  <div className="space-y-2 max-h-[50vh] overflow-y-auto">
                    {recommendations.map(rec => (
                      <label
                        key={rec.symbol}
                        className={`flex items-center gap-4 p-4 rounded-xl border cursor-pointer transition-all ${
                          selectedRecs[rec.symbol]
                            ? 'border-primary bg-primary/10'
                            : 'border-border bg-dark-light hover:border-gray-600'
                        }`}
                      >
                        <input
                          type="checkbox"
                          checked={!!selectedRecs[rec.symbol]}
                          onChange={() => toggleRec(rec.symbol)}
                          className="rounded border-border bg-dark-light"
                        />
                        <div className="flex-1 min-w-0">
                          <div className="flex items-center gap-2">
                            <span className="font-medium text-white">{rec.name}</span>
                            <span className="text-xs text-gray-500">{rec.symbol}</span>
                          </div>
                          <div className="flex items-center gap-4 mt-1 text-xs text-gray-400">
                            {rec.score && <span>评分: <span className="text-accent-purple font-medium">{rec.score}</span></span>}
                            {rec.strategy && <span>策略: <span className="text-accent-cyan">{rec.strategy}</span></span>}
                            {rec.price && <span>推荐价: <span className="text-primary-light">{rec.price}</span></span>}
                            {rec.stop_loss && <span>止损: <span className="text-bear">{rec.stop_loss}</span></span>}
                            {rec.take_profit && <span>止盈: <span className="text-bull">{rec.take_profit}</span></span>}
                          </div>
                        </div>
                      </label>
                    ))}
                  </div>
                )}
                <div className="flex gap-3 pt-2">
                  <button
                    onClick={() => setImportStep(1)}
                    className="px-4 py-2 bg-dark-light text-gray-300 border border-border rounded-xl hover:bg-border transition-all"
                  >
                    上一步
                  </button>
                  <button
                    onClick={goToStep3}
                    disabled={Object.values(selectedRecs).filter(Boolean).length === 0}
                    className="flex-1 py-2 bg-primary text-white rounded-xl hover:bg-primary-dark shadow-glow-blue transition-all disabled:opacity-50 disabled:cursor-not-allowed"
                  >
                    下一步 ({Object.values(selectedRecs).filter(Boolean).length} 只已选)
                  </button>
                </div>
              </div>
            )}

            {/* Step 3: Fill prices & quantities */}
            {importStep === 3 && (
              <div className="space-y-4">
                <div className="text-sm text-gray-400 mb-2">填入实际买入价格和数量</div>
                <div className="space-y-3 max-h-[50vh] overflow-y-auto">
                  {recommendations.filter(r => selectedRecs[r.symbol]).map(rec => (
                    <div key={rec.symbol} className="p-4 rounded-xl border border-border bg-dark-light">
                      <div className="flex items-center gap-2 mb-3">
                        <span className="font-medium text-white">{rec.name}</span>
                        <span className="text-xs text-gray-500">{rec.symbol}</span>
                        {rec.price && (
                          <span className="text-xs text-accent-cyan ml-auto">AI推荐价: {rec.price}</span>
                        )}
                      </div>
                      <div className="grid grid-cols-2 gap-3">
                        <div>
                          <label className="text-xs text-gray-400 mb-1 block">买入价</label>
                          <input
                            type="number"
                            step="0.01"
                            value={importPrices[rec.symbol]?.price || ''}
                            onChange={e => setImportPrices(prev => ({
                              ...prev,
                              [rec.symbol]: { ...prev[rec.symbol], price: e.target.value },
                            }))}
                            className="w-full px-3 py-2 bg-dark text-white rounded-lg border border-border focus:border-primary focus:ring-2 focus:ring-primary/20 outline-none transition-all text-sm"
                          />
                        </div>
                        <div>
                          <label className="text-xs text-gray-400 mb-1 block">数量（股）</label>
                          <input
                            type="number"
                            step="100"
                            value={importPrices[rec.symbol]?.quantity || ''}
                            onChange={e => setImportPrices(prev => ({
                              ...prev,
                              [rec.symbol]: { ...prev[rec.symbol], quantity: e.target.value },
                            }))}
                            className="w-full px-3 py-2 bg-dark text-white rounded-lg border border-border focus:border-primary focus:ring-2 focus:ring-primary/20 outline-none transition-all text-sm"
                          />
                        </div>
                      </div>
                    </div>
                  ))}
                </div>
                <div className="flex gap-3 pt-2">
                  <button
                    onClick={() => setImportStep(2)}
                    className="px-4 py-2 bg-dark-light text-gray-300 border border-border rounded-xl hover:bg-border transition-all"
                  >
                    上一步
                  </button>
                  <button
                    onClick={handleImport}
                    disabled={importing}
                    className="flex-1 py-2 bg-gradient-to-r from-amber-500 to-orange-600 text-white rounded-xl hover:from-amber-600 hover:to-orange-700 shadow-lg shadow-amber-500/25 transition-all disabled:opacity-50 disabled:cursor-not-allowed"
                  >
                    {importing ? '导入中...' : '确认导入'}
                  </button>
                </div>
              </div>
            )}
          </div>
        </div>
      )}
    </div>
  );
};

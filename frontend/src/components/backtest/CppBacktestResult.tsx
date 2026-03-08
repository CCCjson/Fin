import React from 'react';
import { MetricCard } from './MetricCard';
import { EquityCurveChart } from './EquityCurveChart';
import { TradeTable } from './TradeTable';

interface CppBacktestResultProps {
  result: any;
}

export const CppBacktestResult: React.FC<CppBacktestResultProps> = ({ result }) => {
  if (!result) return null;

  const m = result.metrics;
  if (!m) return null;

  // run_and_save 已将 C++ 返回的小数转换为百分比，直接使用即可
  const totalReturnPct = m.total_return_pct ?? 0;
  const annualReturn = m.annual_return ?? 0;
  const maxDrawdownPct = m.max_drawdown_pct ?? 0;
  const winRate = m.win_rate ?? 0;
  const volatility = m.volatility != null ? (m.volatility * 100) : 0;

  const benchmarkReturnPct = m.benchmark_return_pct ?? null;
  const excessReturnPct = m.excess_return_pct ?? null;

  const equityCurve = result.daily_records || result.equity_curve || [];
  const trades = result.trade_records || result.trades || [];
  const benchmark = result.benchmark || null;

  return (
    <div className="space-y-4 md:space-y-6">
      {/* 指标卡片 */}
      <div className="bg-gradient-card border border-border shadow-card p-3 md:p-6 rounded-xl">
        <h2 className="text-lg md:text-xl font-semibold text-white mb-4">
          回测结果 {result.strategy_name ? `- ${result.strategy_name}` : ''} {result.symbol ? `/ ${result.symbol}` : ''}
        </h2>

        {/* 收益类 */}
        <div className="grid grid-cols-2 md:grid-cols-5 gap-3">
          <MetricCard label="总收益率" value={`${totalReturnPct.toFixed(2)}%`}
            type="return" positive={totalReturnPct >= 0} />
          <MetricCard label="年化收益" value={`${annualReturn.toFixed(2)}%`}
            type="return" positive={annualReturn >= 0} />
          {benchmarkReturnPct !== null && (
            <MetricCard label={`基准收益 (${benchmark?.benchmark_name || '基准'})`}
              value={`${benchmarkReturnPct.toFixed(2)}%`}
              type="neutral" />
          )}
          {excessReturnPct !== null && (
            <MetricCard label="超额收益"
              value={`${excessReturnPct >= 0 ? '+' : ''}${excessReturnPct.toFixed(2)}%`}
              type="return" positive={excessReturnPct >= 0} />
          )}
          <MetricCard label="最终资产"
            value={`${(m.final_value || 0).toLocaleString('zh-CN', { maximumFractionDigits: 0 })}`}
            type="neutral" />
        </div>

        {/* 风险类 + 质量类 */}
        <div className="grid grid-cols-2 md:grid-cols-5 gap-3 mt-3">
          <MetricCard label="最大回撤" value={`${maxDrawdownPct.toFixed(2)}%`} type="risk" />
          <MetricCard label="波动率" value={`${volatility.toFixed(2)}%`} type="risk" />
          <MetricCard label="夏普比率" value={(m.sharpe_ratio || 0).toFixed(2)} type="quality" />
          <MetricCard label="Sortino" value={(m.sortino_ratio || 0).toFixed(2)} type="quality" />
          <MetricCard label="盈亏比" value={(m.profit_factor || 0).toFixed(2)} type="quality" />
        </div>

        {/* 统计类 */}
        <div className="grid grid-cols-2 md:grid-cols-5 gap-3 mt-3">
          <MetricCard label="胜率" value={`${winRate.toFixed(1)}%`} type="quality" />
          <MetricCard label="交易次数" value={`${m.total_trades || 0}笔`} type="neutral" />
          <MetricCard label="盈利/亏损" value={`${m.winning_trades || 0}/${m.losing_trades || 0}`} type="neutral" />
          <MetricCard label="手续费"
            value={(m.total_commission || 0).toFixed(2)}
            type="neutral" />
          {(m.total_slippage || 0) > 0 && (
            <MetricCard label="滑点成本"
              value={(m.total_slippage || 0).toFixed(2)}
              type="risk" />
          )}
        </div>
      </div>

      {/* 资金曲线 + 基准线 */}
      <EquityCurveChart data={equityCurve} benchmark={benchmark} />

      {/* 交易记录 */}
      <TradeTable trades={trades} />
    </div>
  );
};

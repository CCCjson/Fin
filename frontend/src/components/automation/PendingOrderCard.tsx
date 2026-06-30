/**
 * 待确认订单卡片
 */
import React, { useState, useEffect } from 'react';
import type { PendingOrderItem } from '../../stores/automationStore';

interface Props {
  order: PendingOrderItem;
  onConfirm: (orderId: string) => void;
  onReject: (orderId: string) => void;
  selected?: boolean;
  onSelect?: (orderId: string, checked: boolean) => void;
}

export const PendingOrderCard: React.FC<Props> = ({ order, onConfirm, onReject, selected, onSelect }) => {
  const [expanded, setExpanded] = useState(false);
  const [timeLeft, setTimeLeft] = useState('');

  const isBuy = order.signal_type === 'BUY';
  const isPending = order.status === 'PENDING';
  const isExpired = order.status === 'EXPIRED';
  const isFilled = order.status === 'FILLED';

  // 过期倒计时 — 每秒更新
  useEffect(() => {
    if (!isPending || !order.expire_at) return;
    const tick = () => {
      const diff = new Date(order.expire_at!).getTime() - Date.now();
      if (diff <= 0) { setTimeLeft('已过期'); return; }
      const min = Math.floor(diff / 60000);
      const sec = Math.floor((diff % 60000) / 1000);
      setTimeLeft(`${min}:${sec.toString().padStart(2, '0')}`);
    };
    tick();
    const id = setInterval(tick, 1000);
    return () => clearInterval(id);
  }, [isPending, order.expire_at]);

  // 是否快过期（最后5分钟）
  const isUrgent = (() => {
    if (!order.expire_at) return false;
    const diff = new Date(order.expire_at).getTime() - Date.now();
    return diff > 0 && diff < 5 * 60 * 1000;
  })();

  const statusColor: Record<string, string> = {
    PENDING: 'text-yellow-400 bg-yellow-400/10',
    FILLED: 'text-green-400 bg-green-400/10',
    REJECTED: 'text-gray-400 bg-gray-400/10',
    EXPIRED: 'text-gray-500 bg-gray-500/10',
    FAILED: 'text-red-400 bg-red-400/10',
    EXECUTING: 'text-accent-cyan bg-accent-cyan/10',
    CONFIRMED: 'text-accent-cyan bg-accent-cyan/10',
  };

  const statusLabel: Record<string, string> = {
    PENDING: '待确认',
    FILLED: '已成交',
    REJECTED: '已拒绝',
    EXPIRED: '已过期',
    FAILED: '失败',
    EXECUTING: '执行中',
    CONFIRMED: '已确认',
  };

  return (
    <div className={`bg-gradient-card border rounded-xl p-4 transition-all duration-300 ease-out ${
      isPending
        ? 'border-yellow-400/30 hover:border-yellow-400/60 hover:shadow-glow-blue'
        : 'border-border hover:border-border-light'
    } ${isExpired ? 'opacity-60' : ''}`}>
      {/* 顶部行 */}
      <div className="flex items-center justify-between mb-3">
        <div className="flex items-center gap-3">
          {onSelect && isPending && (
            <input
              type="checkbox"
              checked={selected}
              onChange={(e) => onSelect(order.order_id, e.target.checked)}
              className="w-4 h-4 rounded border-gray-600 bg-dark text-primary focus:ring-primary"
            />
          )}
          <div>
            <div className="flex items-center gap-2">
              <span className="text-white font-bold text-lg">{order.symbol}</span>
              <span className="text-gray-400 text-sm">{order.name}</span>
            </div>
            <div className="flex items-center gap-2 mt-0.5">
              <span className={`text-xs px-2 py-0.5 rounded font-medium ${
                isBuy ? 'bg-red-500/20 text-red-400' : 'bg-green-500/20 text-green-400'
              }`}>
                {isBuy ? '买入' : '卖出'}
              </span>
              <span className="text-xs text-gray-500">{order.strategy}</span>
              <span className="text-xs text-gray-500">{order.scan_source}</span>
            </div>
          </div>
        </div>

        <div className="flex items-center gap-2">
          <span className={`text-xs px-2 py-1 rounded-full ${statusColor[order.status] || 'text-gray-400'}`}>
            {statusLabel[order.status] || order.status}
          </span>
          {isPending && timeLeft && (
            <span className={`text-xs font-mono ${
              isUrgent ? 'text-red-400 animate-pulse' : 'text-gray-500'
            }`}>
              {timeLeft}
            </span>
          )}
        </div>
      </div>

      {/* 信号强度条 */}
      <div className="mb-3">
        <div className="flex items-center justify-between text-xs text-gray-400 mb-1">
          <span>信号强度</span>
          <span className="font-medium">{(order.strength * 100).toFixed(0)}%</span>
        </div>
        <div className="h-2 bg-dark rounded-full overflow-hidden">
          <div
            className={`h-full rounded-full transition-all duration-500 ${
              order.strength >= 0.7 ? 'bg-green-500' : order.strength >= 0.5 ? 'bg-yellow-500' : 'bg-gray-500'
            }`}
            style={{ width: `${Math.min(order.strength * 100, 100)}%` }}
          />
        </div>
      </div>

      {/* 价格信息 — 带背景色块 */}
      <div className="grid grid-cols-4 gap-2 mb-3">
        <div className="text-center bg-dark/50 rounded-lg p-2">
          <div className="text-xs text-gray-500">建议价</div>
          <div className="text-sm text-white font-medium">{order.suggested_price?.toFixed(2)}</div>
        </div>
        <div className="text-center bg-dark/50 rounded-lg p-2">
          <div className="text-xs text-gray-500">数量</div>
          <div className="text-sm text-white font-medium">{order.suggested_quantity}</div>
        </div>
        <div className="text-center bg-dark/50 rounded-lg p-2">
          <div className="text-xs text-gray-500">止损</div>
          <div className="text-sm text-red-400">{order.stop_loss?.toFixed(2)}</div>
        </div>
        <div className="text-center bg-dark/50 rounded-lg p-2">
          <div className="text-xs text-gray-500">止盈</div>
          <div className="text-sm text-green-400">{order.take_profit?.toFixed(2)}</div>
        </div>
      </div>

      {/* 风控状态 */}
      <div className="flex items-center gap-2 mb-3">
        <span className={`text-xs px-2 py-0.5 rounded ${
          order.risk_check_passed ? 'bg-green-500/10 text-green-400' : 'bg-red-500/10 text-red-400'
        }`}>
          {order.risk_check_passed ? '风控通过' : '风控未通过'}
        </span>
        <span className="text-xs text-gray-500">
          金额 {((order.suggested_price || 0) * (order.suggested_quantity || 0)).toFixed(0)} 元
        </span>
      </div>

      {/* 展开详情 */}
      <button
        onClick={() => setExpanded(!expanded)}
        className="text-xs text-gray-500 hover:text-gray-300 transition-colors mb-2"
      >
        {expanded ? '收起详情 ▲' : '展开详情 ▼'}
      </button>

      <div className={`overflow-hidden transition-all duration-300 ease-out ${
        expanded ? 'max-h-96 opacity-100' : 'max-h-0 opacity-0'
      }`}>
        <div className="mt-2 space-y-2">
          {/* 触发原因 */}
          {order.reasons && order.reasons.length > 0 && (
            <div className="bg-dark/50 rounded-lg p-3">
              <div className="text-xs text-gray-400 mb-1.5 font-medium">触发原因</div>
              {order.reasons.map((r, i) => (
                <div key={i} className="text-xs text-gray-300 flex items-start gap-1.5 mb-1">
                  <span className="text-primary mt-0.5">-</span>
                  <span>
                    {r.indicator && <span className="text-primary font-medium">[{r.indicator}] </span>}
                    {r.detail}
                  </span>
                </div>
              ))}
            </div>
          )}

          {/* 风控详情 */}
          {order.risk_check_detail && order.risk_check_detail.length > 0 && (
            <div className="bg-dark/50 rounded-lg p-3">
              <div className="text-xs text-gray-400 mb-1.5 font-medium">风控检查</div>
              {order.risk_check_detail.map((r, i) => (
                <div key={i} className={`text-xs flex items-center gap-1.5 mb-1 ${r.passed ? 'text-green-400' : 'text-red-400'}`}>
                  <span>{r.passed ? '✓' : '✗'}</span>
                  <span>{r.message}</span>
                </div>
              ))}
            </div>
          )}

          {/* 执行结果 */}
          {isFilled && order.actual_price && (
            <div className="bg-dark/50 rounded-lg p-3">
              <div className="text-xs text-gray-400 mb-1.5 font-medium">执行结果</div>
              <div className="grid grid-cols-3 gap-2">
                <div className="text-xs"><span className="text-gray-500">成交价: </span><span className="text-white">{order.actual_price?.toFixed(2)}</span></div>
                <div className="text-xs"><span className="text-gray-500">成交量: </span><span className="text-white">{order.actual_quantity}</span></div>
                <div className="text-xs"><span className="text-gray-500">手续费: </span><span className="text-white">{order.commission?.toFixed(2)}</span></div>
              </div>
            </div>
          )}

          {/* 拒绝原因 */}
          {order.reject_reason && (
            <div className="text-xs text-red-400 bg-red-400/5 rounded-lg p-2">
              {order.reject_reason}
            </div>
          )}
        </div>
      </div>

      {/* 操作按钮 */}
      {isPending && (
        <div className="flex gap-2 mt-3">
          <button
            onClick={() => onConfirm(order.order_id)}
            className={`flex-1 py-2.5 rounded-lg text-sm font-medium transition-all active:scale-95 ${
              isBuy
                ? 'bg-red-500 hover:bg-red-600 text-white'
                : 'bg-green-500 hover:bg-green-600 text-white'
            }`}
          >
            确认{isBuy ? '买入' : '卖出'}
          </button>
          <button
            onClick={() => onReject(order.order_id)}
            className="flex-1 py-2.5 rounded-lg text-sm font-medium bg-dark-light hover:bg-gray-700 text-gray-300 transition-all active:scale-95"
          >
            拒绝
          </button>
        </div>
      )}

      {/* 时间 */}
      <div className="text-xs text-gray-600 mt-2 text-right">
        {order.created_at && new Date(order.created_at).toLocaleString('zh-CN')}
      </div>
    </div>
  );
};

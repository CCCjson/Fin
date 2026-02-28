/**
 * 全局通知横幅 — 监听 WebSocket，新订单时弹出顶部通知
 */
import React, { useEffect, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { useAutomationStore } from '../../stores/automationStore';

interface Toast {
  id: string;
  message: string;
  type: 'buy' | 'sell' | 'alert' | 'info';
  symbol?: string;
}

export const NotificationBanner: React.FC = () => {
  const [toasts, setToasts] = useState<Toast[]>([]);
  const nav = useNavigate();
  const notifications = useAutomationStore((s) => s.notifications);

  useEffect(() => {
    if (notifications.length === 0) return;
    const latest = notifications[0];
    if (latest.read) return;

    let message = '';
    let type: Toast['type'] = 'info';
    let symbol = '';

    switch (latest.type) {
      case 'pending_order': {
        const d = latest.data;
        symbol = d?.symbol || '';
        const isBuy = d?.signal_type === 'BUY';
        type = isBuy ? 'buy' : 'sell';
        message = `${symbol} ${d?.name || ''} ${isBuy ? '买入' : '卖出'}信号 | 强度 ${((d?.strength || 0) * 100).toFixed(0)}% | ${d?.strategy || ''}`;
        break;
      }
      case 'risk_alert': {
        const d = latest.data;
        symbol = d?.symbol || '';
        type = 'alert';
        message = `风控告警: ${d?.message || ''}`;
        break;
      }
      case 'order_filled': {
        const d = latest.data;
        symbol = d?.symbol || '';
        type = 'info';
        message = `${symbol} 订单已成交`;
        break;
      }
      default:
        return;
    }

    const toast: Toast = { id: latest.id, message, type, symbol };
    setToasts((prev) => [toast, ...prev].slice(0, 3));

    // 自动消失
    setTimeout(() => {
      setToasts((prev) => prev.filter((t) => t.id !== toast.id));
    }, 8000);
  }, [notifications]);

  if (toasts.length === 0) return null;

  const typeStyles: Record<string, string> = {
    buy: 'border-red-500/50 bg-red-500/10',
    sell: 'border-green-500/50 bg-green-500/10',
    alert: 'border-yellow-500/50 bg-yellow-500/10',
    info: 'border-blue-500/50 bg-blue-500/10',
  };

  return (
    <div className="fixed top-0 left-0 right-0 z-[60] flex flex-col items-center gap-2 pt-2 px-4 pointer-events-none">
      {toasts.map((toast) => (
        <div
          key={toast.id}
          className={`pointer-events-auto max-w-lg w-full border rounded-xl px-4 py-3 shadow-lg backdrop-blur-sm flex items-center justify-between gap-3 animate-slide-down ${typeStyles[toast.type] || typeStyles.info}`}
          style={{ animation: 'slide-down 0.3s ease' }}
        >
          <div className="flex-1 text-sm text-white truncate">{toast.message}</div>
          <div className="flex items-center gap-2 flex-shrink-0">
            <button
              onClick={() => nav('/app/automation')}
              className="text-xs text-primary hover:text-primary-light font-medium"
            >
              查看
            </button>
            <button
              onClick={() => setToasts((prev) => prev.filter((t) => t.id !== toast.id))}
              className="text-gray-500 hover:text-gray-300"
            >
              <svg className="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M6 18L18 6M6 6l12 12" />
              </svg>
            </button>
          </div>
        </div>
      ))}
    </div>
  );
};

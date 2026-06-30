import React from 'react';

interface Props {
  preview: any;
  onConfirm: () => void;
  onCancel: () => void;
}

/* 下单二次确认弹窗 —— 展示订单详情 + 风控预检结果 */
export const ConfirmDialog: React.FC<Props> = ({ preview, onConfirm, onCancel }) => {
  const p = preview || {};
  const err = p.error;
  const riskPassed = p.risk_passed;
  const sideLabel = p.action === 'BUY' ? '买入' : p.action === 'SELL' ? '卖出' : p.action;

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center" onClick={onCancel}>
      <div className="absolute inset-0 bg-black/60 backdrop-blur-sm" />
      <div
        className="relative w-full max-w-md bg-dark-card border border-border rounded-2xl shadow-card p-6"
        onClick={(e) => e.stopPropagation()}
      >
        <div className="flex items-center gap-2 mb-4">
          <span className="text-2xl">⚠️</span>
          <h2 className="text-lg font-bold text-white">下单确认（模拟盘）</h2>
        </div>

        {err ? (
          <div className="p-3 rounded-lg bg-red-900/40 border border-red-600/40 text-red-200 text-sm mb-4">
            无法下单：{err}
          </div>
        ) : (
          <>
            <div className="grid grid-cols-2 gap-3 mb-4 text-sm">
              <Cell label="股票" value={p.symbol} />
              <Cell label="方向" value={sideLabel} valueClass={p.action === 'BUY' ? 'text-bull' : 'text-bear'} />
              <Cell label="数量" value={`${p.quantity} 股`} />
              <Cell label="价格" value={`¥${p.price}`} />
              <Cell label="预计金额" value={`¥${Number(p.est_amount).toLocaleString()}`} />
              <Cell label="可用现金" value={`¥${Number(p.cash).toLocaleString()}`} />
            </div>

            <div className={`p-3 rounded-lg text-sm mb-4 border ${riskPassed
              ? 'bg-green-900/20 border-green-600/30 text-green-200'
              : 'bg-red-900/30 border-red-600/40 text-red-200'}`}>
              <div className="font-semibold mb-1">{riskPassed ? '✅ 风控预检通过' : '⛔ 风控未通过（确认后仍会被拦截）'}</div>
              {Array.isArray(p.risk_failed) && p.risk_failed.length > 0 && (
                <ul className="list-disc list-inside text-xs space-y-0.5">
                  {p.risk_failed.map((m: string, i: number) => <li key={i}>{m}</li>)}
                </ul>
              )}
            </div>
          </>
        )}

        <div className="flex gap-3 justify-end">
          <button
            onClick={onCancel}
            className="px-4 py-2 rounded-lg border border-border text-gray-300 hover:bg-dark-light text-sm transition-colors"
          >
            取消
          </button>
          <button
            onClick={onConfirm}
            disabled={!!err}
            className="px-5 py-2 rounded-lg bg-primary hover:bg-primary-light disabled:opacity-40 text-dark text-sm font-medium transition-colors"
          >
            确认下单
          </button>
        </div>
      </div>
    </div>
  );
};

const Cell: React.FC<{ label: string; value: any; valueClass?: string }> = ({ label, value, valueClass }) => (
  <div className="bg-dark-light rounded-lg p-2.5">
    <div className="text-gray-500 text-xs">{label}</div>
    <div className={`font-medium ${valueClass || 'text-white'}`}>{value ?? '—'}</div>
  </div>
);

export default ConfirmDialog;

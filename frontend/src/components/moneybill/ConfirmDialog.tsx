import React from 'react';
import { localFullDateTime } from '../../utils/datetime';

interface Props {
  /** 触发确认的工具名，如 place_order / add_to_watchlist，决定弹窗样式。*/
  name?: string;
  preview: any;
  onConfirm: () => void;
  onCancel: () => void;
}

/* 需确认工具的中文标题——未收录的工具兜底显示工具名本身。*/
const TOOL_LABELS: Record<string, string> = {
  place_order: '下单确认（模拟盘）',
  place_crypto_order: '下单确认（币安现货 · 实盘）',
  add_to_watchlist: '加入自选股',
  remove_from_watchlist: '移出自选股',
  create_price_alert: '创建价格预警',
  delete_price_alert: '删除价格预警',
  record_manual_trade: '录入手动交易',
  update_setting: '修改设置',
};

/* preview 字段名 → 中文标签，跨工具共享同一份映射。*/
const FIELD_LABELS: Record<string, string> = {
  symbol: '股票', name: '名称', group_name: '分组', note: '备注',
  already_in: '已在自选', alert_type: '预警类型', threshold: '阈值',
  current_price: '当前价', repeat: '触发方式', desc: '说明',
  action: '操作', side: '方向', price: '价格', quantity: '数量',
  est_amount: '预计金额', trade_date: '交易日期', key: '配置项',
  label: '字段名', new_value: '新值', sensitive: '敏感字段',
  alert_id: '预警ID', status: '状态', message: '提醒文案',
  triggered_at: '触发时间', triggered_price: '触发价', items: '详情',
};

function formatValue(key: string, value: any): string {
  if (value === null || value === undefined || value === '') return '—';
  // 时间类字段（后端带 UTC offset）转本地显示，别原样吐 UTC 串
  if (key === 'triggered_at') return localFullDateTime(value);
  if (typeof value === 'boolean') return value ? '是' : '否';
  if (Array.isArray(value)) {
    if (key === 'items') {
      return value.map((it: any) => `${it.name || it.symbol || ''}（${it.group_name || '默认分组'}）`).join('、') || '—';
    }
    return value.join('、');
  }
  return String(value);
}

/* 下单二次确认弹窗（place_order 富样式：订单六格 + 风控预检）*/
const PlaceOrderBody: React.FC<{ p: any }> = ({ p }) => {
  const sideLabel = p.action === 'BUY' ? '买入' : p.action === 'SELL' ? '卖出' : p.action;
  const riskPassed = p.risk_passed;
  return (
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
  );
};

/* 加密下单二次确认（币安现货：币量小数 + USDT 计价 + 自动补足披露 + 风控预检）*/
const PlaceCryptoOrderBody: React.FC<{ p: any }> = ({ p }) => {
  const sideLabel = p.action === 'BUY' ? '买入' : p.action === 'SELL' ? '卖出' : p.action;
  const riskPassed = p.risk_passed;
  const steps = Array.isArray(p.needs_funding) ? p.needs_funding : [];
  const short = typeof p.funding_short_usdt === 'number' ? p.funding_short_usdt : 0;
  return (
    <>
      <div className="grid grid-cols-2 gap-3 mb-4 text-sm">
        <Cell label="交易对" value={p.symbol} />
        <Cell label="方向" value={sideLabel} valueClass={p.action === 'BUY' ? 'text-bull' : 'text-bear'} />
        <Cell label="数量" value={p.quantity} />
        <Cell label="价格" value={`$${p.price}`} />
        <Cell label="预计金额" value={`$${Number(p.est_amount_usdt).toLocaleString()}`} />
        <Cell label="可动用买力" value={`$${Number(p.cash_usdt).toLocaleString()}`} />
      </div>

      {/* 买入自动补足披露：现货不够 → 先赎活期/划资金到现货，再买（同一次确认内完成）*/}
      {steps.length > 0 && (
        <div className="p-3 rounded-lg text-sm mb-4 border bg-blue-900/20 border-blue-600/30 text-blue-100">
          <div className="font-semibold mb-1">💱 现货 USDT 不足，将先自动补足再下单：</div>
          <ul className="list-disc list-inside text-xs space-y-0.5">
            {steps.map((s: any, i: number) => (
              <li key={i}>
                {s.action === 'redeem' ? '赎回' : '划转'} {s.amount} {s.asset}（来自{s.from}）→ 现货
              </li>
            ))}
          </ul>
          <div className="text-[11px] text-blue-200/70 mt-1">现货现有 ${Number(p.spot_cash_usdt).toLocaleString()}</div>
        </div>
      )}
      {short > 0 && (
        <div className="p-3 rounded-lg text-sm mb-4 border bg-red-900/30 border-red-600/40 text-red-200">
          ⛔ 买力不足：活期+资金也补不满，仍缺 ${short} USDT，确认后将被拦截。
        </div>
      )}

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
  );
};

/* 通用确认卡片——非下单类工具（自选/预警/设置/交易记录）走这里，按 preview 键值对渲染。*/
const GenericBody: React.FC<{ p: any }> = ({ p }) => (
  <div className="grid grid-cols-2 gap-3 mb-4 text-sm">
    {Object.entries(p).map(([key, value]) => (
      <Cell key={key} label={FIELD_LABELS[key] || key} value={formatValue(key, value)} />
    ))}
  </div>
);

export const ConfirmDialog: React.FC<Props> = ({ name, preview, onConfirm, onCancel }) => {
  const p = preview || {};
  const err = p.error;
  const isCryptoOrder = name === 'place_crypto_order';
  const isOrder = name === 'place_order' || isCryptoOrder;
  const title = (name && TOOL_LABELS[name]) || name || '操作确认';

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center" onClick={onCancel}>
      <div className="absolute inset-0 bg-black/60 backdrop-blur-sm" />
      <div
        className="relative w-full max-w-md bg-dark-card border border-border rounded-2xl shadow-card p-6"
        onClick={(e) => e.stopPropagation()}
      >
        <div className="flex items-center gap-2 mb-4">
          <span className="text-2xl">{isOrder ? '⚠️' : '✅'}</span>
          <h2 className="text-lg font-bold text-white">{isOrder ? title : `操作确认 · ${title}`}</h2>
        </div>

        {err ? (
          <div className="p-3 rounded-lg bg-red-900/40 border border-red-600/40 text-red-200 text-sm mb-4">
            无法执行：{err}
          </div>
        ) : isCryptoOrder ? (
          <PlaceCryptoOrderBody p={p} />
        ) : isOrder ? (
          <PlaceOrderBody p={p} />
        ) : (
          <GenericBody p={p} />
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
            {isOrder ? '确认下单' : '确认执行'}
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

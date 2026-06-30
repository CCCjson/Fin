import React, { useState, useCallback, useRef, useEffect, useMemo } from 'react';
import { Card } from '../components/common/Card';

const API = '/api';

// ══════════════════════════════════════════════════════════════
// 类型定义
// ══════════════════════════════════════════════════════════════

interface DepthLevel {
  price: number;
  quantity: number;
  order_count: number;
}

interface DepthSnapshot {
  bids: DepthLevel[];
  asks: DepthLevel[];
  spread: number;
  mid_price: number;
}

interface Fill {
  fill_id: string;
  buy_order_id: string;
  sell_order_id: string;
  price: number;
  quantity: number;
  aggressor_side: string;
  timestamp: number;
}

interface BookStats {
  spread: number;
  spread_bps: number;
  mid_price: number;
  bid_depth: number;
  ask_depth: number;
  imbalance: number;
  vwap: number;
  total_fills: number;
  total_volume: number;
}

interface MatchResult {
  order_id: string;
  filled_quantity: number;
  remaining_quantity: number;
  is_resting: boolean;
  is_rejected: boolean;
  fills: Fill[];
}

interface MMStatus {
  running: boolean;
  net_position: number;
  active_bid_orders: number;
  active_ask_orders: number;
  total_trades: number;
  uptime_seconds: number;
  target_mid: number;
  pnl: number;
}

// ══════════════════════════════════════════════════════════════
// 多市场预设
// ══════════════════════════════════════════════════════════════

interface MarketPreset {
  id: string;
  name: string;
  flag: string;
  defaultSymbol: string;
  defaultMidPrice: number;
  tickSize: number;
  spreadTicks: number;
  lotSize: number;
  hasLotRestriction: boolean;
  hasPriceLimit: boolean;
  priceLimitPct: number;
  currency: string;
  currencySymbol: string;
  color: string;
  gradient: string;
  description: string;
  tradingHours: string;
  quickLots: number[];
}

const MARKET_PRESETS: MarketPreset[] = [
  {
    id: 'A', name: 'A 股', flag: '\u{1F1E8}\u{1F1F3}',
    defaultSymbol: '600519', defaultMidPrice: 1500.00,
    tickSize: 0.01, spreadTicks: 2, lotSize: 100,
    hasLotRestriction: true, hasPriceLimit: true, priceLimitPct: 0.10,
    currency: 'CNY', currencySymbol: '\u00A5',
    color: '#EF4444', gradient: 'from-red-500/20 to-red-900/10',
    description: '沪深主板 / 科创板 / 创业板',
    tradingHours: '9:30-11:30 / 13:00-15:00',
    quickLots: [1, 5, 10, 50, 100],
  },
  {
    id: 'HK', name: '港 股', flag: '\u{1F1ED}\u{1F1F0}',
    defaultSymbol: '00700', defaultMidPrice: 380.00,
    tickSize: 0.20, spreadTicks: 3, lotSize: 100,
    hasLotRestriction: true, hasPriceLimit: false, priceLimitPct: 0,
    currency: 'HKD', currencySymbol: 'HK$',
    color: '#8B5CF6', gradient: 'from-purple-500/20 to-purple-900/10',
    description: '港股主板 / 恒生指数成分股',
    tradingHours: '9:30-12:00 / 13:00-16:00',
    quickLots: [1, 5, 10, 50, 100],
  },
  {
    id: 'US', name: '美 股', flag: '\u{1F1FA}\u{1F1F8}',
    defaultSymbol: 'AAPL', defaultMidPrice: 185.00,
    tickSize: 0.01, spreadTicks: 1, lotSize: 1,
    hasLotRestriction: false, hasPriceLimit: false, priceLimitPct: 0,
    currency: 'USD', currencySymbol: '$',
    color: '#3B82F6', gradient: 'from-blue-500/20 to-blue-900/10',
    description: 'NYSE / NASDAQ',
    tradingHours: '9:30-16:00 ET',
    quickLots: [10, 50, 100, 500, 1000],
  },
];

// ══════════════════════════════════════════════════════════════
// WebSocket Hook
// ══════════════════════════════════════════════════════════════

function useOrderBookWS(
  sessionId: string,
  onUpdate: (data: { depth?: DepthSnapshot; fills?: Fill[]; stats?: BookStats }) => void,
) {
  const wsRef = useRef<WebSocket | null>(null);
  const reconnectRef = useRef<ReturnType<typeof setTimeout>>(undefined);
  const reconnectDelay = useRef(500);
  const onUpdateRef = useRef(onUpdate);
  onUpdateRef.current = onUpdate;

  useEffect(() => {
    if (!sessionId) return;

    const connect = () => {
      const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
      const host = window.location.host;
      const url = `${protocol}//${host}/api/orderbook/sessions/${sessionId}/ws`;

      const ws = new WebSocket(url);
      wsRef.current = ws;

      ws.onopen = () => {
        reconnectDelay.current = 500;
      };

      ws.onmessage = (event) => {
        try {
          const msg = JSON.parse(event.data);
          if (msg.type === 'orderbook_update' && msg.data) {
            onUpdateRef.current(msg.data);
          }
        } catch {
          // ignore
        }
      };

      ws.onclose = () => {
        wsRef.current = null;
        // 自动重连（指数退避，最大5秒）
        reconnectRef.current = setTimeout(() => {
          reconnectDelay.current = Math.min(reconnectDelay.current * 1.5, 5000);
          connect();
        }, reconnectDelay.current);
      };

      ws.onerror = () => {
        ws.close();
      };
    };

    connect();

    // 心跳
    const pingInterval = setInterval(() => {
      if (wsRef.current?.readyState === WebSocket.OPEN) {
        wsRef.current.send('ping');
      }
    }, 30000);

    return () => {
      clearInterval(pingInterval);
      if (reconnectRef.current) clearTimeout(reconnectRef.current);
      if (wsRef.current) {
        wsRef.current.onclose = null;  // 防止触发重连
        wsRef.current.close();
        wsRef.current = null;
      }
    };
  }, [sessionId]);
}

// ══════════════════════════════════════════════════════════════
// Toast 通知
// ══════════════════════════════════════════════════════════════

interface ToastData {
  id: number;
  type: 'success' | 'partial' | 'rejected' | 'error';
  title: string;
  message: string;
}

const Toast: React.FC<{ toast: ToastData; onClose: () => void }> = ({ toast, onClose }) => {
  useEffect(() => {
    const t = setTimeout(onClose, 4000);
    return () => clearTimeout(t);
  }, [onClose]);

  const colors = {
    success:  'border-green-500/50 bg-green-500/10',
    partial:  'border-yellow-500/50 bg-yellow-500/10',
    rejected: 'border-red-500/50 bg-red-500/10',
    error:    'border-red-500/50 bg-red-500/10',
  };
  const icons = {
    success: '\u2713', partial: '\u25D4', rejected: '\u2717', error: '\u26A0',
  };
  const iconColors = {
    success: 'text-green-400', partial: 'text-yellow-400', rejected: 'text-red-400', error: 'text-red-400',
  };

  return (
    <div className={`animate-slide-in-right border rounded-lg px-4 py-3 shadow-lg backdrop-blur-sm ${colors[toast.type]}`}>
      <div className="flex items-start gap-3">
        <span className={`text-lg ${iconColors[toast.type]}`}>{icons[toast.type]}</span>
        <div className="flex-1 min-w-0">
          <p className="text-sm font-medium text-white">{toast.title}</p>
          <p className="text-xs text-gray-400 mt-0.5">{toast.message}</p>
        </div>
        <button onClick={onClose} className="text-gray-500 hover:text-white text-sm">\u2715</button>
      </div>
    </div>
  );
};

// ══════════════════════════════════════════════════════════════
// 主组件
// ══════════════════════════════════════════════════════════════

export const OrderBook: React.FC = () => {
  // ── 会话状态 ──
  const [sessionId, setSessionId] = useState(() => localStorage.getItem('ob_session_id') || '');
  const [marketId, setMarketId] = useState(() => localStorage.getItem('ob_market_id') || '');
  const [sessionSymbol, setSessionSymbol] = useState(() => localStorage.getItem('ob_symbol') || '');
  const [prevClose, setPrevClose] = useState(() => parseFloat(localStorage.getItem('ob_prev_close') || '0'));

  // ── 数据状态 ──
  const [depth, setDepth] = useState<DepthSnapshot | null>(null);
  const [fills, setFills] = useState<Fill[]>([]);
  const [stats, setStats] = useState<BookStats | null>(null);
  const [lastResult, setLastResult] = useState<MatchResult | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState('');
  const [toasts, setToasts] = useState<ToastData[]>([]);

  // ── 下单表单 ──
  const [side, setSide] = useState<'BUY' | 'SELL'>('BUY');
  const [orderType, setOrderType] = useState('LIMIT');
  const [price, setPrice] = useState('');
  const [quantity, setQuantity] = useState('100');

  // ── 创建会话表单 ──
  const [selectedMarket, setSelectedMarket] = useState<string>('A');
  const [symbol, setSymbol] = useState('');
  const [midPrice, setMidPrice] = useState('');
  const [seedCount, setSeedCount] = useState('200');
  const [inputPrevClose, setInputPrevClose] = useState('');

  // ── 做市商状态 ──
  const [mmStatus, setMmStatus] = useState<MMStatus | null>(null);
  const [mmLoading, setMmLoading] = useState(false);
  const [mmVolatility, setMmVolatility] = useState('0.0005');
  const [mmDrift, setMmDrift] = useState('0');
  const [mmSpreadTicks, setMmSpreadTicks] = useState('2');
  const [mmBaseQty, setMmBaseQty] = useState('500');

  const toastIdRef = useRef(0);

  const market = useMemo(
    () => MARKET_PRESETS.find(m => m.id === marketId) || MARKET_PRESETS[0],
    [marketId]
  );

  const selectedPreset = useMemo(
    () => MARKET_PRESETS.find(m => m.id === selectedMarket) || MARKET_PRESETS[0],
    [selectedMarket]
  );

  // 当选择市场变化时，更新表单默认值
  useEffect(() => {
    const p = selectedPreset;
    setSymbol(p.defaultSymbol);
    setMidPrice(p.defaultMidPrice.toFixed(2));
    setInputPrevClose(p.defaultMidPrice.toFixed(2));
  }, [selectedPreset]);

  // 涨跌停价计算
  const limitUpPrice = useMemo(() => {
    if (!market.hasPriceLimit || !prevClose) return null;
    return Math.round((prevClose * (1 + market.priceLimitPct)) * 100) / 100;
  }, [market, prevClose]);

  const limitDownPrice = useMemo(() => {
    if (!market.hasPriceLimit || !prevClose) return null;
    return Math.round((prevClose * (1 - market.priceLimitPct)) * 100) / 100;
  }, [market, prevClose]);

  // ── Toast helper ──
  const addToast = useCallback((type: ToastData['type'], title: string, message: string) => {
    const id = ++toastIdRef.current;
    setToasts(prev => [...prev.slice(-4), { id, type, title, message }]);
  }, []);

  const removeToast = useCallback((id: number) => {
    setToasts(prev => prev.filter(t => t.id !== id));
  }, []);

  // ── 持久化 sessionId ──
  useEffect(() => {
    if (sessionId) {
      localStorage.setItem('ob_session_id', sessionId);
      localStorage.setItem('ob_market_id', marketId);
      localStorage.setItem('ob_symbol', sessionSymbol);
      localStorage.setItem('ob_prev_close', prevClose.toString());
    } else {
      localStorage.removeItem('ob_session_id');
      localStorage.removeItem('ob_market_id');
      localStorage.removeItem('ob_symbol');
      localStorage.removeItem('ob_prev_close');
    }
  }, [sessionId, marketId, sessionSymbol, prevClose]);

  // ── WebSocket 实时推送 ──
  const handleWSUpdate = useCallback((data: { depth?: DepthSnapshot; fills?: Fill[]; stats?: BookStats }) => {
    if (data.depth) setDepth(data.depth);
    if (data.stats) setStats(data.stats);
    if (data.fills && data.fills.length > 0) {
      setFills(prev => {
        const merged = [...prev, ...data.fills!];
        return merged.slice(-50);  // 保留最近50条
      });
    }
  }, []);

  useOrderBookWS(sessionId, handleWSUpdate);

  // ── 回退轮询（WS 未连接时） ──
  const refreshData = useCallback(async () => {
    if (!sessionId) return;
    try {
      const [dRes, fRes, sRes] = await Promise.all([
        fetch(`${API}/orderbook/sessions/${sessionId}/depth?levels=15`),
        fetch(`${API}/orderbook/sessions/${sessionId}/fills?limit=30`),
        fetch(`${API}/orderbook/sessions/${sessionId}/stats`),
      ]);
      if (dRes.ok) setDepth(await dRes.json());
      if (fRes.ok) {
        const data = await fRes.json();
        setFills(data.fills || []);
      }
      if (sRes.ok) setStats(await sRes.json());
      setError('');
    } catch {
      // 会话不可达
    }
  }, [sessionId]);

  // 初始加载一次 HTTP 数据（WS 可能还没连上）
  useEffect(() => {
    if (sessionId) refreshData();
  }, [sessionId, refreshData]);

  // ── 检测恢复的会话是否还有效 ──
  useEffect(() => {
    if (!sessionId) return;
    fetch(`${API}/orderbook/sessions/${sessionId}/stats`)
      .then(res => { if (!res.ok) resetSession(); })
      .catch(() => resetSession());
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // ── 做市商状态轮询 ──
  useEffect(() => {
    if (!sessionId) return;
    const poll = async () => {
      try {
        const res = await fetch(`${API}/orderbook/sessions/${sessionId}/market-maker/status`);
        if (res.ok) setMmStatus(await res.json());
      } catch {}
    };
    poll();
    const iv = setInterval(poll, 2000);
    return () => clearInterval(iv);
  }, [sessionId]);

  // ── 创建会话 ──
  const createSession = async () => {
    setLoading(true);
    setError('');
    try {
      const res = await fetch(`${API}/orderbook/sessions`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          symbol,
          mid_price: parseFloat(midPrice),
          seed_count: parseInt(seedCount),
          tick_size: selectedPreset.tickSize,
          spread_ticks: selectedPreset.spreadTicks,
        }),
      });
      if (!res.ok) {
        const err = await res.json();
        throw new Error(err.detail || 'Failed');
      }
      const data = await res.json();
      setSessionId(data.session_id);
      setMarketId(selectedPreset.id);
      setSessionSymbol(symbol);
      setPrevClose(parseFloat(inputPrevClose) || parseFloat(midPrice));
      setPrice(midPrice);
      setQuantity(selectedPreset.lotSize.toString());
    } catch (e: any) {
      setError(e.message);
    } finally {
      setLoading(false);
    }
  };

  // ── 重置会话 ──
  const resetSession = () => {
    setSessionId('');
    setMarketId('');
    setSessionSymbol('');
    setPrevClose(0);
    setDepth(null);
    setFills([]);
    setStats(null);
    setLastResult(null);
    setMmStatus(null);
  };

  // ── 验证数量 ──
  const validateQuantity = (q: number): string | null => {
    if (market.hasLotRestriction && q % market.lotSize !== 0) {
      return `${market.name}要求整手交易（1手=${market.lotSize}股）`;
    }
    if (q <= 0) return '数量必须大于 0';
    return null;
  };

  // ── 验证价格 ──
  const validatePrice = (p: number): string | null => {
    if (market.hasPriceLimit && prevClose > 0) {
      if (limitUpPrice && p > limitUpPrice) return `超过涨停价 ${limitUpPrice.toFixed(2)}`;
      if (limitDownPrice && p < limitDownPrice) return `低于跌停价 ${limitDownPrice.toFixed(2)}`;
    }
    return null;
  };

  // ── 提交订单 ──
  const submitOrder = async () => {
    if (!sessionId) return;
    setError('');

    const qty = parseInt(quantity);
    const qtyErr = validateQuantity(qty);
    if (qtyErr) { addToast('error', '数量错误', qtyErr); return; }

    const p = orderType === 'MARKET' ? 0 : parseFloat(price);
    if (orderType !== 'MARKET') {
      const priceErr = validatePrice(p);
      if (priceErr) { addToast('error', '价格错误', priceErr); return; }
    }

    try {
      const res = await fetch(`${API}/orderbook/sessions/${sessionId}/orders`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ side, order_type: orderType, price: p, quantity: qty }),
      });
      const data = await res.json();
      if (!res.ok) throw new Error(data.detail || data.error || 'Failed');

      setLastResult(data);
      // 不再需要手动 refreshData，WS 会推送

      // Toast 通知
      if (data.is_rejected) {
        addToast('rejected', '订单被拒绝', `FOK 订单无法完全成交（需 ${qty} 股）`);
      } else if (data.remaining_quantity > 0 && data.filled_quantity > 0) {
        addToast('partial', '部分成交',
          `成交 ${data.filled_quantity} 股，${data.remaining_quantity} 股已挂单`);
      } else if (data.filled_quantity > 0) {
        const avgPrice = data.fills.length > 0
          ? (data.fills.reduce((s: number, f: Fill) => s + f.price * f.quantity, 0) /
             data.fills.reduce((s: number, f: Fill) => s + f.quantity, 0)).toFixed(2)
          : '-';
        addToast('success', '完全成交',
          `${data.filled_quantity} 股 @ 均价 ${market.currencySymbol}${avgPrice}`);
      } else {
        addToast('partial', '已挂单', `${qty} 股 @ ${market.currencySymbol}${p.toFixed(2)} 等待撮合`);
      }
    } catch (e: any) {
      addToast('error', '下单失败', e.message);
    }
  };

  // ── 做市商控制 ──
  const toggleMarketMaker = async () => {
    if (!sessionId) return;
    setMmLoading(true);
    try {
      if (mmStatus?.running) {
        await fetch(`${API}/orderbook/sessions/${sessionId}/market-maker/stop`, { method: 'POST' });
        addToast('success', '做市商已停止', '所有挂单已撤销');
      } else {
        await fetch(`${API}/orderbook/sessions/${sessionId}/market-maker/start`, {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({
            tick_size: market.tickSize,
            spread_ticks: parseInt(mmSpreadTicks) || 2,
            base_quantity: parseInt(mmBaseQty) || 500,
            volatility: parseFloat(mmVolatility) || 0.0005,
            price_drift: parseFloat(mmDrift) || 0,
          }),
        });
        addToast('success', '做市商已启动', '开始自动提供流动性');
      }
      // 刷新状态
      const res = await fetch(`${API}/orderbook/sessions/${sessionId}/market-maker/status`);
      if (res.ok) setMmStatus(await res.json());
    } catch (e: any) {
      addToast('error', '操作失败', e.message);
    } finally {
      setMmLoading(false);
    }
  };

  // ── 盘口深度条的最大量 ──
  const maxQty = depth
    ? Math.max(...depth.bids.map(b => b.quantity), ...depth.asks.map(a => a.quantity), 1)
    : 1;

  // ── 快捷价格 ──
  const getQuickPrices = () => {
    const mid = stats?.mid_price || depth?.mid_price || 0;
    const bestBid = depth?.bids[0]?.price;
    const bestAsk = depth?.asks[0]?.price;

    const prices: { label: string; value: number; color: string }[] = [];
    if (market.hasPriceLimit && limitUpPrice) {
      prices.push({ label: '涨停', value: limitUpPrice, color: 'text-red-400' });
    }
    if (bestAsk) prices.push({ label: '对手卖1', value: bestAsk, color: 'text-green-400' });
    if (mid) prices.push({ label: '中间价', value: mid, color: 'text-yellow-400' });
    if (bestBid) prices.push({ label: '对手买1', value: bestBid, color: 'text-red-400' });
    if (market.hasPriceLimit && limitDownPrice) {
      prices.push({ label: '跌停', value: limitDownPrice, color: 'text-green-400' });
    }
    return prices;
  };

  // ══════════════════════════════════════════════════════════════
  // 渲染 — 创建会话页
  // ══════════════════════════════════════════════════════════════

  if (!sessionId) {
    return (
      <div className="p-4 md:p-8 max-w-4xl mx-auto pb-24 md:pb-8">
        {/* 标题 */}
        <div className="text-center mb-8">
          <h1 className="text-3xl md:text-4xl font-bold text-white mb-2">
            订单簿模拟器
          </h1>
          <p className="text-gray-400">
            选择市场，创建模拟交易会话，体验限价订单簿的撮合逻辑
          </p>
        </div>

        {/* 市场选择卡片 */}
        <div className="grid grid-cols-1 md:grid-cols-3 gap-4 mb-8">
          {MARKET_PRESETS.map(preset => {
            const isSelected = selectedMarket === preset.id;
            return (
              <button
                key={preset.id}
                onClick={() => setSelectedMarket(preset.id)}
                className={`relative p-5 rounded-xl border-2 text-left transition-all duration-300 overflow-hidden group
                  ${isSelected
                    ? 'border-opacity-80 bg-gradient-card scale-[1.02]'
                    : 'border-border bg-dark-card hover:border-border-light hover:scale-[1.01]'
                  }`}
                style={{
                  borderColor: isSelected ? preset.color : undefined,
                  boxShadow: isSelected ? `0 0 30px ${preset.color}30` : undefined,
                }}
              >
                {/* 背景渐变 */}
                <div className={`absolute inset-0 bg-gradient-to-br ${preset.gradient} opacity-0 group-hover:opacity-100 transition-opacity duration-500`} />
                {isSelected && <div className={`absolute inset-0 bg-gradient-to-br ${preset.gradient}`} />}

                <div className="relative z-10">
                  <div className="flex items-center gap-3 mb-3">
                    <span className="text-3xl">{preset.flag}</span>
                    <div>
                      <h3 className="text-lg font-bold text-white">{preset.name}</h3>
                      <p className="text-xs text-gray-500">{preset.description}</p>
                    </div>
                  </div>

                  <div className="space-y-1.5 text-xs text-gray-400">
                    <div className="flex justify-between">
                      <span>交易时段</span>
                      <span className="text-gray-300">{preset.tradingHours}</span>
                    </div>
                    <div className="flex justify-between">
                      <span>最小交易单位</span>
                      <span className="text-gray-300">
                        {preset.hasLotRestriction ? `${preset.lotSize}股/手` : '1股'}
                      </span>
                    </div>
                    <div className="flex justify-between">
                      <span>涨跌停</span>
                      <span className="text-gray-300">
                        {preset.hasPriceLimit ? `\u00B1${(preset.priceLimitPct * 100).toFixed(0)}%` : '无'}
                      </span>
                    </div>
                    <div className="flex justify-between">
                      <span>最小变动</span>
                      <span className="text-gray-300">{preset.currencySymbol}{preset.tickSize}</span>
                    </div>
                  </div>
                </div>

                {/* 选中标记 */}
                {isSelected && (
                  <div className="absolute top-3 right-3 w-6 h-6 rounded-full flex items-center justify-center text-white text-xs font-bold"
                    style={{ backgroundColor: preset.color }}>
                    \u2713
                  </div>
                )}
              </button>
            );
          })}
        </div>

        {/* 配置表单 */}
        <div className="bg-dark-card rounded-xl border border-border p-5 md:p-6 shadow-card">
          <h2 className="text-sm font-medium text-gray-400 mb-4 flex items-center gap-2">
            <span className="text-lg">{selectedPreset.flag}</span>
            {selectedPreset.name} 会话配置
          </h2>

          <div className="grid grid-cols-1 md:grid-cols-2 gap-4 mb-4">
            <div>
              <label className="text-xs text-gray-500 mb-1 block">股票代码</label>
              <input value={symbol} onChange={e => setSymbol(e.target.value)}
                className="form-input font-mono" placeholder={selectedPreset.defaultSymbol} />
            </div>
            <div>
              <label className="text-xs text-gray-500 mb-1 block">中间价（{selectedPreset.currencySymbol}）</label>
              <input value={midPrice} onChange={e => setMidPrice(e.target.value)}
                className="form-input font-mono" placeholder={selectedPreset.defaultMidPrice.toFixed(2)} />
            </div>
            {selectedPreset.hasPriceLimit && (
              <div>
                <label className="text-xs text-gray-500 mb-1 block">前收盘价（计算涨跌停）</label>
                <input value={inputPrevClose} onChange={e => setInputPrevClose(e.target.value)}
                  className="form-input font-mono" />
              </div>
            )}
            <div>
              <label className="text-xs text-gray-500 mb-1 block">初始播种订单数</label>
              <input value={seedCount} onChange={e => setSeedCount(e.target.value)}
                className="form-input font-mono" placeholder="200" />
            </div>
          </div>

          <button onClick={createSession} disabled={loading}
            className="w-full py-3.5 rounded-xl font-bold text-white text-sm transition-all duration-300 disabled:opacity-50"
            style={{
              background: `linear-gradient(135deg, ${selectedPreset.color}, ${selectedPreset.color}99)`,
              boxShadow: `0 0 25px ${selectedPreset.color}40`,
            }}>
            {loading ? '创建中...' : '启动模拟交易所'}
          </button>
          {error && <p className="text-red-400 text-sm mt-3">{error}</p>}
        </div>
      </div>
    );
  }

  // ══════════════════════════════════════════════════════════════
  // 渲染 — 交易主界面
  // ══════════════════════════════════════════════════════════════

  const quickPrices = getQuickPrices();

  return (
    <div className="p-2 md:p-3 h-full flex flex-col gap-2 md:gap-3 overflow-auto pb-20 md:pb-3">
      {/* ── 顶栏 ── */}
      <Card variant="flat" className="flex flex-col gap-2 md:flex-row md:items-center md:justify-between px-4 py-3">
        <div className="flex items-center gap-3">
          <span className="text-xl">{market.flag}</span>
          <div>
            <div className="flex items-center gap-2">
              <h1 className="text-lg font-bold text-white">{sessionSymbol}</h1>
              <span className="text-xs px-2 py-0.5 rounded-full font-medium"
                style={{ backgroundColor: `${market.color}20`, color: market.color }}>
                {market.name}
              </span>
              {mmStatus?.running && (
                <span className="text-xs px-2 py-0.5 rounded-full font-medium bg-amber-500/20 text-amber-400 animate-pulse">
                  MM
                </span>
              )}
            </div>
            <span className="text-xs text-gray-500 font-mono">session: {sessionId.slice(0, 12)}</span>
          </div>
        </div>

        {/* 核心指标 */}
        <div className="flex items-center gap-4 md:gap-6 overflow-x-auto scrollbar-hide">
          {stats && (
            <>
              <MiniStat label="中间价" value={stats.mid_price.toFixed(2)} prefix={market.currencySymbol} large />
              <MiniStat label="价差" value={stats.spread.toFixed(2)} suffix={` (${stats.spread_bps.toFixed(1)}bp)`} />
              <MiniStat label="失衡"
                value={stats.imbalance.toFixed(3)}
                color={stats.imbalance > 0.15 ? 'text-bull' : stats.imbalance < -0.15 ? 'text-bear' : 'text-white'} />
              <MiniStat label="VWAP" value={stats.vwap > 0 ? stats.vwap.toFixed(2) : '--'} prefix={market.currencySymbol} />
            </>
          )}
        </div>

        <button onClick={resetSession}
          className="text-xs text-gray-500 hover:text-white px-3 py-1.5 border border-border rounded-lg transition-colors whitespace-nowrap">
          新建会话
        </button>
      </Card>

      {error && <p className="text-red-400 text-sm bg-red-400/10 px-3 py-2 rounded-lg">{error}</p>}

      {/* ── 主体 ── */}
      <div className="flex-1 grid grid-cols-1 md:grid-cols-12 gap-2 md:gap-3 min-h-0">

        {/* ═══ 左列：盘口深度 ═══ */}
        <Card variant="flat" className="md:col-span-5 overflow-auto">
          {/* 表头 */}
          <div className="sticky top-0 bg-dark-card/95 backdrop-blur-sm px-4 py-2.5 border-b border-border/50 flex items-center gap-2 text-xs text-gray-500">
            <span className="w-20 text-right">价格</span>
            <span className="flex-1" />
            <span className="w-14 text-right">数量</span>
            <span className="w-8 text-right">笔</span>
          </div>

          <div className="px-3 py-2">
            {/* 卖盘（反转显示，最低在下） */}
            <div className="space-y-px mb-1">
              {depth?.asks.slice().reverse().map((level, i) => (
                <DepthRow key={`a${i}`}
                  price={level.price} quantity={level.quantity} orderCount={level.order_count}
                  maxQty={maxQty} side="ask" market={market}
                  onClick={() => { setPrice(level.price.toFixed(2)); setSide('BUY'); }}
                />
              ))}
            </div>

            {/* 价差区域 */}
            <div className="relative py-3 my-1">
              <div className="absolute inset-x-0 top-1/2 -translate-y-1/2 h-px bg-gradient-to-r from-transparent via-border-light to-transparent" />
              <div className="relative flex items-center justify-center gap-3">
                <span className="font-mono text-xl font-bold px-3 py-1 rounded-lg"
                  style={{ color: market.color, backgroundColor: `${market.color}10` }}>
                  {depth?.mid_price.toFixed(2) ?? '--'}
                </span>
                <div className="text-xs text-gray-500">
                  <span>spread </span>
                  <span className="text-gray-400 font-mono">{depth?.spread.toFixed(2) ?? '--'}</span>
                </div>
              </div>
              {/* 脉搏动画 */}
              <div className="absolute left-1/2 top-1/2 -translate-x-1/2 -translate-y-1/2 w-2 h-2 rounded-full animate-ping opacity-30"
                style={{ backgroundColor: market.color }} />
            </div>

            {/* 买盘 */}
            <div className="space-y-px mt-1">
              {depth?.bids.map((level, i) => (
                <DepthRow key={`b${i}`}
                  price={level.price} quantity={level.quantity} orderCount={level.order_count}
                  maxQty={maxQty} side="bid" market={market}
                  onClick={() => { setPrice(level.price.toFixed(2)); setSide('SELL'); }}
                />
              ))}
            </div>
          </div>
        </Card>

        {/* ═══ 中列：成交流水 + 深度图 ═══ */}
        <div className="md:col-span-3 flex flex-col gap-2 md:gap-3">
          {/* 深度图 */}
          <div className="bg-dark-card rounded-xl border border-border p-3 flex-1 min-h-[180px]">
            <h2 className="text-xs font-medium text-gray-500 mb-2">累积深度</h2>
            <DepthChart depth={depth} marketColor={market.color} />
          </div>

          {/* 成交流水 */}
          <Card variant="flat" className="p-3 flex-1 overflow-auto">
            <h2 className="text-xs font-medium text-gray-500 mb-2">成交流水</h2>
            <div className="space-y-px text-xs font-mono">
              <div className="flex gap-2 text-gray-600 pb-1 border-b border-border/30">
                <span className="w-16">价格</span>
                <span className="w-12 text-right">数量</span>
                <span className="flex-1 text-right">方向</span>
              </div>
              {fills.slice().reverse().map((f, i) => (
                <div key={i} className="flex gap-2 items-center py-0.5 hover:bg-white/[0.02] rounded transition-colors">
                  <span className="w-16 text-white">{f.price.toFixed(2)}</span>
                  <span className="w-12 text-right text-gray-400">{f.quantity}</span>
                  <span className={`flex-1 text-right ${f.aggressor_side === 'BUY' ? 'text-bull' : 'text-bear'}`}>
                    {f.aggressor_side === 'BUY' ? '买入' : '卖出'}
                  </span>
                </div>
              ))}
              {fills.length === 0 && <p className="text-gray-600 text-center py-6">暂无成交</p>}
            </div>
          </Card>
        </div>

        {/* ═══ 右列：下单面板 + 做市商 ═══ */}
        <div className="md:col-span-4 flex flex-col gap-2 md:gap-3">
          {/* 下单表单 */}
          <Card variant="flat" className="p-4">
            {/* 买卖切换 */}
            <div className="grid grid-cols-2 gap-1.5 p-1 bg-dark rounded-lg mb-4">
              <button onClick={() => setSide('BUY')}
                className={`py-2.5 rounded-md font-bold text-sm transition-all duration-200
                  ${side === 'BUY'
                    ? 'bg-bull text-white shadow-glow-red'
                    : 'text-gray-500 hover:text-gray-300'}`}>
                买入
              </button>
              <button onClick={() => setSide('SELL')}
                className={`py-2.5 rounded-md font-bold text-sm transition-all duration-200
                  ${side === 'SELL'
                    ? 'bg-bear text-white shadow-glow-green'
                    : 'text-gray-500 hover:text-gray-300'}`}>
                卖出
              </button>
            </div>

            {/* 订单类型 */}
            <div className="mb-3">
              <label className="text-xs text-gray-500 mb-1 block">订单类型</label>
              <div className="grid grid-cols-4 gap-1 p-0.5 bg-dark rounded-lg">
                {['LIMIT', 'MARKET', 'IOC', 'FOK'].map(t => (
                  <button key={t} onClick={() => setOrderType(t)}
                    className={`py-1.5 rounded text-xs font-medium transition-all
                      ${orderType === t
                        ? 'bg-dark-light text-white'
                        : 'text-gray-500 hover:text-gray-300'}`}>
                    {t === 'LIMIT' ? '限价' : t === 'MARKET' ? '市价' : t}
                  </button>
                ))}
              </div>
            </div>

            {/* 快捷价格按钮 */}
            {orderType !== 'MARKET' && quickPrices.length > 0 && (
              <div className="mb-3">
                <label className="text-xs text-gray-500 mb-1 block">快捷价格</label>
                <div className="flex flex-wrap gap-1.5">
                  {quickPrices.map(qp => (
                    <button key={qp.label} onClick={() => setPrice(qp.value.toFixed(2))}
                      className={`px-2.5 py-1 rounded text-xs font-mono border border-border hover:border-border-light transition-colors ${qp.color}`}>
                      {qp.label} {qp.value.toFixed(2)}
                    </button>
                  ))}
                </div>
              </div>
            )}

            {/* 价格输入 */}
            {orderType !== 'MARKET' && (
              <div className="mb-3">
                <label className="text-xs text-gray-500 mb-1 block">
                  价格
                  {market.hasPriceLimit && limitUpPrice && limitDownPrice && (
                    <span className="ml-2 text-gray-600">
                      ({limitDownPrice.toFixed(2)} ~ {limitUpPrice.toFixed(2)})
                    </span>
                  )}
                </label>
                <div className="flex items-center gap-1">
                  <button onClick={() => setPrice(p => (parseFloat(p || '0') - market.tickSize).toFixed(2))}
                    className="w-9 h-9 rounded-lg bg-dark border border-border text-gray-400 hover:text-white hover:border-border-light transition-colors text-lg flex items-center justify-center">
                    -
                  </button>
                  <input value={price} onChange={e => setPrice(e.target.value)}
                    onKeyDown={e => {
                      if (e.key === 'ArrowUp') { e.preventDefault(); setPrice(p => (parseFloat(p || '0') + market.tickSize).toFixed(2)); }
                      if (e.key === 'ArrowDown') { e.preventDefault(); setPrice(p => (parseFloat(p || '0') - market.tickSize).toFixed(2)); }
                    }}
                    className="form-input flex-1 text-center font-mono font-bold text-base" />
                  <button onClick={() => setPrice(p => (parseFloat(p || '0') + market.tickSize).toFixed(2))}
                    className="w-9 h-9 rounded-lg bg-dark border border-border text-gray-400 hover:text-white hover:border-border-light transition-colors text-lg flex items-center justify-center">
                    +
                  </button>
                </div>
              </div>
            )}

            {/* 数量输入 */}
            <div className="mb-3">
              <label className="text-xs text-gray-500 mb-1 block">
                数量
                {market.hasLotRestriction && <span className="ml-1 text-gray-600">(1手={market.lotSize}股)</span>}
              </label>
              <div className="flex items-center gap-1">
                <button onClick={() => setQuantity(q => Math.max(market.lotSize, parseInt(q || '0') - market.lotSize).toString())}
                  className="w-9 h-9 rounded-lg bg-dark border border-border text-gray-400 hover:text-white hover:border-border-light transition-colors text-lg flex items-center justify-center">
                  -
                </button>
                <input value={quantity} onChange={e => setQuantity(e.target.value)}
                  className="form-input flex-1 text-center font-mono font-bold text-base" />
                <button onClick={() => setQuantity(q => (parseInt(q || '0') + market.lotSize).toString())}
                  className="w-9 h-9 rounded-lg bg-dark border border-border text-gray-400 hover:text-white hover:border-border-light transition-colors text-lg flex items-center justify-center">
                  +
                </button>
              </div>
              {/* 快捷手数 */}
              <div className="flex gap-1.5 mt-2">
                {market.quickLots.map(lot => (
                  <button key={lot}
                    onClick={() => setQuantity((lot * market.lotSize).toString())}
                    className={`flex-1 py-1 rounded text-xs font-mono border transition-colors
                      ${parseInt(quantity) === lot * market.lotSize
                        ? 'border-primary/50 text-primary bg-primary/10'
                        : 'border-border text-gray-500 hover:text-gray-300 hover:border-border-light'
                      }`}>
                    {market.hasLotRestriction ? `${lot}手` : `${lot}股`}
                  </button>
                ))}
              </div>
            </div>

            {/* 预估金额 */}
            <div className="bg-dark rounded-lg px-3 py-2 mb-4 text-xs">
              <div className="flex justify-between text-gray-500">
                <span>预估金额</span>
                <span className="font-mono text-gray-300">
                  {market.currencySymbol}
                  {(() => {
                    const p = orderType === 'MARKET'
                      ? (side === 'BUY' ? depth?.asks[0]?.price : depth?.bids[0]?.price) || 0
                      : parseFloat(price) || 0;
                    return (p * (parseInt(quantity) || 0)).toLocaleString('en', { minimumFractionDigits: 2, maximumFractionDigits: 2 });
                  })()}
                </span>
              </div>
            </div>

            {/* 提交按钮 */}
            <button onClick={submitOrder}
              className={`w-full py-3 rounded-xl font-bold text-sm text-white transition-all duration-200
                ${side === 'BUY'
                  ? 'bg-bull hover:bg-bull-dark shadow-glow-red'
                  : 'bg-bear hover:bg-bear-dark shadow-glow-green'}`}>
              {side === 'BUY' ? '买入' : '卖出'} {quantity} 股
            </button>
          </Card>

          {/* 撮合结果 */}
          {lastResult && (
            <Card variant="flat" className="p-3 text-xs">
              <h2 className="text-xs font-medium text-gray-500 mb-2">上笔撮合</h2>
              <div className="space-y-1 text-gray-400">
                <div className="flex justify-between">
                  <span>成交</span>
                  <span className="text-white font-mono">{lastResult.filled_quantity} 股</span>
                </div>
                <div className="flex justify-between">
                  <span>剩余</span>
                  <span className="text-white font-mono">{lastResult.remaining_quantity} 股</span>
                </div>
                {lastResult.fills.length > 0 && (
                  <div className="pt-1 mt-1 border-t border-border/30 space-y-0.5 font-mono text-gray-500">
                    {lastResult.fills.map((f, i) => (
                      <div key={i} className="flex justify-between">
                        <span>{f.price.toFixed(2)}</span>
                        <span>\u00D7 {f.quantity}</span>
                      </div>
                    ))}
                  </div>
                )}
              </div>
            </Card>
          )}

          {/* ═══ 做市商控制面板 ═══ */}
          <Card variant="flat" className="p-4">
            <div className="flex items-center justify-between mb-3">
              <h2 className="text-xs font-medium text-gray-400 flex items-center gap-2">
                <span className="w-2 h-2 rounded-full" style={{ backgroundColor: mmStatus?.running ? '#F59E0B' : '#6B7280' }} />
                做市商 Bot
              </h2>
              <button
                onClick={toggleMarketMaker}
                disabled={mmLoading}
                className={`px-3 py-1 rounded-lg text-xs font-medium transition-all disabled:opacity-50
                  ${mmStatus?.running
                    ? 'bg-red-500/20 text-red-400 hover:bg-red-500/30 border border-red-500/30'
                    : 'bg-amber-500/20 text-amber-400 hover:bg-amber-500/30 border border-amber-500/30'
                  }`}
              >
                {mmLoading ? '...' : mmStatus?.running ? '停止' : '启动'}
              </button>
            </div>

            {/* 参数配置（未运行时可调） */}
            {!mmStatus?.running && (
              <div className="grid grid-cols-2 gap-2 mb-3">
                <div>
                  <label className="text-[10px] text-gray-600 block">波动率</label>
                  <input value={mmVolatility} onChange={e => setMmVolatility(e.target.value)}
                    className="form-input text-xs font-mono py-1" />
                </div>
                <div>
                  <label className="text-[10px] text-gray-600 block">价格漂移</label>
                  <input value={mmDrift} onChange={e => setMmDrift(e.target.value)}
                    className="form-input text-xs font-mono py-1" />
                </div>
                <div>
                  <label className="text-[10px] text-gray-600 block">半价差(ticks)</label>
                  <input value={mmSpreadTicks} onChange={e => setMmSpreadTicks(e.target.value)}
                    className="form-input text-xs font-mono py-1" />
                </div>
                <div>
                  <label className="text-[10px] text-gray-600 block">首档数量</label>
                  <input value={mmBaseQty} onChange={e => setMmBaseQty(e.target.value)}
                    className="form-input text-xs font-mono py-1" />
                </div>
              </div>
            )}

            {/* 运行状态 */}
            {mmStatus?.running && (
              <div className="space-y-1.5 text-xs">
                <div className="flex justify-between text-gray-500">
                  <span>净持仓</span>
                  <span className={`font-mono font-medium ${mmStatus.net_position > 0 ? 'text-bull' : mmStatus.net_position < 0 ? 'text-bear' : 'text-white'}`}>
                    {mmStatus.net_position > 0 ? '+' : ''}{mmStatus.net_position}
                  </span>
                </div>
                <div className="flex justify-between text-gray-500">
                  <span>挂单</span>
                  <span className="font-mono text-gray-300">
                    {mmStatus.active_bid_orders}B / {mmStatus.active_ask_orders}A
                  </span>
                </div>
                <div className="flex justify-between text-gray-500">
                  <span>成交笔数</span>
                  <span className="font-mono text-gray-300">{mmStatus.total_trades}</span>
                </div>
                <div className="flex justify-between text-gray-500">
                  <span>估算 PnL</span>
                  <span className={`font-mono font-medium ${mmStatus.pnl >= 0 ? 'text-bull' : 'text-bear'}`}>
                    {mmStatus.pnl >= 0 ? '+' : ''}{mmStatus.pnl.toFixed(2)}
                  </span>
                </div>
                <div className="flex justify-between text-gray-500">
                  <span>目标中间价</span>
                  <span className="font-mono text-gray-300">{mmStatus.target_mid.toFixed(2)}</span>
                </div>
                <div className="flex justify-between text-gray-500">
                  <span>运行时长</span>
                  <span className="font-mono text-gray-300">{formatUptime(mmStatus.uptime_seconds)}</span>
                </div>
              </div>
            )}
          </Card>
        </div>
      </div>

      {/* ── 底部统计 ── */}
      {stats && (
        <Card variant="flat" className="px-4 py-2.5 flex flex-wrap items-center gap-4 md:gap-6 text-xs">
          <StatChip label="买盘深度" value={stats.bid_depth.toLocaleString()} icon="\u25B2" iconColor="text-bull" />
          <StatChip label="卖盘深度" value={stats.ask_depth.toLocaleString()} icon="\u25BC" iconColor="text-bear" />
          <StatChip label="总成交量" value={stats.total_volume.toLocaleString()} />
          <StatChip label="成交笔数" value={stats.total_fills.toString()} />
        </Card>
      )}

      {/* ── Toast 通知 ── */}
      <div className="fixed top-4 right-4 z-50 flex flex-col gap-2 w-80">
        {toasts.map(t => (
          <Toast key={t.id} toast={t} onClose={() => removeToast(t.id)} />
        ))}
      </div>
    </div>
  );
};

// ══════════════════════════════════════════════════════════════
// 工具函数
// ══════════════════════════════════════════════════════════════

function formatUptime(seconds: number): string {
  if (seconds < 60) return `${Math.floor(seconds)}s`;
  if (seconds < 3600) return `${Math.floor(seconds / 60)}m ${Math.floor(seconds % 60)}s`;
  return `${Math.floor(seconds / 3600)}h ${Math.floor((seconds % 3600) / 60)}m`;
}

// ══════════════════════════════════════════════════════════════
// 子组件
// ══════════════════════════════════════════════════════════════

// 盘口深度行
const DepthRow: React.FC<{
  price: number; quantity: number; orderCount: number;
  maxQty: number; side: 'bid' | 'ask'; market: MarketPreset;
  onClick: () => void;
}> = ({ price, quantity, orderCount, maxQty, side, onClick }) => {
  const pct = (quantity / maxQty) * 100;
  const isBid = side === 'bid';

  return (
    <div
      onClick={onClick}
      className="relative flex items-center gap-2 text-xs h-7 px-1 rounded cursor-pointer group hover:bg-white/[0.03] transition-colors"
    >
      {/* 深度条 */}
      <div className="absolute inset-y-0 rounded"
        style={{
          [isBid ? 'left' : 'right']: 0,
          width: `${pct}%`,
          background: isBid
            ? 'linear-gradient(90deg, rgba(239,68,68,0.15), rgba(239,68,68,0.05))'
            : 'linear-gradient(270deg, rgba(16,185,129,0.15), rgba(16,185,129,0.05))',
        }}
      />

      <span className={`relative w-20 text-right font-mono font-medium group-hover:underline
        ${isBid ? 'text-bull' : 'text-bear'}`}>
        {price.toFixed(2)}
      </span>
      <span className="relative flex-1" />
      <span className="relative w-14 text-right text-gray-400 font-mono">{quantity}</span>
      <span className="relative w-8 text-right text-gray-600">{orderCount}</span>
    </div>
  );
};

// 累积深度图（Canvas）
const DepthChart: React.FC<{ depth: DepthSnapshot | null; marketColor: string }> = ({ depth, marketColor }) => {
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const containerRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    const canvas = canvasRef.current;
    const container = containerRef.current;
    if (!canvas || !container || !depth) return;

    const rect = container.getBoundingClientRect();
    const w = rect.width;
    const h = rect.height;
    const dpr = window.devicePixelRatio || 1;
    canvas.width = w * dpr;
    canvas.height = h * dpr;
    canvas.style.width = `${w}px`;
    canvas.style.height = `${h}px`;

    const ctx = canvas.getContext('2d');
    if (!ctx) return;
    ctx.scale(dpr, dpr);
    ctx.clearRect(0, 0, w, h);

    // 计算累积深度
    const bids = depth.bids.map((b, i) => ({
      price: b.price,
      cumQty: depth.bids.slice(0, i + 1).reduce((s, x) => s + x.quantity, 0),
    }));
    const asks = depth.asks.map((a, i) => ({
      price: a.price,
      cumQty: depth.asks.slice(0, i + 1).reduce((s, x) => s + x.quantity, 0),
    }));

    if (bids.length === 0 && asks.length === 0) return;

    const allPrices = [...bids.map(b => b.price), ...asks.map(a => a.price)];
    const minP = Math.min(...allPrices);
    const maxP = Math.max(...allPrices);
    const priceRange = maxP - minP || 1;
    const maxCum = Math.max(
      bids.length > 0 ? bids[bids.length - 1].cumQty : 0,
      asks.length > 0 ? asks[asks.length - 1].cumQty : 0,
      1
    );

    const px = (price: number) => ((price - minP) / priceRange) * w;
    const py = (qty: number) => h - (qty / maxCum) * (h - 10);

    // 买盘面积
    if (bids.length > 0) {
      ctx.beginPath();
      ctx.moveTo(px(bids[0].price), h);
      bids.forEach(b => ctx.lineTo(px(b.price), py(b.cumQty)));
      ctx.lineTo(px(bids[bids.length - 1].price), h);
      ctx.closePath();
      const grad = ctx.createLinearGradient(0, 0, 0, h);
      grad.addColorStop(0, 'rgba(239,68,68,0.3)');
      grad.addColorStop(1, 'rgba(239,68,68,0.02)');
      ctx.fillStyle = grad;
      ctx.fill();
      // 线条
      ctx.beginPath();
      bids.forEach((b, i) => i === 0 ? ctx.moveTo(px(b.price), py(b.cumQty)) : ctx.lineTo(px(b.price), py(b.cumQty)));
      ctx.strokeStyle = 'rgba(239,68,68,0.6)';
      ctx.lineWidth = 1.5;
      ctx.stroke();
    }

    // 卖盘面积
    if (asks.length > 0) {
      ctx.beginPath();
      ctx.moveTo(px(asks[0].price), h);
      asks.forEach(a => ctx.lineTo(px(a.price), py(a.cumQty)));
      ctx.lineTo(px(asks[asks.length - 1].price), h);
      ctx.closePath();
      const grad = ctx.createLinearGradient(0, 0, 0, h);
      grad.addColorStop(0, 'rgba(16,185,129,0.3)');
      grad.addColorStop(1, 'rgba(16,185,129,0.02)');
      ctx.fillStyle = grad;
      ctx.fill();
      ctx.beginPath();
      asks.forEach((a, i) => i === 0 ? ctx.moveTo(px(a.price), py(a.cumQty)) : ctx.lineTo(px(a.price), py(a.cumQty)));
      ctx.strokeStyle = 'rgba(16,185,129,0.6)';
      ctx.lineWidth = 1.5;
      ctx.stroke();
    }

    // 中间价竖线
    const midX = px(depth.mid_price);
    ctx.beginPath();
    ctx.moveTo(midX, 0);
    ctx.lineTo(midX, h);
    ctx.strokeStyle = 'rgba(255,255,255,0.15)';
    ctx.lineWidth = 1;
    ctx.setLineDash([4, 4]);
    ctx.stroke();
    ctx.setLineDash([]);
  }, [depth, marketColor]);

  return (
    <div ref={containerRef} className="w-full h-full min-h-[120px]">
      <canvas ref={canvasRef} className="w-full h-full" />
    </div>
  );
};

// 顶栏迷你统计
const MiniStat: React.FC<{
  label: string; value: string; prefix?: string; suffix?: string;
  color?: string; large?: boolean;
}> = ({ label, value, prefix, suffix, color = 'text-white', large }) => (
  <div className="flex flex-col whitespace-nowrap">
    <span className="text-[10px] text-gray-500 leading-tight">{label}</span>
    <span className={`font-mono ${color} ${large ? 'text-base font-bold' : 'text-xs'}`}>
      {prefix}{value}{suffix}
    </span>
  </div>
);

// 底部统计小卡
const StatChip: React.FC<{
  label: string; value: string; icon?: string; iconColor?: string;
}> = ({ label, value, icon, iconColor = 'text-gray-400' }) => (
  <div className="flex items-center gap-2 bg-dark/50 rounded-lg px-3 py-1.5">
    {icon && <span className={`text-xs ${iconColor}`}>{icon}</span>}
    <span className="text-gray-500">{label}</span>
    <span className="text-white font-mono font-medium">{value}</span>
  </div>
);

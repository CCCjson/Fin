import React, { useState, useCallback, useRef, useEffect } from 'react';

const API = 'http://localhost:8000';

// ── 类型定义 ──

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

// ── 组件 ──

export const OrderBook: React.FC = () => {
  const [sessionId, setSessionId] = useState('');
  const [depth, setDepth] = useState<DepthSnapshot | null>(null);
  const [fills, setFills] = useState<Fill[]>([]);
  const [stats, setStats] = useState<BookStats | null>(null);
  const [lastResult, setLastResult] = useState<MatchResult | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState('');

  // 下单表单
  const [side, setSide] = useState<'BUY' | 'SELL'>('BUY');
  const [orderType, setOrderType] = useState('LIMIT');
  const [price, setPrice] = useState('');
  const [quantity, setQuantity] = useState('100');

  // 创建会话参数
  const [symbol, setSymbol] = useState('AAPL');
  const [midPrice, setMidPrice] = useState('100.00');
  const [seedCount, setSeedCount] = useState('200');

  const intervalRef = useRef<ReturnType<typeof setInterval>>();

  // ── 自动刷新 ──
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
    } catch {
      // 静默处理轮询失败
    }
  }, [sessionId]);

  useEffect(() => {
    if (sessionId) {
      refreshData();
      intervalRef.current = setInterval(refreshData, 2000);
    }
    return () => { if (intervalRef.current) clearInterval(intervalRef.current); };
  }, [sessionId, refreshData]);

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
        }),
      });
      if (!res.ok) {
        const err = await res.json();
        throw new Error(err.detail || 'Failed');
      }
      const data = await res.json();
      setSessionId(data.session_id);
      setPrice(midPrice);
    } catch (e: any) {
      setError(e.message);
    } finally {
      setLoading(false);
    }
  };

  // ── 提交订单 ──
  const submitOrder = async () => {
    if (!sessionId) return;
    setError('');
    try {
      const res = await fetch(`${API}/orderbook/sessions/${sessionId}/orders`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          side,
          order_type: orderType,
          price: orderType === 'MARKET' ? 0 : parseFloat(price),
          quantity: parseInt(quantity),
        }),
      });
      const data = await res.json();
      if (!res.ok) throw new Error(data.detail || data.error || 'Failed');
      setLastResult(data);
      refreshData();
    } catch (e: any) {
      setError(e.message);
    }
  };

  // ── 撤单 ──
  const cancelOrder = async (orderId: string) => {
    if (!sessionId) return;
    try {
      await fetch(`${API}/orderbook/sessions/${sessionId}/orders/${orderId}`, {
        method: 'DELETE',
      });
      refreshData();
    } catch {}
  };

  // ── 盘口深度条的最大量 ──
  const maxQty = depth
    ? Math.max(
        ...depth.bids.map(b => b.quantity),
        ...depth.asks.map(a => a.quantity),
        1
      )
    : 1;

  // ── 渲染 ──

  // 如果还没有会话，显示创建表单
  if (!sessionId) {
    return (
      <div className="p-6 max-w-xl mx-auto mt-20">
        <h1 className="text-2xl font-bold text-white mb-6">订单簿模拟器</h1>
        <p className="text-gray-400 mb-6">
          创建一个模拟交易会话，体验限价订单簿的撮合逻辑。
          底层由独立的 C++ 服务驱动。
        </p>
        <div className="space-y-4 bg-dark-card p-6 rounded-xl border border-border">
          <div>
            <label className="text-sm text-gray-400">股票代码</label>
            <input value={symbol} onChange={e => setSymbol(e.target.value)}
              className="w-full mt-1 p-2 bg-dark rounded border border-border text-white" />
          </div>
          <div className="grid grid-cols-2 gap-4">
            <div>
              <label className="text-sm text-gray-400">中间价</label>
              <input value={midPrice} onChange={e => setMidPrice(e.target.value)}
                className="w-full mt-1 p-2 bg-dark rounded border border-border text-white" />
            </div>
            <div>
              <label className="text-sm text-gray-400">初始订单数</label>
              <input value={seedCount} onChange={e => setSeedCount(e.target.value)}
                className="w-full mt-1 p-2 bg-dark rounded border border-border text-white" />
            </div>
          </div>
          <button onClick={createSession} disabled={loading}
            className="w-full py-3 bg-primary hover:bg-primary/80 text-white rounded-lg font-medium disabled:opacity-50">
            {loading ? '创建中...' : '创建模拟会话'}
          </button>
          {error && <p className="text-red-400 text-sm">{error}</p>}
        </div>
      </div>
    );
  }

  return (
    <div className="p-4 h-full flex flex-col gap-4 overflow-auto">
      {/* 头部 */}
      <div className="flex items-center justify-between">
        <div className="flex items-center gap-4">
          <h1 className="text-xl font-bold text-white">订单簿 — {symbol}</h1>
          <span className="text-xs text-gray-500 font-mono">session: {sessionId.slice(0, 12)}</span>
        </div>
        <button onClick={() => { setSessionId(''); setDepth(null); setFills([]); setStats(null); }}
          className="text-sm text-gray-400 hover:text-white px-3 py-1 border border-border rounded">
          新建会话
        </button>
      </div>

      {error && <p className="text-red-400 text-sm bg-red-400/10 px-3 py-2 rounded">{error}</p>}

      {/* 主体：三列 */}
      <div className="flex-1 grid grid-cols-12 gap-4 min-h-0">

        {/* 左列：买卖盘深度 */}
        <div className="col-span-5 bg-dark-card rounded-xl border border-border p-4 overflow-auto">
          <h2 className="text-sm font-medium text-gray-400 mb-3">盘口深度</h2>

          {/* 卖盘（从高到低显示，最低价在最下面靠近中间） */}
          <div className="space-y-1 mb-2">
            {depth?.asks.slice().reverse().map((level, i) => (
              <div key={`a${i}`} className="flex items-center gap-2 text-sm h-7">
                <span className="w-16 text-right text-green-400 font-mono">{level.price.toFixed(2)}</span>
                <div className="flex-1 flex justify-end">
                  <div className="bg-green-500/20 h-5 rounded-sm"
                    style={{ width: `${(level.quantity / maxQty) * 100}%`, minWidth: 2 }} />
                </div>
                <span className="w-16 text-right text-gray-400 font-mono">{level.quantity}</span>
                <span className="w-8 text-right text-gray-600 text-xs">{level.order_count}</span>
              </div>
            ))}
          </div>

          {/* 价差 */}
          <div className="border-t border-b border-border/50 py-2 my-2 text-center">
            <span className="text-yellow-400 font-mono text-lg">
              {depth?.mid_price.toFixed(2) ?? '—'}
            </span>
            <span className="text-gray-500 text-xs ml-2">
              spread: {depth?.spread.toFixed(2) ?? '—'}
            </span>
          </div>

          {/* 买盘（从高到低） */}
          <div className="space-y-1 mt-2">
            {depth?.bids.map((level, i) => (
              <div key={`b${i}`} className="flex items-center gap-2 text-sm h-7">
                <span className="w-16 text-right text-red-400 font-mono">{level.price.toFixed(2)}</span>
                <div className="flex-1">
                  <div className="bg-red-500/20 h-5 rounded-sm"
                    style={{ width: `${(level.quantity / maxQty) * 100}%`, minWidth: 2 }} />
                </div>
                <span className="w-16 text-right text-gray-400 font-mono">{level.quantity}</span>
                <span className="w-8 text-right text-gray-600 text-xs">{level.order_count}</span>
              </div>
            ))}
          </div>
        </div>

        {/* 中列：成交流水 */}
        <div className="col-span-3 bg-dark-card rounded-xl border border-border p-4 overflow-auto">
          <h2 className="text-sm font-medium text-gray-400 mb-3">成交流水</h2>
          <div className="space-y-1 text-xs font-mono">
            <div className="flex gap-2 text-gray-500 mb-2">
              <span className="w-16">价格</span>
              <span className="w-12 text-right">数量</span>
              <span className="flex-1 text-right">方向</span>
            </div>
            {fills.slice().reverse().map((f, i) => (
              <div key={i} className="flex gap-2 items-center">
                <span className="w-16 text-white">{f.price.toFixed(2)}</span>
                <span className="w-12 text-right text-gray-400">{f.quantity}</span>
                <span className={`flex-1 text-right ${f.aggressor_side === 'BUY' ? 'text-red-400' : 'text-green-400'}`}>
                  {f.aggressor_side === 'BUY' ? '买入' : '卖出'}
                </span>
              </div>
            ))}
            {fills.length === 0 && <p className="text-gray-600 text-center py-4">暂无成交</p>}
          </div>
        </div>

        {/* 右列：下单面板 */}
        <div className="col-span-4 flex flex-col gap-4">
          {/* 下单表单 */}
          <div className="bg-dark-card rounded-xl border border-border p-4">
            <h2 className="text-sm font-medium text-gray-400 mb-3">提交订单</h2>

            {/* 方向选择 */}
            <div className="grid grid-cols-2 gap-2 mb-3">
              <button onClick={() => setSide('BUY')}
                className={`py-2 rounded font-medium text-sm ${side === 'BUY' ? 'bg-red-500 text-white' : 'bg-dark text-gray-400 border border-border'}`}>
                买入
              </button>
              <button onClick={() => setSide('SELL')}
                className={`py-2 rounded font-medium text-sm ${side === 'SELL' ? 'bg-green-500 text-white' : 'bg-dark text-gray-400 border border-border'}`}>
                卖出
              </button>
            </div>

            {/* 类型选择 */}
            <div className="mb-3">
              <label className="text-xs text-gray-500">订单类型</label>
              <select value={orderType} onChange={e => setOrderType(e.target.value)}
                className="w-full mt-1 p-2 bg-dark rounded border border-border text-white text-sm">
                <option value="MARKET">MARKET (市价)</option>
                <option value="LIMIT">LIMIT (限价)</option>
                <option value="IOC">IOC (即时取消)</option>
                <option value="FOK">FOK (全填或杀)</option>
              </select>
            </div>

            {/* 价格 */}
            {orderType !== 'MARKET' && (
              <div className="mb-3">
                <label className="text-xs text-gray-500">价格</label>
                <input value={price} onChange={e => setPrice(e.target.value)}
                  className="w-full mt-1 p-2 bg-dark rounded border border-border text-white text-sm font-mono"
                  placeholder="100.00" />
              </div>
            )}

            {/* 数量 */}
            <div className="mb-3">
              <label className="text-xs text-gray-500">数量</label>
              <input value={quantity} onChange={e => setQuantity(e.target.value)}
                className="w-full mt-1 p-2 bg-dark rounded border border-border text-white text-sm font-mono"
                placeholder="100" />
            </div>

            <button onClick={submitOrder}
              className={`w-full py-2.5 rounded font-medium text-sm text-white ${side === 'BUY' ? 'bg-red-500 hover:bg-red-600' : 'bg-green-500 hover:bg-green-600'}`}>
              {side === 'BUY' ? '买入' : '卖出'} {quantity} 股
            </button>
          </div>

          {/* 上次撮合结果 */}
          {lastResult && (
            <div className="bg-dark-card rounded-xl border border-border p-4 text-sm">
              <h2 className="text-sm font-medium text-gray-400 mb-2">撮合结果</h2>
              <div className="space-y-1 text-gray-300">
                <p>成交: <span className="text-white font-mono">{lastResult.filled_quantity}</span> 股</p>
                <p>剩余: <span className="text-white font-mono">{lastResult.remaining_quantity}</span> 股</p>
                <p>成交笔数: <span className="text-white font-mono">{lastResult.fills.length}</span></p>
                {lastResult.is_resting && <p className="text-yellow-400">剩余已挂单</p>}
                {lastResult.is_rejected && <p className="text-red-400">订单被拒绝 (FOK 量不够)</p>}
                {lastResult.fills.length > 0 && (
                  <div className="mt-2 space-y-0.5 font-mono text-xs">
                    {lastResult.fills.map((f, i) => (
                      <p key={i} className="text-gray-400">
                        {f.price.toFixed(2)} × {f.quantity}
                      </p>
                    ))}
                  </div>
                )}
              </div>
            </div>
          )}
        </div>
      </div>

      {/* 底部：市场统计 */}
      {stats && (
        <div className="bg-dark-card rounded-xl border border-border px-6 py-3 flex items-center gap-8 text-sm">
          <Stat label="价差" value={stats.spread.toFixed(2)} />
          <Stat label="价差(bps)" value={stats.spread_bps.toFixed(1)} />
          <Stat label="买盘深度" value={stats.bid_depth.toLocaleString()} />
          <Stat label="卖盘深度" value={stats.ask_depth.toLocaleString()} />
          <Stat label="买卖失衡" value={stats.imbalance.toFixed(3)}
            color={stats.imbalance > 0 ? 'text-red-400' : stats.imbalance < 0 ? 'text-green-400' : 'text-white'} />
          <Stat label="VWAP" value={stats.vwap > 0 ? stats.vwap.toFixed(2) : '—'} />
          <Stat label="总成交量" value={stats.total_volume.toLocaleString()} />
          <Stat label="成交笔数" value={stats.total_fills.toString()} />
        </div>
      )}
    </div>
  );
};

// 统计小组件
const Stat: React.FC<{ label: string; value: string; color?: string }> = ({
  label, value, color = 'text-white'
}) => (
  <div>
    <span className="text-gray-500 mr-2">{label}</span>
    <span className={`font-mono ${color}`}>{value}</span>
  </div>
);

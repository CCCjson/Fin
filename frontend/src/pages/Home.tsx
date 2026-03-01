import React, { useState, useEffect, useRef } from 'react';
import { useNavigate } from 'react-router-dom';
import { useAuthStore } from '../stores/authStore';
import { LoginOverlay } from '../components/LoginOverlay';
import { ParticleBackground } from '../components/ParticleBackground';
import api from '../services/api';

/* ================================================================
   数据定义
   ================================================================ */

interface MarketModule {
  id: string;
  name: string;
  subtitle: string;
  icon: string;
  accentColor: string;
  available: boolean;
  route: string;
  // 轨道参数
  orbitRadius: number;   // px — 各不相同
  orbitSpeed: number;     // 秒/圈
  orbitDirection: 1 | -1; // 1=顺时针, -1=逆时针
  startAngle: number;     // 初始角度(deg)
}

const markets: MarketModule[] = [
  {
    id: 'a-stock', name: 'A 股', subtitle: '中国大陆证券市场',
    icon: '🇨🇳', accentColor: '#EF4444', available: true, route: '/app/realtime',
    orbitRadius: 130, orbitSpeed: 50, orbitDirection: 1, startAngle: -90,
  },
  {
    id: 'futures', name: '期 货', subtitle: '商品与金融衍生品',
    icon: '📈', accentColor: '#F59E0B', available: false, route: '',
    orbitRadius: 200, orbitSpeed: 70, orbitDirection: -1, startAngle: 45,
  },
  {
    id: 'us-stock', name: '美 股', subtitle: '美国证券市场',
    icon: '🇺🇸', accentColor: '#3B82F6', available: false, route: '',
    orbitRadius: 250, orbitSpeed: 60, orbitDirection: 1, startAngle: 160,
  },
  {
    id: 'hk-stock', name: '港 股', subtitle: '香港证券市场',
    icon: '🇭🇰', accentColor: '#8B5CF6', available: false, route: '',
    orbitRadius: 300, orbitSpeed: 80, orbitDirection: -1, startAngle: 220,
  },
  {
    id: 'trading', name: '交 易', subtitle: '半自动交易系统',
    icon: '💰', accentColor: '#10B981', available: true, route: '/trading',
    orbitRadius: 225, orbitSpeed: 55, orbitDirection: -1, startAngle: 0,
  },
  {
    id: 'alpha-lab', name: 'Alpha Lab', subtitle: 'AI 自动策略探索',
    icon: '🧬', accentColor: '#A855F7', available: true, route: '/alpha-lab',
    orbitRadius: 275, orbitSpeed: 90, orbitDirection: 1, startAngle: 280,
  },
];

interface FeaturePlanet {
  id: string;
  name: string;
  icon: string;
  route: string;
  color: string;
}

const featureMap: Record<string, FeaturePlanet[]> = {
  'a-stock': [
    { id: 'realtime', name: '实时行情', icon: '⚡', route: '/app/realtime', color: '#F59E0B' },
    { id: 'market', name: 'K线分析', icon: '📈', route: '/app/market', color: '#3B82F6' },
    { id: 'signals', name: '信号分析', icon: '🎯', route: '/app/signals', color: '#EF4444' },
    { id: 'advisor', name: 'AI 顾问', icon: '💬', route: '/app/advisor', color: '#8B5CF6' },
    { id: 'reports', name: 'AI 报告', icon: '🤖', route: '/app/reports', color: '#A855F7' },
    { id: 'backtest', name: '策略回测', icon: '🔬', route: '/app/backtest', color: '#06B6D4' },
    { id: 'tracking', name: '信号追踪', icon: '📋', route: '/app/tracking', color: '#10B981' },
    { id: 'review', name: '每日复盘', icon: '📝', route: '/app/review', color: '#F97316' },
    { id: 'portfolio', name: '交易记录', icon: '📒', route: '/app/portfolio', color: '#14B8A6' },
    { id: 'orderbook', name: '订单簿', icon: '📊', route: '/app/orderbook', color: '#6366F1' },
    { id: 'pipeline', name: '数据管道', icon: '🚀', route: '/app/pipeline', color: '#EC4899' },
    { id: 'prediction', name: '股价预测', icon: '🔮', route: '/app/prediction', color: '#8B5CF6' },
    { id: 'news', name: '新闻分析', icon: '📰', route: '/app/news', color: '#0EA5E9' },
  ],
};

// 功能行星布局：内圈5 + 外圈8
function getFeaturePositions(inner: number, outer: number, r1: number, r2: number) {
  const out: { x: number; y: number; ring: number }[] = [];
  for (let i = 0; i < inner; i++) {
    const a = (i / inner) * Math.PI * 2 - Math.PI / 2;
    out.push({ x: Math.cos(a) * r1, y: Math.sin(a) * r1, ring: 1 });
  }
  for (let i = 0; i < outer; i++) {
    const a = (i / outer) * Math.PI * 2 - Math.PI / 2 + Math.PI / outer;
    out.push({ x: Math.cos(a) * r2, y: Math.sin(a) * r2, ring: 2 });
  }
  return out;
}

const featurePositions = getFeaturePositions(5, 8, 135, 250);

/* ================================================================
   Ticker Bar（行情滚动条）
   ================================================================ */

interface TickerItem { name: string; price: number | null; change_pct: number | null; }

const TickerBar: React.FC = () => {
  const [items, setItems] = useState<TickerItem[]>([]);
  const [isTrading, setIsTrading] = useState(true);

  useEffect(() => {
    (async () => {
      try {
        const res: any = await api.get('/realtime/ticker');
        if (res.success && res.data?.length) { setItems(res.data); setIsTrading(res.is_trading); }
      } catch { /* 静默 */ }
    })();
  }, []);

  if (!items.length) return null;

  const renderItem = (t: TickerItem, i: number) => {
    const up = (t.change_pct ?? 0) >= 0;
    return (
      <span key={i} className="inline-flex items-center gap-2 text-xs md:text-sm flex-shrink-0">
        <span className="text-gray-400 font-medium">{t.name}</span>
        <span className="text-gray-300">{t.price != null ? t.price.toLocaleString() : '--'}</span>
        <span className={up ? 'text-bull' : 'text-bear'}>
          {t.change_pct != null ? `${up ? '+' : ''}${t.change_pct.toFixed(2)}%` : '--'}
        </span>
        {!isTrading && <span title="休市中">🙅</span>}
      </span>
    );
  };

  return (
    <div className="absolute top-4 left-0 right-0 z-30 overflow-hidden opacity-0"
      style={{ animation: 'home-fadein 0.6s ease 0.8s both' }}>
      <div className="animate-ticker flex whitespace-nowrap" style={{ width: 'max-content' }}>
        <div className="flex gap-8 flex-shrink-0 pr-8">{items.map((t, i) => renderItem(t, i))}</div>
        <div className="flex gap-8 flex-shrink-0 pr-8">{items.map((t, i) => renderItem(t, i + items.length))}</div>
      </div>
    </div>
  );
};

/* ================================================================
   CSS 动画（<style> 注入）
   ================================================================ */

const DynamicStyles: React.FC = () => (
  <style>{`
    @keyframes home-fadein {
      from { opacity: 0; }
      to { opacity: 1; }
    }
    @keyframes home-fadeup {
      from { opacity: 0; transform: translateY(20px); }
      to { opacity: 1; transform: translateY(0); }
    }



    /* 轨道公转 — 每个市场模块独立 */
    ${markets.map((m, i) => `
      @keyframes orbit-${i} {
        from { transform: rotate(${m.startAngle}deg) translateX(${m.orbitRadius}px) rotate(${-m.startAngle}deg); }
        to   { transform: rotate(${m.startAngle + 360 * m.orbitDirection}deg) translateX(${m.orbitRadius}px) rotate(${-m.startAngle - 360 * m.orbitDirection}deg); }
      }
    `).join('')}

    /* 连线呼吸 */
    @keyframes line-breathe {
      0%, 100% { opacity: 0.3; }
      50% { opacity: 0.7; }
    }

    /* 呼吸光环 */
    @keyframes breath-ring {
      0%, 100% { transform: scale(1); opacity: 0.5; }
      50% { transform: scale(1.15); opacity: 0; }
    }

    /* 太阳发光 */
    @keyframes sun-glow {
      0%, 100% { box-shadow: 0 0 40px var(--glow-color, #EF444430), 0 0 80px var(--glow-color2, #EF444415); }
      50% { box-shadow: 0 0 60px var(--glow-color, #EF444445), 0 0 100px var(--glow-color2, #EF444425); }
    }

    /* 功能行星浮动 */
    @keyframes feat-float-0 { 0%,100% { transform: translate(0,0); } 50% { transform: translate(3px,-5px); } }
    @keyframes feat-float-1 { 0%,100% { transform: translate(0,0); } 50% { transform: translate(-4px,-3px); } }
    @keyframes feat-float-2 { 0%,100% { transform: translate(0,0); } 50% { transform: translate(4px,4px); } }
    @keyframes feat-float-3 { 0%,100% { transform: translate(0,0); } 50% { transform: translate(-3px,5px); } }
  `}</style>
);

/* ================================================================
   主页组件
   ================================================================ */

type ViewMode = 'home' | 'zooming' | 'features';

export const Home: React.FC = () => {
  const navigate = useNavigate();
  const { isAuthenticated, username, loading, checkAuth } = useAuthStore();

  useEffect(() => { checkAuth(); }, []);

  // 视图状态
  const [viewMode, setViewMode] = useState<ViewMode>('home');
  const [activeMarket, setActiveMarket] = useState<MarketModule | null>(null);
  const [hoveredMarket, setHoveredMarket] = useState<number | null>(null);
  const [hoveredFeature, setHoveredFeature] = useState<number | null>(null);
  const [featuresPhase, setFeaturesPhase] = useState(0); // 0=未展开 1=展开中 2=稳定

  // 每个市场模块当前角度（冻结公转用）
  const orbitStartTimeRef = useRef(Date.now());
  const [frozenAngles, setFrozenAngles] = useState<number[]>([]);

  // 计算某个模块当前公转角度
  const calcCurrentAngle = (m: MarketModule) => {
    const elapsed = (Date.now() - orbitStartTimeRef.current) / 1000;
    return m.startAngle + (360 * m.orbitDirection * elapsed) / m.orbitSpeed;
  };

  // 计算某个模块当前的屏幕相对中心偏移
  const getModuleOffset = (m: MarketModule, angleDeg: number) => {
    const rad = (angleDeg * Math.PI) / 180;
    return { x: Math.cos(rad) * m.orbitRadius, y: Math.sin(rad) * m.orbitRadius };
  };

  // 点击市场模块
  const handleMarketClick = (market: MarketModule, index: number) => {
    if (!market.available || !isAuthenticated || viewMode !== 'home') return;

    // 没有功能映射的市场，直接导航
    if (!featureMap[market.id]) {
      navigate(market.route);
      return;
    }

    // 冻结所有模块的当前角度
    const angles = markets.map(m => calcCurrentAngle(m));
    setFrozenAngles(angles);

    setActiveMarket(market);
    setViewMode('zooming');

    // 推镜头完成后展开功能行星（给足时间让镜头推进丝滑完成）
    setTimeout(() => {
      setViewMode('features');
      setTimeout(() => setFeaturesPhase(1), 150);
      setTimeout(() => setFeaturesPhase(2), 1000);
    }, 1200);
  };

  // 返回主页
  const handleBack = () => {
    setFeaturesPhase(0);
    setTimeout(() => {
      setViewMode('home');
      setActiveMarket(null);
      setFrozenAngles([]);
      orbitStartTimeRef.current = Date.now();
    }, 500);
  };

  // 点击功能行星
  const handleFeatureClick = (route: string) => {
    navigate(route);
  };

  /* ============ 计算镜头 transform ============ */
  let cameraTransform = 'translate(0, 0) scale(1)';

  if ((viewMode === 'zooming' || viewMode === 'features') && activeMarket) {
    const idx = markets.indexOf(activeMarket);
    const angle = frozenAngles[idx] ?? activeMarket.startAngle;
    const offset = getModuleOffset(activeMarket, angle);
    // 镜头推向模块：反向平移 + 放大
    cameraTransform = `translate(${-offset.x}px, ${-offset.y}px) scale(2.5)`;
  }

  /* ============ 渲染 ============ */
  return (
    <div className="min-h-screen bg-dark relative overflow-hidden">
      <DynamicStyles />
      <ParticleBackground />

      {!loading && !isAuthenticated && <LoginOverlay />}

      {/* 用户头像 */}
      {isAuthenticated && username && viewMode === 'home' && (
        <div className="absolute top-5 right-6 z-40 flex items-center gap-2.5 px-4 py-2 rounded-full bg-gradient-card border border-border opacity-0"
          style={{ animation: 'home-fadein 0.5s ease 1s both' }}>
          <div className="w-7 h-7 rounded-full bg-gradient-to-br from-primary to-accent-cyan flex items-center justify-center text-white text-xs font-bold">
            {username.charAt(0).toUpperCase()}
          </div>
          <span className="text-sm text-gray-300 font-medium hidden sm:inline">{username}</span>
          <span className="w-2 h-2 rounded-full bg-bear animate-pulse" title="在线" />
        </div>
      )}

      {/* Ticker */}
      {viewMode === 'home' && <TickerBar />}

      {/* 返回按钮 */}
      {viewMode === 'features' && (
        <button
          onClick={handleBack}
          className="absolute top-5 left-5 z-50 flex items-center gap-2 px-3 py-1.5 rounded-full bg-dark-card/80 border border-border text-gray-400 hover:text-white hover:border-gray-500 transition-all text-sm opacity-0"
          style={{ animation: 'home-fadein 0.5s ease 0.5s both' }}
        >
          <svg className="h-4 w-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
            <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M15 19l-7-7 7-7" />
          </svg>
          返回
        </button>
      )}

      {/* ======== 镜头容器（整个画面被 transform） ======== */}
      <div
        className="absolute inset-0 flex items-center justify-center"
        style={{
          transform: cameraTransform,
          transition: viewMode === 'home'
            ? 'transform 0.8s cubic-bezier(0.4, 0, 0.2, 1)'
            : 'transform 1.1s cubic-bezier(0.16, 1, 0.3, 1)',
          transformOrigin: 'center center',
          willChange: 'transform',
        }}
      >
        {/* ---- FinHub 中心标题 ---- */}
        <div
          className="absolute z-20 flex flex-col items-center pointer-events-none select-none"
          style={{
            opacity: viewMode === 'home' ? 1 : 0,
            transition: 'opacity 0.4s ease',
          }}
        >
          <h1 className="text-4xl md:text-5xl lg:text-6xl font-bold opacity-0"
            style={{ animation: 'home-fadeup 0.8s ease 0.2s both' }}>
            <span className="bg-gradient-to-r from-primary-light via-accent-cyan to-accent-purple bg-clip-text text-transparent">
              FinHub
            </span>
          </h1>
        </div>

        {/* ---- 轨道环 (装饰) ---- */}
        {markets.map((m, i) => (
          <div
            key={`orbit-ring-${i}`}
            className="absolute rounded-full border pointer-events-none"
            style={{
              width: m.orbitRadius * 2,
              height: m.orbitRadius * 2,
              borderColor: hoveredMarket === i ? `${m.accentColor}20` : '#1E293B30',
              borderStyle: 'dashed',
              borderWidth: 0.5,
              opacity: viewMode === 'home' ? 0.5 : 0,
              transition: 'opacity 0.5s ease, border-color 0.3s',
            }}
          />
        ))}

        {/* ---- 连线：每个模块 → 中心 ---- */}
        <svg className="absolute pointer-events-none" style={{
          width: 600, height: 600,
          left: 'calc(50% - 300px)', top: 'calc(50% - 300px)',
          opacity: viewMode === 'home' ? 1 : 0,
          transition: 'opacity 0.4s ease',
        }}>
          {viewMode === 'home' && markets.map((m, i) => {
            const elapsed = (Date.now() - orbitStartTimeRef.current) / 1000;
            // 近似：用 CSS 动画的角度不好拿到，所以连线做静态近似（startAngle 位置）
            const rad = (m.startAngle * Math.PI) / 180;
            const ex = 300 + Math.cos(rad) * m.orbitRadius;
            const ey = 300 + Math.sin(rad) * m.orbitRadius;
            const isH = hoveredMarket === i;
            return (
              <line key={i} x1={300} y1={300} x2={ex} y2={ey}
                stroke={isH ? `${m.accentColor}50` : '#8B5CF615'}
                strokeWidth={isH ? 1 : 0.5}
                strokeDasharray="4 6"
                style={{ animation: `line-breathe ${5 + i}s ease-in-out infinite`, transition: 'stroke 0.3s' }}
              />
            );
          })}
        </svg>

        {/* ---- 市场模块节点（公转动画） ---- */}
        {markets.map((m, i) => {
          const isFrozen = frozenAngles.length > 0;
          const isHovered = hoveredMarket === i;
          const nodeSize = m.available
            ? 'w-[56px] h-[56px] md:w-[72px] md:h-[72px]'
            : 'w-[48px] h-[48px] md:w-[64px] md:h-[64px]';

          // 公转动画 or 冻结位置
          const orbitStyle: React.CSSProperties = isFrozen
            ? (() => {
                const rad = (frozenAngles[i] * Math.PI) / 180;
                const x = Math.cos(rad) * m.orbitRadius;
                const y = Math.sin(rad) * m.orbitRadius;
                return { transform: `translate(${x}px, ${y}px)` };
              })()
            : {
                animation: `orbit-${i} ${m.orbitSpeed}s linear infinite`,
              };

          return (
            <div
              key={m.id}
              className="absolute z-10"
              style={{
                ...orbitStyle,
                opacity: viewMode === 'home' ? 1 : 0,
                transition: viewMode === 'home' ? 'opacity 0.4s ease' : 'opacity 0.8s ease 0.2s',
              }}
            >
              <div
                className={`${nodeSize} rounded-full flex flex-col items-center justify-center
                  border relative
                  ${m.available ? 'cursor-pointer' : 'cursor-default opacity-50'}`}
                style={{
                  background: `radial-gradient(circle at 50% 38%, ${m.accentColor}12, #111827 75%)`,
                  borderColor: isHovered && m.available ? `${m.accentColor}60` : '#1E293B40',
                  boxShadow: isHovered && m.available
                    ? `0 0 30px ${m.accentColor}35, 0 0 60px ${m.accentColor}12`
                    : `0 0 12px ${m.accentColor}06`,
                  transform: isHovered && m.available ? 'scale(1.12)' : 'scale(1)',
                  transition: 'transform 0.3s, border-color 0.3s, box-shadow 0.3s',
                }}
                onClick={() => handleMarketClick(m, i)}
                onMouseEnter={() => setHoveredMarket(i)}
                onMouseLeave={() => setHoveredMarket(null)}
              >
                {m.available && (
                  <div className="absolute inset-0 rounded-full pointer-events-none"
                    style={{
                      border: `1px solid ${m.accentColor}15`,
                      animation: `breath-ring ${4 + i * 0.5}s ease-in-out infinite`,
                    }}
                  />
                )}
                <span className="text-xl md:text-2xl mb-0.5">{m.icon}</span>
                <span className="text-[9px] md:text-[11px] font-bold"
                  style={{ color: m.available ? m.accentColor : '#6B7280' }}>
                  {m.name}
                </span>
                {m.available && (
                  <span className="absolute top-1.5 right-1.5 md:top-2 md:right-2 w-1.5 h-1.5 md:w-2 md:h-2 rounded-full animate-pulse"
                    style={{ backgroundColor: '#10B981' }} />
                )}
              </div>

              {/* hover tooltip */}
              {isHovered && viewMode === 'home' && (
                <div className="absolute left-1/2 -translate-x-1/2 flex flex-col items-center pointer-events-none whitespace-nowrap"
                  style={{ top: '100%', marginTop: 6 }}>
                  <span className="text-[10px] text-gray-400">{m.subtitle}</span>
                  {m.available && <span className="text-[10px] mt-1 font-medium" style={{ color: m.accentColor }}>点击进入 →</span>}
                  {!m.available && <span className="text-[10px] mt-1 text-gray-600">敬请期待</span>}
                </div>
              )}
            </div>
          );
        })}
      </div>

      {/* ======== 功能行星层（独立于镜头，固定在屏幕中央） ======== */}
      {viewMode === 'features' && activeMarket && featureMap[activeMarket.id] && (
        <div className="fixed inset-0 z-30 flex items-center justify-center pointer-events-none">
          {/* 中心太阳 */}
          <div
            className="absolute z-20 flex flex-col items-center justify-center w-[80px] h-[80px] md:w-[100px] md:h-[100px] rounded-full pointer-events-auto cursor-pointer"
            onClick={handleBack}
            style={{
              background: `radial-gradient(circle at 50% 38%, ${activeMarket.accentColor}25, #111827 75%)`,
              border: `1px solid ${activeMarket.accentColor}40`,
              '--glow-color': `${activeMarket.accentColor}30`,
              '--glow-color2': `${activeMarket.accentColor}15`,
              animation: 'sun-glow 4s ease-in-out infinite',
            } as React.CSSProperties}
          >
            <span className="text-2xl md:text-3xl mb-0.5">{activeMarket.icon}</span>
            <span className="text-[10px] md:text-xs font-bold" style={{ color: activeMarket.accentColor }}>
              {activeMarket.name}
            </span>
          </div>

          {/* 轨道环 */}
          <div className="absolute rounded-full border border-dashed pointer-events-none"
            style={{
              width: 270, height: 270, borderColor: '#1E293B',
              opacity: featuresPhase >= 1 ? 0.4 : 0,
              transition: 'opacity 0.6s ease 0.3s',
            }} />
          <div className="absolute rounded-full border border-dashed pointer-events-none"
            style={{
              width: 500, height: 500, borderColor: '#1E293B',
              opacity: featuresPhase >= 1 ? 0.3 : 0,
              transition: 'opacity 0.6s ease 0.5s',
            }} />

          {/* 连线 */}
          {featureMap[activeMarket.id].map((planet, i) => {
            const pos = featurePositions[i];
            const dist = Math.sqrt(pos.x ** 2 + pos.y ** 2);
            const angle = Math.atan2(pos.y, pos.x) * (180 / Math.PI);
            const isH = hoveredFeature === i;
            return (
              <div key={`fl-${planet.id}`} className="absolute pointer-events-none"
                style={{
                  width: dist, height: 0,
                  borderTop: `${isH ? 1.5 : 0.5}px ${isH ? 'solid' : 'dashed'} ${isH ? `${planet.color}70` : '#8B5CF618'}`,
                  transformOrigin: '0 0',
                  transform: `rotate(${angle}deg)`,
                  opacity: featuresPhase >= 1 ? 1 : 0,
                  transition: `opacity 0.5s ease ${0.2 + i * 0.04}s, border 0.3s`,
                  animation: featuresPhase >= 2 ? `line-breathe ${4 + i * 0.3}s ease-in-out infinite` : 'none',
                }} />
            );
          })}

          {/* 功能行星 */}
          {featureMap[activeMarket.id].map((planet, i) => {
            const pos = featurePositions[i];
            const isH = hoveredFeature === i;
            const delay = 0.1 + i * 0.05;
            const isInner = pos.ring === 1;
            const sz = isInner
              ? 'w-[64px] h-[64px] md:w-[80px] md:h-[80px]'
              : 'w-[54px] h-[54px] md:w-[68px] md:h-[68px]';

            return (
              <div key={planet.id} className="absolute z-20 pointer-events-auto"
                style={{
                  transform: featuresPhase >= 1
                    ? `translate(${pos.x}px, ${pos.y}px)`
                    : 'translate(0, 0) scale(0)',
                  opacity: featuresPhase >= 1 ? 1 : 0,
                  transition: `transform 0.65s cubic-bezier(0.34, 1.56, 0.64, 1) ${delay}s, opacity 0.3s ease ${delay}s`,
                }}>
                <div style={{
                  animation: featuresPhase >= 2 ? `feat-float-${i % 4} ${5 + i * 0.4}s ease-in-out infinite` : 'none',
                }}>
                  <div
                    className={`${sz} rounded-full flex flex-col items-center justify-center cursor-pointer relative`}
                    onClick={() => handleFeatureClick(planet.route)}
                    onMouseEnter={() => setHoveredFeature(i)}
                    onMouseLeave={() => setHoveredFeature(null)}
                    style={{
                      background: `radial-gradient(circle at 50% 38%, ${planet.color}15, #111827 75%)`,
                      border: `1px solid ${isH ? `${planet.color}60` : '#1E293B50'}`,
                      boxShadow: isH
                        ? `0 0 25px ${planet.color}40, 0 0 50px ${planet.color}15`
                        : `0 0 8px ${planet.color}08`,
                      transform: isH ? 'scale(1.15)' : 'scale(1)',
                      transition: 'transform 0.25s, border-color 0.25s, box-shadow 0.25s',
                    }}>
                    <span className={`${isInner ? 'text-xl md:text-2xl' : 'text-lg md:text-xl'} mb-0.5`}>{planet.icon}</span>
                    <span className="text-[8px] md:text-[10px] font-semibold" style={{ color: planet.color }}>
                      {planet.name}
                    </span>
                  </div>

                  {isH && (
                    <div className="absolute left-1/2 -translate-x-1/2 pointer-events-none whitespace-nowrap"
                      style={{ top: '100%', marginTop: 4 }}>
                      <span className="text-[10px] font-medium" style={{ color: planet.color }}>点击进入 →</span>
                    </div>
                  )}
                </div>
              </div>
            );
          })}
        </div>
      )}

      {/* footer */}
      {viewMode === 'home' && (
        <footer className="absolute bottom-4 left-0 right-0 z-10 text-center text-gray-600 text-xs md:text-sm opacity-0"
          style={{ animation: 'home-fadeup 0.6s ease 1s both' }}>
          <p>v2.1.0 · by Jason</p>
        </footer>
      )}
    </div>
  );
};

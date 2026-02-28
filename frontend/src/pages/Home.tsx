import React, { useState, useEffect, useRef, useCallback } from 'react';
import { useNavigate } from 'react-router-dom';
import { useAuthStore } from '../stores/authStore';
import { LoginOverlay } from '../components/LoginOverlay';
import { ParticleBackground } from '../components/ParticleBackground';
import api from '../services/api';

interface MarketModule {
  id: string;
  name: string;
  subtitle: string;
  tags: string[];
  icon: string;
  accentColor: string;
  available: boolean;
  route: string;
}

const markets: MarketModule[] = [
  {
    id: 'a-stock',
    name: 'A 股',
    subtitle: '中国大陆证券市场',
    tags: ['沪深主板', '创业板', '科创板'],
    icon: '🇨🇳',
    accentColor: '#EF4444',
    available: true,
    route: '/app/realtime',
  },
  {
    id: 'futures',
    name: '期 货',
    subtitle: '商品与金融衍生品',
    tags: ['商品期货', '金融期货', '股指期货'],
    icon: '📈',
    accentColor: '#F59E0B',
    available: false,
    route: '',
  },
  {
    id: 'us-stock',
    name: '美 股',
    subtitle: '美国证券市场',
    tags: ['NYSE', '纳斯达克', '中概股'],
    icon: '🇺🇸',
    accentColor: '#3B82F6',
    available: false,
    route: '',
  },
  {
    id: 'hk-stock',
    name: '港 股',
    subtitle: '香港证券市场',
    tags: ['港交所主板', '恒生科技', 'GEM'],
    icon: '🇭🇰',
    accentColor: '#8B5CF6',
    available: false,
    route: '',
  },
  {
    id: 'trading',
    name: '交 易',
    subtitle: '半自动交易系统',
    tags: ['信号扫描', '风控管理', '自动下单'],
    icon: '💰',
    accentColor: '#F59E0B',
    available: true,
    route: '/trading',
  },
];

// ======== #4 Spotlight 光效卡片 ========
const MarketCard: React.FC<{
  market: MarketModule;
  onClick: (el: HTMLDivElement) => void;
  index: number;
}> = ({ market, onClick, index }) => {
  const cardRef = useRef<HTMLDivElement>(null);
  const [mousePos, setMousePos] = useState({ x: 0, y: 0 });
  const [isHovering, setIsHovering] = useState(false);

  const handleMouseMove = useCallback((e: React.MouseEvent) => {
    const rect = cardRef.current?.getBoundingClientRect();
    if (!rect) return;
    setMousePos({ x: e.clientX - rect.left, y: e.clientY - rect.top });
  }, []);

  const spotlightStyle: React.CSSProperties = isHovering && market.available
    ? {
        background: `radial-gradient(300px circle at ${mousePos.x}px ${mousePos.y}px, ${market.accentColor}15, transparent 60%)`,
      }
    : {};

  const borderGlowStyle: React.CSSProperties = isHovering && market.available
    ? {
        boxShadow: `0 0 24px ${market.accentColor}40`,
        borderColor: `${market.accentColor}80`,
      }
    : {};

  return (
    <div
      ref={cardRef}
      onClick={() => cardRef.current && onClick(cardRef.current)}
      onMouseMove={handleMouseMove}
      onMouseEnter={() => setIsHovering(true)}
      onMouseLeave={() => setIsHovering(false)}
      style={{
        ...borderGlowStyle,
        animation: `card-enter 0.6s ease both`,
        animationDelay: `${0.3 + index * 0.12}s`,
      }}
      className={`
        relative group overflow-hidden bg-gradient-card border border-border rounded-2xl p-5 md:p-6 h-full
        transition-all duration-300 ease-out
        ${market.available ? 'cursor-pointer' : 'cursor-default opacity-60 hover:opacity-75'}
      `}
    >
      {/* Spotlight 跟随光效 */}
      <div
        className="absolute inset-0 pointer-events-none transition-opacity duration-300"
        style={{ ...spotlightStyle, opacity: isHovering ? 1 : 0 }}
      />

      {/* 图标 */}
      <div
        className="relative w-14 h-14 md:w-16 md:h-16 rounded-xl flex items-center justify-center mb-4 md:mb-5 transition-transform duration-300 group-hover:scale-110"
        style={{ backgroundColor: `${market.accentColor}15` }}
      >
        <span className="text-3xl md:text-4xl">{market.icon}</span>
      </div>

      {/* 名称 */}
      <h2
        className="relative text-xl md:text-2xl font-bold mb-1"
        style={{ color: market.accentColor }}
      >
        {market.name}
      </h2>

      {/* 副标题 */}
      <p className="relative text-gray-500 text-xs md:text-sm mb-3 md:mb-4">
        {market.subtitle}
      </p>

      {/* 标签 */}
      <div className="relative flex flex-wrap gap-1.5 mb-4 md:mb-5">
        {market.tags.map((tag) => (
          <span
            key={tag}
            className="text-[10px] md:text-xs px-2 py-0.5 rounded-full bg-dark-lighter text-gray-400 border border-border"
          >
            {tag}
          </span>
        ))}
      </div>

      {/* 状态 */}
      {market.available ? (
        <div className="relative flex items-center gap-2 text-sm">
          <span className="w-2 h-2 rounded-full bg-bear animate-pulse" />
          <span className="text-bear font-medium">已上线</span>
          <span
            className="ml-auto opacity-0 group-hover:opacity-100 transition-opacity duration-300 text-sm font-medium"
            style={{ color: market.accentColor }}
          >
            进入 →
          </span>
        </div>
      ) : (
        <div className="relative flex items-center gap-2 text-sm">
          <span className="w-2 h-2 rounded-full bg-gray-600" />
          <span className="text-gray-500">敬请期待</span>
        </div>
      )}
    </div>
  );
};

// ======== #5 行情滚动条 ========
interface TickerItem {
  name: string;
  price: number | null;
  change_pct: number | null;
}

const TickerBar: React.FC = () => {
  const [items, setItems] = useState<TickerItem[]>([]);
  const [isTrading, setIsTrading] = useState(true);

  useEffect(() => {
    const fetchTicker = async () => {
      try {
        const res: any = await api.get('/realtime/ticker');
        if (res.success && res.data?.length) {
          setItems(res.data);
          setIsTrading(res.is_trading);
        }
      } catch {
        // 静默失败
      }
    };
    fetchTicker();
  }, []);

  if (!items.length) return null;

  const renderItem = (t: TickerItem, i: number) => {
    const up = (t.change_pct ?? 0) >= 0;
    const pctStr = t.change_pct != null
      ? `${up ? '+' : ''}${t.change_pct.toFixed(2)}%`
      : '--';
    const priceStr = t.price != null ? t.price.toLocaleString() : '--';
    return (
      <span key={i} className="inline-flex items-center gap-2 text-xs md:text-sm flex-shrink-0">
        <span className="text-gray-400 font-medium">{t.name}</span>
        <span className="text-gray-300">{priceStr}</span>
        <span className={up ? 'text-bull' : 'text-bear'}>{pctStr}</span>
        {!isTrading && <span title="休市中">🙅</span>}
      </span>
    );
  };

  return (
    <div className="w-full max-w-5xl overflow-hidden mb-10 md:mb-14 opacity-0"
      style={{ animation: 'fade-up 0.6s ease 0.8s both' }}
    >
      <div className="animate-ticker flex whitespace-nowrap" style={{ width: 'max-content' }}>
        {/* 第一组 */}
        <div className="flex gap-8 flex-shrink-0 pr-8">
          {items.map((t, i) => renderItem(t, i))}
        </div>
        {/* 第二组（无缝衔接） */}
        <div className="flex gap-8 flex-shrink-0 pr-8">
          {items.map((t, i) => renderItem(t, i + items.length))}
        </div>
      </div>
    </div>
  );
};

// ======== 页面转场动画 ========
const TransitionOverlay: React.FC<{
  rect: DOMRect;
  market: MarketModule;
}> = ({ rect, market }) => {
  const [phase, setPhase] = useState(0);

  useEffect(() => {
    requestAnimationFrame(() => requestAnimationFrame(() => setPhase(1)));
    const t = setTimeout(() => setPhase(2), 580);
    return () => clearTimeout(t);
  }, []);

  const vw = window.innerWidth;
  const vh = window.innerHeight;

  // 飞行：纯 transform（GPU 合成）
  const dx = vw / 2 - rect.left - rect.width / 2;
  const dy = vh / 2 - rect.top - rect.height / 2;

  const transforms = [
    'translate3d(0, 0, 0) rotateY(0deg) scale(1)',
    `translate3d(${dx}px, ${dy}px, 0) rotateY(360deg) scale(1.1)`,
    `translate3d(${dx}px, ${dy}px, 0) rotateY(360deg) scale(1.05)`,
  ];

  // 展开：clip-path inset（GPU 合成，丝滑无变形）
  const ct = (vh - rect.height) / 2;
  const cs = (vw - rect.width) / 2;
  const expandClip = phase < 2
    ? `inset(${ct}px ${cs}px round 16px)`
    : 'inset(0px round 0px)';

  // B) 光晕效果
  const glowShadow = phase === 1
    ? `0 0 40px ${market.accentColor}90, 0 0 80px ${market.accentColor}50, 0 0 120px ${market.accentColor}25`
    : 'none';

  return (
    <div className="fixed inset-0 z-[100]" style={{ perspective: 1200 }}>
      {/* 暗色背景淡入 */}
      <div
        className="absolute inset-0"
        style={{
          backgroundColor: '#0A0E27',
          opacity: phase > 0 ? 1 : 0,
          transition: 'opacity 0.5s ease',
        }}
      />
      {/* 展开层：clip-path 从卡片大小平滑展开到全屏 */}
      <div
        className="fixed inset-0"
        style={{
          backgroundColor: '#0A0E27',
          clipPath: expandClip,
          opacity: phase >= 1 ? 1 : 0,
          transition: phase < 1 ? 'none'
            : phase === 1 ? 'opacity 0.2s ease'
            : 'clip-path 0.6s cubic-bezier(0.22, 0.61, 0.36, 1)',
          willChange: 'clip-path',
        }}
      />
      {/* 飞行卡片 */}
      <div
        className="fixed overflow-hidden"
        style={{
          left: rect.left, top: rect.top,
          width: rect.width, height: rect.height,
          backgroundColor: '#0A0E27',
          borderRadius: 16,
          boxShadow: glowShadow,
          transform: transforms[phase],
          opacity: phase === 2 ? 0 : 1,
          // C) 弹性缓动飞行
          transition: phase === 0 ? 'none'
            : phase === 1
            ? 'transform 0.55s cubic-bezier(0.34, 1.56, 0.64, 1), box-shadow 0.55s ease, opacity 0.2s ease'
            : 'transform 0.3s ease, opacity 0.35s ease, box-shadow 0.3s ease',
          transformOrigin: 'center center',
          transformStyle: 'preserve-3d',
          willChange: 'transform',
        }}
      >
        {/* 卡片渐变背景 */}
        <div
          className="absolute inset-0"
          style={{ background: 'linear-gradient(135deg, #151B2E 0%, #1A1F37 100%)' }}
        />
        {/* 卡片边框 */}
        <div
          className="absolute inset-0 border border-border"
          style={{ borderRadius: 'inherit' }}
        />
        {/* 卡片内容 */}
        <div className="relative p-5 md:p-6">
          <div
            className="w-14 h-14 md:w-16 md:h-16 rounded-xl flex items-center justify-center mb-4"
            style={{ backgroundColor: `${market.accentColor}15` }}
          >
            <span className="text-3xl md:text-4xl">{market.icon}</span>
          </div>
          <h2 className="text-xl md:text-2xl font-bold mb-1" style={{ color: market.accentColor }}>
            {market.name}
          </h2>
          <p className="text-gray-500 text-xs md:text-sm">{market.subtitle}</p>
        </div>
      </div>
    </div>
  );
};

// ======== 主页 ========
export const Home: React.FC = () => {
  const navigate = useNavigate();
  const { isAuthenticated, username, loading, checkAuth } = useAuthStore();

  useEffect(() => {
    checkAuth();
  }, []);

  const [transition, setTransition] = useState<{ rect: DOMRect; market: MarketModule } | null>(null);

  const handleClick = (market: MarketModule, el: HTMLDivElement) => {
    if (!market.available || !isAuthenticated || transition) return;
    const rect = el.getBoundingClientRect();
    setTransition({ rect, market });
    setTimeout(() => navigate(market.route), 1050);
  };

  return (
    <div className="min-h-screen bg-dark flex flex-col relative overflow-hidden">
      {/* A) 转场时背景模糊下沉 */}
      <div
        className="flex-1 flex flex-col relative"
        style={{
          transform: transition ? 'scale(0.95)' : 'scale(1)',
          filter: transition ? 'blur(3px)' : 'none',
          opacity: transition ? 0 : 1,
          transition: 'transform 0.7s ease, filter 0.7s ease, opacity 0.7s ease',
          transformOrigin: 'center center',
          willChange: 'transform, filter, opacity',
        }}
      >
      {/* 粒子背景 */}
      <ParticleBackground />

      {/* 登录浮窗 */}
      {!loading && !isAuthenticated && <LoginOverlay />}

      {/* 右上角用户标识 */}
      {isAuthenticated && username && (
        <div className="absolute top-5 right-6 z-10 flex items-center gap-2.5 px-4 py-2 rounded-full bg-gradient-card border border-border">
          <div className="w-7 h-7 rounded-full bg-gradient-to-br from-primary to-accent-cyan flex items-center justify-center text-white text-xs font-bold">
            {username.charAt(0).toUpperCase()}
          </div>
          <span className="text-sm text-gray-300 font-medium hidden sm:inline">{username}</span>
          <span className="w-2 h-2 rounded-full bg-bear animate-pulse" title="在线" />
        </div>
      )}

      {/* 主内容区域 */}
      <div className="relative z-[1] flex-1 flex flex-col items-center justify-center px-4 sm:px-6 lg:px-8">
        {/* #2 标题入场动画 */}
        <div className="text-center mb-8 md:mb-10">
          <h1
            className="text-4xl md:text-5xl lg:text-6xl font-bold opacity-0"
            style={{ animation: 'fade-up 0.8s ease 0.1s both' }}
          >
            <span className="bg-gradient-to-r from-primary-light via-accent-cyan to-accent-purple bg-clip-text text-transparent">
              FinHub
            </span>
          </h1>
        </div>

        {/* #5 行情滚动条 */}
        <TickerBar />

        {/* #3 市场卡片 - 交错入场 */}
        <div className="grid grid-cols-2 lg:grid-cols-5 gap-4 md:gap-5 w-full max-w-6xl">
          {markets.map((market, index) => {
            const isClicked = transition?.market.id === market.id;
            // D) 未点击的卡片四散飞出
            const scatterDirs = [
              { x: -1, y: -0.8, r: -30 },
              { x: 1, y: -0.8, r: 25 },
              { x: -1, y: 0.8, r: 20 },
              { x: 1, y: 0.8, r: -25 },
              { x: 0, y: 1, r: 15 },
            ];
            let wrapStyle: React.CSSProperties = {};
            if (transition) {
              if (isClicked) {
                wrapStyle = { opacity: 0, transition: 'opacity 0.05s' };
              } else {
                const d = scatterDirs[index];
                wrapStyle = {
                  transform: `translate(${d.x * window.innerWidth * 0.4}px, ${d.y * window.innerHeight * 0.4}px) rotate(${d.r}deg) scale(0.3)`,
                  opacity: 0,
                  transition: 'all 0.6s cubic-bezier(0.4, 0, 1, 1)',
                };
              }
            }
            return (
              <div key={market.id} className="h-full" style={wrapStyle}>
                <MarketCard
                  market={market}
                  onClick={(el) => handleClick(market, el)}
                  index={index}
                />
              </div>
            );
          })}
        </div>
      </div>

      {/* 底部信息 */}
      <footer
        className="relative z-[1] text-center py-6 text-gray-600 text-xs md:text-sm opacity-0"
        style={{ animation: 'fade-up 0.6s ease 1s both' }}
      >
        <div className="flex items-center justify-center gap-3 md:gap-4 mb-2">
          <span>📊 实时行情</span>
          <span className="text-border">·</span>
          <span>🎯 信号检测</span>
          <span className="text-border">·</span>
          <span>🔬 策略回测</span>
          <span className="text-border">·</span>
          <span>🤖 AI 顾问</span>
        </div>
        <p>v2.0.0 · by Jason</p>
      </footer>
      </div>

      {/* 页面转场动画 */}
      {transition && <TransitionOverlay rect={transition.rect} market={transition.market} />}
    </div>
  );
};

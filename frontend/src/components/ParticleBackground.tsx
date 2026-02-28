import React, { useRef, useEffect, useCallback } from 'react';

type Shape = 'square' | 'cross' | 'dash' | 'digit' | 'symbol';

interface Particle {
  x: number;
  y: number;
  baseVx: number;    // 发散方向速度
  baseVy: number;
  vx: number;        // 鼠标吸引额外速度
  vy: number;
  size: number;
  baseSize: number;
  opacity: number;
  baseOpacity: number;
  color: string;
  glowColor: string;
  shape: Shape;
  char: string;
  rotation: number;
  rotationSpeed: number;
}

const COLORS = [
  { fill: '#3B82F6', glow: 'rgba(59,130,246,' },
  { fill: '#06B6D4', glow: 'rgba(6,182,212,' },
  { fill: '#8B5CF6', glow: 'rgba(139,92,246,' },
  { fill: '#60A5FA', glow: 'rgba(96,165,250,' },
  { fill: '#2DD4BF', glow: 'rgba(45,212,191,' },
  { fill: '#A78BFA', glow: 'rgba(167,139,250,' },
];

const DIGITS = ['0', '1'];
const SYMBOLS = ['$', '%', '+', '>', '<', '#', '/', '::'];

const PARTICLE_COUNT = 150;
const MOUSE_RADIUS = 220;
const MOUSE_ATTRACT = 0.06;
const TAIL_LENGTH = 50;

function pickShape(): { shape: Shape; char: string } {
  const r = Math.random();
  if (r < 0.25) return { shape: 'square', char: '' };
  if (r < 0.40) return { shape: 'cross', char: '' };
  if (r < 0.55) return { shape: 'dash', char: '' };
  if (r < 0.80) return { shape: 'digit', char: DIGITS[Math.floor(Math.random() * DIGITS.length)] };
  return { shape: 'symbol', char: SYMBOLS[Math.floor(Math.random() * SYMBOLS.length)] };
}

export const ParticleBackground: React.FC = () => {
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const particlesRef = useRef<Particle[]>([]);
  const mouseRef = useRef({ x: -9999, y: -9999 });
  const rafRef = useRef<number>(0);
  const sizeRef = useRef({ w: 0, h: 0 });

  // 从底部中心发射一个粒子
  const createParticle = useCallback((w: number, h: number, scatter?: boolean): Particle => {
    const color = COLORS[Math.floor(Math.random() * COLORS.length)];
    const { shape, char } = pickShape();

    // 发射角度：从底部中心向上扇形发散（-120° ~ -60°，即左上到右上的 120° 扇面）
    const angle = -Math.PI / 2 + (Math.random() - 0.5) * Math.PI * 1.4;
    const speed = 0.4 + Math.random() * 0.8;

    const baseSize = shape === 'digit' || shape === 'symbol'
      ? 18 + Math.random() * 10
      : 6 + Math.random() * 6;

    // scatter=true 时初始化分散在画面各处（首次加载），否则从底部中心出发
    const originX = w / 2 + (Math.random() - 0.5) * 60;
    const originY = h + 10;

    let startX: number, startY: number;
    if (scatter) {
      // 沿发射方向随机散布到画面中，模拟已经发射了一段时间
      const t = Math.random() * Math.max(w, h) * 1.2;
      startX = originX + Math.cos(angle) * t;
      startY = originY + Math.sin(angle) * t;
    } else {
      startX = originX;
      startY = originY;
    }

    return {
      x: startX,
      y: startY,
      baseVx: Math.cos(angle) * speed,
      baseVy: Math.sin(angle) * speed,
      vx: 0,
      vy: 0,
      size: baseSize,
      baseSize,
      opacity: 0.15,
      baseOpacity: 0.15,
      color: color.fill,
      glowColor: color.glow,
      shape,
      char,
      rotation: Math.random() * Math.PI * 2,
      rotationSpeed: (Math.random() - 0.5) * 0.015,
    };
  }, []);

  const initParticles = useCallback((w: number, h: number) => {
    const particles: Particle[] = [];
    for (let i = 0; i < PARTICLE_COUNT; i++) {
      particles.push(createParticle(w, h, true));
    }
    particlesRef.current = particles;
  }, [createParticle]);

  useEffect(() => {
    const canvas = canvasRef.current;
    if (!canvas) return;
    const ctx = canvas.getContext('2d');
    if (!ctx) return;

    const resize = () => {
      const dpr = window.devicePixelRatio || 1;
      const rect = canvas.parentElement!.getBoundingClientRect();
      sizeRef.current = { w: rect.width, h: rect.height };
      canvas.width = rect.width * dpr;
      canvas.height = rect.height * dpr;
      canvas.style.width = `${rect.width}px`;
      canvas.style.height = `${rect.height}px`;
      ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
      initParticles(rect.width, rect.height);
    };

    resize();
    window.addEventListener('resize', resize);

    const onMouseMove = (e: MouseEvent) => {
      const rect = canvas.getBoundingClientRect();
      mouseRef.current = { x: e.clientX - rect.left, y: e.clientY - rect.top };
    };
    const onMouseLeave = () => {
      mouseRef.current = { x: -9999, y: -9999 };
    };

    window.addEventListener('mousemove', onMouseMove);
    document.addEventListener('mouseleave', onMouseLeave);

    const animate = () => {
      const { w, h } = sizeRef.current;
      ctx.clearRect(0, 0, w, h);

      const particles = particlesRef.current;
      const mouse = mouseRef.current;
      const centerX = w / 2;
      const centerY = h;
      // 最大距离（对角线）
      const maxDist = Math.sqrt(centerX * centerX + centerY * centerY);

      for (let i = 0; i < particles.length; i++) {
        const p = particles[i];

        // 渐变：根据离底部中心的距离，越远越大越亮
        const distFromOrigin = Math.sqrt((p.x - centerX) ** 2 + (p.y - centerY) ** 2);
        const distRatio = Math.min(1, distFromOrigin / maxDist);
        const factor = 0.3 + distRatio * 0.7;  // 0.3 ~ 1.0
        p.size = p.baseSize * factor;
        p.baseOpacity = (0.15 + 0.7 * distRatio);

        // 鼠标吸引
        const dx = mouse.x - p.x;
        const dy = mouse.y - p.y;
        const dist = Math.sqrt(dx * dx + dy * dy);

        if (dist < MOUSE_RADIUS && dist > 5) {
          const force = ((MOUSE_RADIUS - dist) / MOUSE_RADIUS) * MOUSE_ATTRACT;
          p.vx += (dx / dist) * force;
          p.vy += (dy / dist) * force;
          p.opacity = Math.min(1, p.baseOpacity + (1 - dist / MOUSE_RADIUS) * 0.5);
        } else {
          p.opacity += (p.baseOpacity - p.opacity) * 0.05;
        }

        // 吸引速度衰减
        p.vx *= 0.96;
        p.vy *= 0.96;

        // 移动
        p.x += p.baseVx + p.vx;
        p.y += p.baseVy + p.vy;
        p.rotation += p.rotationSpeed;

        // 越界 → 从底部中心重新发射
        if (p.x < -40 || p.x > w + 40 || p.y < -40 || p.y > h + 40) {
          particles[i] = createParticle(w, h, false);
          continue;
        }

        // 拖尾
        const totalVx = p.baseVx + p.vx;
        const totalVy = p.baseVy + p.vy;
        const speed = Math.sqrt(totalVx * totalVx + totalVy * totalVy);
        if (speed > 0.2) {
          const tailLen = TAIL_LENGTH * Math.min(speed / 1.5, 1) * factor;
          const tailX = p.x - (totalVx / speed) * tailLen;
          const tailY = p.y - (totalVy / speed) * tailLen;
          const grad = ctx.createLinearGradient(p.x, p.y, tailX, tailY);
          grad.addColorStop(0, `${p.glowColor}${p.opacity * 0.7})`);
          grad.addColorStop(1, `${p.glowColor}0)`);
          ctx.strokeStyle = grad;
          ctx.lineWidth = Math.max(1.5, p.size * 0.5);
          ctx.beginPath();
          ctx.moveTo(p.x, p.y);
          ctx.lineTo(tailX, tailY);
          ctx.stroke();
        }

        // 绘制粒子
        ctx.save();
        ctx.translate(p.x, p.y);
        ctx.rotate(p.rotation);

        const glowOpacity = p.opacity * 0.4;
        const coreOpacity = p.opacity;

        if (p.shape === 'square') {
          ctx.fillStyle = `${p.glowColor}${glowOpacity})`;
          ctx.fillRect(-p.size, -p.size, p.size * 2, p.size * 2);
          ctx.fillStyle = `${p.glowColor}${coreOpacity})`;
          ctx.fillRect(-p.size / 2, -p.size / 2, p.size, p.size);
        } else if (p.shape === 'cross') {
          const half = p.size / 2;
          ctx.strokeStyle = `${p.glowColor}${glowOpacity})`;
          ctx.lineWidth = 4;
          ctx.beginPath();
          ctx.moveTo(-half, 0); ctx.lineTo(half, 0);
          ctx.moveTo(0, -half); ctx.lineTo(0, half);
          ctx.stroke();
          ctx.strokeStyle = `${p.glowColor}${coreOpacity})`;
          ctx.lineWidth = 1.5;
          ctx.beginPath();
          ctx.moveTo(-half, 0); ctx.lineTo(half, 0);
          ctx.moveTo(0, -half); ctx.lineTo(0, half);
          ctx.stroke();
        } else if (p.shape === 'dash') {
          ctx.strokeStyle = `${p.glowColor}${glowOpacity})`;
          ctx.lineWidth = 4;
          ctx.beginPath();
          ctx.moveTo(-p.size, 0); ctx.lineTo(p.size, 0);
          ctx.stroke();
          ctx.strokeStyle = `${p.glowColor}${coreOpacity})`;
          ctx.lineWidth = 1.5;
          ctx.beginPath();
          ctx.moveTo(-p.size, 0); ctx.lineTo(p.size, 0);
          ctx.stroke();
        } else {
          ctx.textAlign = 'center';
          ctx.textBaseline = 'middle';
          ctx.font = `${p.size + 4}px monospace`;
          ctx.fillStyle = `${p.glowColor}${glowOpacity})`;
          ctx.fillText(p.char, 0, 0);
          ctx.font = `${p.size}px monospace`;
          ctx.fillStyle = `${p.glowColor}${coreOpacity})`;
          ctx.fillText(p.char, 0, 0);
        }

        ctx.restore();
      }

      rafRef.current = requestAnimationFrame(animate);
    };

    rafRef.current = requestAnimationFrame(animate);

    return () => {
      cancelAnimationFrame(rafRef.current);
      window.removeEventListener('resize', resize);
      window.removeEventListener('mousemove', onMouseMove);
      document.removeEventListener('mouseleave', onMouseLeave);
    };
  }, [initParticles, createParticle]);

  return (
    <canvas
      ref={canvasRef}
      className="absolute inset-0 pointer-events-none"
      style={{ zIndex: 0 }}
    />
  );
};

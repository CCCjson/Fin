import React, { useEffect, useRef, useState } from 'react';
import { useReducedMotion, useSpring } from 'framer-motion';

/* ================================================================
   数字滚动组件 —— 大号数字平滑过渡（综合评分 / 账户盈亏 / Token 等）
   - value 变化时用弹簧动画从旧值滚到新值
   - reduced-motion 时直接显示终值
   - 支持小数位、前后缀、千分位
   ================================================================ */

interface AnimatedNumberProps {
  value: number | null | undefined;
  decimals?: number;
  prefix?: string;
  suffix?: string;
  /** 千分位分隔 */
  grouped?: boolean;
  /** 空值占位 */
  placeholder?: string;
  className?: string;
}

const fmt = (n: number, decimals: number, grouped: boolean) => {
  const opts: Intl.NumberFormatOptions = {
    minimumFractionDigits: decimals,
    maximumFractionDigits: decimals,
    useGrouping: grouped,
  };
  return n.toLocaleString(undefined, opts);
};

export const AnimatedNumber: React.FC<AnimatedNumberProps> = ({
  value,
  decimals = 0,
  prefix = '',
  suffix = '',
  grouped = false,
  placeholder = '—',
  className,
}) => {
  const reduce = useReducedMotion();
  const isNum = typeof value === 'number' && Number.isFinite(value);
  const target = isNum ? (value as number) : 0;

  const spring = useSpring(target, { stiffness: 90, damping: 20, mass: 0.6 });
  const [display, setDisplay] = useState(target);
  const prev = useRef(target);

  useEffect(() => {
    if (!isNum) return;
    if (reduce) {
      setDisplay(target);
      prev.current = target;
      spring.set(target);
      return;
    }
    spring.set(target);
  }, [target, isNum, reduce, spring]);

  useEffect(() => {
    const unsub = spring.on('change', (v) => setDisplay(v));
    return () => unsub();
  }, [spring]);

  if (!isNum) return <span className={className}>{placeholder}</span>;

  return (
    <span className={className}>
      {prefix}
      {fmt(reduce ? target : display, decimals, grouped)}
      {suffix}
    </span>
  );
};

export default AnimatedNumber;

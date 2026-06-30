import React from 'react';

/* ================================================================
   统一卡片原子 —— 收口各页内联重复的
   `bg-gradient-card border border-border shadow-card rounded-xl` 串。
   黑底霓虹绿赛博风：支持发光、玻璃、HUD 角标三类点缀。
   ================================================================ */

type CardVariant = 'flat' | 'elevated' | 'glass';

interface CardProps extends React.HTMLAttributes<HTMLElement> {
  /** flat=纯卡面 / elevated=渐变+阴影（默认）/ glass=玻璃拟态 */
  variant?: CardVariant;
  /** hover 时霓虹绿辉光 */
  glow?: boolean;
  /** 四角 HUD 霓虹括号 */
  hud?: boolean;
  /** 渲染标签，默认 div */
  as?: React.ElementType;
  className?: string;
  children?: React.ReactNode;
}

const VARIANT_CLS: Record<CardVariant, string> = {
  flat: 'bg-dark-card border border-border',
  elevated: 'bg-gradient-card border border-border shadow-card',
  glass: 'bg-dark-card/60 backdrop-blur-md border border-primary/20',
};

export const Card: React.FC<CardProps> = ({
  variant = 'elevated',
  glow = false,
  hud = false,
  as,
  className = '',
  children,
  ...rest
}) => {
  const Tag = (as ?? 'div') as React.ElementType;
  const cls = [
    'relative rounded-xl',
    VARIANT_CLS[variant],
    glow ? 'transition-all hover:shadow-glow-blue' : '',
    className,
  ]
    .filter(Boolean)
    .join(' ');

  return (
    <Tag className={cls} {...rest}>
      {hud && <HudCorners />}
      {children}
    </Tag>
  );
};

/** 四角霓虹括号，纯装饰 */
const HudCorners: React.FC = () => {
  const base = 'pointer-events-none absolute w-3 h-3 border-primary/60';
  return (
    <>
      <span className={`${base} top-0 left-0 border-t border-l rounded-tl-md`} />
      <span className={`${base} top-0 right-0 border-t border-r rounded-tr-md`} />
      <span className={`${base} bottom-0 left-0 border-b border-l rounded-bl-md`} />
      <span className={`${base} bottom-0 right-0 border-b border-r rounded-br-md`} />
    </>
  );
};

export default Card;

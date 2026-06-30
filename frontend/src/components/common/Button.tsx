import React from 'react';

/* ================================================================
   统一按钮原子 —— 收口散落的裸 <button> 类名串。
   黑底霓虹绿赛博风：primary 用霓虹绿底 + 黑字（对比度足、够酷）。
   ================================================================ */

type ButtonVariant = 'primary' | 'ghost' | 'danger' | 'subtle';
type ButtonSize = 'sm' | 'md' | 'lg';

interface ButtonProps extends React.ButtonHTMLAttributes<HTMLButtonElement> {
  variant?: ButtonVariant;
  size?: ButtonSize;
  /** loading 时禁用并显示转圈 */
  loading?: boolean;
  /** 前置图标（emoji 或节点） */
  icon?: React.ReactNode;
  className?: string;
}

const VARIANT_CLS: Record<ButtonVariant, string> = {
  primary:
    'bg-primary text-dark font-medium hover:bg-primary-light hover:shadow-glow-green disabled:hover:shadow-none',
  ghost:
    'border border-primary/40 text-primary-light hover:bg-primary/10 hover:border-primary/70',
  danger:
    'bg-bull text-white font-medium hover:bg-bull-light hover:shadow-glow-red',
  subtle:
    'bg-dark-light text-gray-300 hover:bg-dark-card hover:text-white border border-border',
};

const SIZE_CLS: Record<ButtonSize, string> = {
  sm: 'px-3 py-1.5 text-xs rounded-lg gap-1.5',
  md: 'px-4 py-2 text-sm rounded-lg gap-2',
  lg: 'px-5 py-2.5 text-base rounded-xl gap-2',
};

export const Button: React.FC<ButtonProps> = ({
  variant = 'primary',
  size = 'md',
  loading = false,
  icon,
  className = '',
  disabled,
  children,
  ...rest
}) => {
  const cls = [
    'inline-flex items-center justify-center font-medium transition-all',
    'disabled:opacity-50 disabled:cursor-not-allowed select-none',
    VARIANT_CLS[variant],
    SIZE_CLS[size],
    className,
  ]
    .filter(Boolean)
    .join(' ');

  return (
    <button className={cls} disabled={disabled || loading} {...rest}>
      {loading ? (
        <span className="inline-block w-4 h-4 border-2 border-current border-t-transparent rounded-full animate-spin" />
      ) : (
        icon
      )}
      {children}
    </button>
  );
};

export default Button;

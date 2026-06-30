import React from 'react';
import { motion, useReducedMotion } from 'framer-motion';
import { fadeUp, fadeOnly } from './motion';
import { Button } from './Button';

/* ================================================================
   统一空状态 —— 替代散落的「暂无XXX」灰字
   icon + 标题 + 提示 + 可选引导按钮，带柔和淡入。
   ================================================================ */

interface EmptyStateProps {
  icon?: string;
  title: string;
  hint?: string;
  action?: { label: string; onClick: () => void };
  className?: string;
  /** 紧凑模式（用于列表内嵌） */
  compact?: boolean;
}

export const EmptyState: React.FC<EmptyStateProps> = ({
  icon = '📭',
  title,
  hint,
  action,
  className = '',
  compact = false,
}) => {
  const reduce = useReducedMotion();
  return (
    <motion.div
      variants={reduce ? fadeOnly : fadeUp}
      initial="hidden"
      animate="show"
      className={`flex flex-col items-center justify-center text-center ${
        compact ? 'py-8' : 'py-16'
      } ${className}`}
    >
      <div className={`${compact ? 'text-3xl' : 'text-5xl'} mb-3 opacity-80`}>{icon}</div>
      <div className={`${compact ? 'text-sm' : 'text-base'} font-medium text-gray-300`}>{title}</div>
      {hint && <div className="text-xs text-gray-500 mt-1.5 max-w-xs leading-relaxed">{hint}</div>}
      {action && (
        <Button onClick={action.onClick} className="mt-4">
          {action.label}
        </Button>
      )}
    </motion.div>
  );
};

export default EmptyState;

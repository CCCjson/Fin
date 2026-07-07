import React, { useCallback, useState } from 'react';

/** 展示当前对话的后端 session_id（mb_xxx），点击复制。会话创建前显示灰态占位。 */
export const SessionIdBadge: React.FC<{ sessionId?: string; className?: string }> = ({
  sessionId,
  className = '',
}) => {
  const [copied, setCopied] = useState(false);

  const onCopy = useCallback(() => {
    if (!sessionId) return;
    navigator.clipboard?.writeText(sessionId).then(() => {
      setCopied(true);
      setTimeout(() => setCopied(false), 1500);
    }).catch(() => {});
  }, [sessionId]);

  if (!sessionId) {
    return (
      <span className={`font-mono text-[10px] text-gray-600 ${className}`}>
        🆔 会话尚未创建…
      </span>
    );
  }

  const short = sessionId.length > 10 ? `${sessionId.slice(0, 10)}…` : sessionId;

  return (
    <button
      type="button"
      onClick={onCopy}
      title={`点击复制完整会话ID：${sessionId}`}
      className={`font-mono text-[10px] text-gray-500 hover:text-gray-300 transition-colors ${className}`}
    >
      {copied ? '已复制 ✓' : `🆔 ${short}`}
    </button>
  );
};

export default SessionIdBadge;

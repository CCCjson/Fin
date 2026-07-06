import { useCallback, useRef } from 'react';

/**
 * 防中文拼音（或其他输入法）组字时按 Enter 选词被误判为「发送」。
 *
 * 只判断 `e.nativeEvent.isComposing` 在 Safari / 部分安卓输入法上不够：
 * 那些环境会在确认候选词的 Enter keydown 之前先触发 compositionend，
 * 届时 isComposing 已经是 false。额外记录 compositionend 的时间戳，
 * 短时间窗口内的 Enter 仍视为「刚组字完成」，不放行发送。
 */
export function useImeGuard(graceMs = 10) {
  const composingRef = useRef(false);
  const composedAtRef = useRef(0);

  const onCompositionStart = useCallback(() => {
    composingRef.current = true;
  }, []);

  const onCompositionEnd = useCallback(() => {
    composingRef.current = false;
    composedAtRef.current = Date.now();
  }, []);

  const shouldSend = useCallback(
    (e: React.KeyboardEvent) => {
      if (e.nativeEvent.isComposing) return false;
      if (composingRef.current) return false;
      if (Date.now() - composedAtRef.current < graceMs) return false;
      return true;
    },
    [graceMs],
  );

  return { onCompositionStart, onCompositionEnd, shouldSend };
}

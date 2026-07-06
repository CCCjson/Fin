import { useEffect } from 'react';
import type { RefObject } from 'react';

/**
 * 当 `active` 为 true 时，监听点击/Escape，命中 ref 之外则调用 onOutside。
 * 用 capture 阶段的 pointerdown，保证即使目标元素后续 stopPropagation 也能先判定。
 */
export function useClickOutside(
  ref: RefObject<HTMLElement | null>,
  onOutside: () => void,
  active: boolean,
) {
  useEffect(() => {
    if (!active) return;

    const handlePointerDown = (e: PointerEvent) => {
      if (ref.current && !ref.current.contains(e.target as Node)) {
        onOutside();
      }
    };
    const handleKeyDown = (e: KeyboardEvent) => {
      if (e.key === 'Escape') onOutside();
    };

    document.addEventListener('pointerdown', handlePointerDown, { capture: true });
    document.addEventListener('keydown', handleKeyDown);
    return () => {
      document.removeEventListener('pointerdown', handlePointerDown, { capture: true });
      document.removeEventListener('keydown', handleKeyDown);
    };
  }, [active, ref, onOutside]);
}

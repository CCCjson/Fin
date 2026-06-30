/* ================================================================
   统一动画 variants —— 全项目复用 framer-motion 的入场/微交互定义
   调性：适度炫酷但克制，服务于信息而非喧宾夺主。
   无障碍：组件里用 useReducedMotion() 时改用 *Reduced 版本（纯淡入）。
   ================================================================ */
import type { Variants, Transition } from 'framer-motion';

/** 柔和弹簧 —— 卡片/气泡入场主用 */
export const springSoft: Transition = {
  type: 'spring',
  stiffness: 320,
  damping: 30,
  mass: 0.8,
};

/** 更弹一点 —— 对勾/徽标 pop 用 */
export const springPop: Transition = {
  type: 'spring',
  stiffness: 520,
  damping: 22,
};

/** 淡入 + 轻微上移（消息气泡 / 卡片通用） */
export const fadeUp: Variants = {
  hidden: { opacity: 0, y: 12 },
  show: { opacity: 1, y: 0, transition: springSoft },
};

/** 从右淡入（用户气泡） */
export const fadeFromRight: Variants = {
  hidden: { opacity: 0, x: 16 },
  show: { opacity: 1, x: 0, transition: springSoft },
};

/** 从左淡入（助手气泡 / 工具卡片） */
export const fadeFromLeft: Variants = {
  hidden: { opacity: 0, x: -16 },
  show: { opacity: 1, x: 0, transition: springSoft },
};

/** pop 入场（药丸、徽标） */
export const popIn: Variants = {
  hidden: { opacity: 0, scale: 0.6 },
  show: { opacity: 1, scale: 1, transition: springPop },
  exit: { opacity: 0, scale: 0.6, transition: { duration: 0.12 } },
};

/** 容器：子项依次入场 */
export const staggerContainer: Variants = {
  hidden: {},
  show: { transition: { staggerChildren: 0.06, delayChildren: 0.04 } },
};

/** toast 进出 */
export const toastVariants: Variants = {
  hidden: { opacity: 0, x: 40, scale: 0.95 },
  show: { opacity: 1, x: 0, scale: 1, transition: springSoft },
  exit: { opacity: 0, x: 40, scale: 0.9, transition: { duration: 0.18 } },
};

/** 无障碍降级：纯淡入，无位移/缩放 */
export const fadeOnly: Variants = {
  hidden: { opacity: 0 },
  show: { opacity: 1, transition: { duration: 0.18 } },
  exit: { opacity: 0, transition: { duration: 0.12 } },
};

/**
 * 根据 reduced-motion 选择 variants：开启减少动态时一律退化为纯淡入。
 * 用法：const v = pickVariants(reduce, fadeUp)
 */
export function pickVariants(reduce: boolean | null, variants: Variants): Variants {
  return reduce ? fadeOnly : variants;
}

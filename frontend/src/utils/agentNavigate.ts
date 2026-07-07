import { usePageContextStore } from '../store/pageContextStore';
import { useFloatingChatUiStore } from '../store/floatingChatUiStore';

/* ================================================================
   agent 驱动导航的共用逻辑（ChatThread 完整页 + FloatingChat 浮窗共用）。
   open_page 工具 → NAVIGATE 事件 → onNavigate → 这里：
     1) 若带 symbol，写入 pageContextStore 并绑定到「目标路径」
        （导航是异步的，此刻 window.location 还是旧页，必须显式传 path）；
     2) 切页，个股相关页用 ?symbol= 让页面自动预填（如 K线分析页）；
     3) popFloating=true 时弹开浮窗并强制共享会话，让对话在缩小的
        MoneyBill 里无缝续接（完整页导航走这条）。
   ================================================================ */

export function buildAgentPath(path: string, symbol?: string): string {
  return symbol ? `${path}?symbol=${encodeURIComponent(symbol)}` : path;
}

export function navigateFromAgent(
  navigate: (to: string) => void,
  path: string,
  symbol: string | undefined,
  opts?: { popFloating?: boolean },
): void {
  if (symbol) usePageContextStore.getState().setEntities({ symbol }, path);
  navigate(buildAgentPath(path, symbol));
  if (opts?.popFloating) useFloatingChatUiStore.getState().requestOpen();
}

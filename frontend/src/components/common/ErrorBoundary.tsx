import React from 'react';

/* ================================================================
   全局错误兜底
   - ErrorBoundary：React 渲染错误 → 显示可恢复的错误页（而非白屏/整页崩溃）
   - installGlobalErrorLog：window.onerror / unhandledrejection → 存 localStorage
     （WKWebView 崩溃后会静默重载页面，console 全丢；落盘才能事后排查）
   ================================================================ */

const LAST_ERROR_KEY = 'fin-last-error';

function saveLastError(kind: string, message: string, stack?: string) {
  try {
    localStorage.setItem(
      LAST_ERROR_KEY,
      JSON.stringify({ kind, message, stack: stack?.slice(0, 2000), at: new Date().toISOString() }),
    );
  } catch {
    /* localStorage 满/不可用时放弃记录 */
  }
}

export function installGlobalErrorLog() {
  window.addEventListener('error', (e) => {
    saveLastError('error', String(e.message || e.error), e.error?.stack);
  });
  window.addEventListener('unhandledrejection', (e) => {
    const r = e.reason;
    saveLastError('unhandledrejection', String(r?.message ?? r), r?.stack);
  });
  // 上次异常留痕：启动时打出来，方便在 Web Inspector 里确认是否发生过静默重载
  try {
    const last = localStorage.getItem(LAST_ERROR_KEY);
    if (last) console.warn('[fin] 上次会话记录到的最后错误:', JSON.parse(last));
  } catch {
    /* ignore */
  }
}

interface State {
  error: Error | null;
}

export class ErrorBoundary extends React.Component<React.PropsWithChildren, State> {
  state: State = { error: null };

  static getDerivedStateFromError(error: Error): State {
    return { error };
  }

  componentDidCatch(error: Error, info: React.ErrorInfo) {
    saveLastError('react-render', error.message, `${error.stack}\n${info.componentStack}`);
  }

  render() {
    if (this.state.error) {
      return (
        <div className="flex h-screen flex-col items-center justify-center gap-4 bg-dark text-gray-300">
          <div className="text-4xl">😵</div>
          <div className="text-sm">界面出错了：{this.state.error.message}</div>
          <button
            onClick={() => this.setState({ error: null })}
            className="rounded-xl bg-primary px-4 py-2 text-sm text-white"
          >
            尝试恢复
          </button>
        </div>
      );
    }
    return this.props.children;
  }
}

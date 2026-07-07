/**
 * 打开外部链接——兼容浏览器/网页端与 Mac 桌面 App(Tauri)。
 *
 * 桌面 App 是 Tauri v2 的 WKWebView，`<a target="_blank">` 点击不会打开外链。
 * 这里检测到 Tauri 时，直接 invoke opener 插件的 Rust 命令 `plugin:opener|open_url`
 * （与插件自带 init 脚本同款调用），用系统默认浏览器打开——不依赖 @tauri-apps/plugin-opener
 * 这个 npm 包，也不受 vite 依赖优化影响，最稳。浏览器环境走 window.open。
 */
export function isTauri(): boolean {
  return typeof window !== 'undefined' && '__TAURI_INTERNALS__' in window;
}

export function openExternal(url: string | null | undefined): void {
  if (!url) return;
  if (isTauri()) {
    const internals = (window as unknown as {
      __TAURI_INTERNALS__?: { invoke: (cmd: string, args?: unknown) => Promise<unknown> };
    }).__TAURI_INTERNALS__;
    internals?.invoke('plugin:opener|open_url', { url })
      .catch((e: unknown) => {
        // 临时诊断：release 包无 devtools，invoke 若被 scope 拒会静默失败，这里显形。确诊后删。
        console.error('[openExternal] open_url failed:', e);
        // eslint-disable-next-line no-alert
        window.alert('打开外链失败: ' + String(e));
      });
    return;
  }
  window.open(url, '_blank', 'noopener,noreferrer');
}

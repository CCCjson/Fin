/**
 * 鉴权 token 的唯一声明处。
 *
 * TOKEN_KEY 与「401 → 清 token → 跳回首页」这条策略只在这里定义一次；
 * services/api.ts（axios 拦截器）与 utils/authFetch.ts（裸 fetch 收口）都从这里
 * 引用，避免两处各自硬编码同一份逻辑。
 */

export const TOKEN_KEY = 'fin_auth_token';

/** 命中 401 时清除本地 token 并跳回首页。 */
export function handleUnauthorized(): void {
  localStorage.removeItem(TOKEN_KEY);
  if (window.location.pathname !== '/') {
    window.location.href = '/';
  }
}

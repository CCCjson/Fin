/**
 * authFetch —— 带鉴权头的 fetch 收口
 *
 * 大量流式/裸 fetch 请求绕过了 axios 实例（services/api.ts），因此不带 Authorization。
 * 这个 helper 是 fetch 的 drop-in 替代：只做「注入 Bearer 头 + 401 清 token」这一件事，
 * TOKEN_KEY 与 401 处理逻辑统一从 utils/authToken.ts 取，与 api.ts 里的 axios 拦截器
 * 共用同一份，url / method / body / signal 等一律原样透传。
 */

import { TOKEN_KEY, handleUnauthorized } from './authToken';

/** 读取当前 token（与 services/api.ts 同一把 localStorage key）。 */
export function getAuthToken(): string | null {
  return localStorage.getItem(TOKEN_KEY);
}

/**
 * 与原生 fetch 签名一致，自动带上 `Authorization: Bearer <token>`。
 * 命中 401 时清除本地 token 并跳回首页（与 axios 响应拦截器行为一致）。
 */
export async function authFetch(
  input: RequestInfo | URL,
  init: RequestInit = {},
): Promise<Response> {
  const token = getAuthToken();

  // 合并 header，保留调用方原有的 Content-Type 等，不覆盖
  const headers = new Headers(init.headers || {});
  if (token && !headers.has('Authorization')) {
    headers.set('Authorization', `Bearer ${token}`);
  }

  const response = await fetch(input, { ...init, headers });

  if (response.status === 401) {
    handleUnauthorized();
  }

  return response;
}

export default authFetch;

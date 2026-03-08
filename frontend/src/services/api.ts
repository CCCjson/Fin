import axios from 'axios';
import type { AxiosRequestConfig } from 'axios';

const TOKEN_KEY = 'fin_auth_token';

const api = axios.create({
  baseURL: import.meta.env.VITE_API_URL || '/api',
  timeout: 15000,
  headers: {
    'Content-Type': 'application/json',
  },
});

// 请求拦截器 — 自动带上 Authorization
api.interceptors.request.use((config) => {
  const token = localStorage.getItem(TOKEN_KEY);
  if (token) {
    config.headers.Authorization = `Bearer ${token}`;
  }
  return config;
});

// 响应拦截器 — 直接返回 response.data
api.interceptors.response.use(
  (response) => response.data,
  (error) => {
    // 401 → 清除 token，跳回首页
    if (error?.response?.status === 401) {
      localStorage.removeItem(TOKEN_KEY);
      if (window.location.pathname !== '/') {
        window.location.href = '/';
      }
    }
    console.error('API Error:', error);
    return Promise.reject(error);
  }
);

// 响应拦截器已提取 response.data，重新声明方法签名使 TS 类型与运行时一致
interface TypedApi {
  get<T = any>(url: string, config?: AxiosRequestConfig): Promise<T>;
  post<T = any>(url: string, data?: any, config?: AxiosRequestConfig): Promise<T>;
  put<T = any>(url: string, data?: any, config?: AxiosRequestConfig): Promise<T>;
  delete<T = any>(url: string, config?: AxiosRequestConfig): Promise<T>;
  patch<T = any>(url: string, data?: any, config?: AxiosRequestConfig): Promise<T>;
}

export default api as unknown as TypedApi;

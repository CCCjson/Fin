import axios from 'axios';

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

// 响应拦截器
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

export default api;

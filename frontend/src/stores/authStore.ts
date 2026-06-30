import { create } from 'zustand';
import api from '../services/api';

const TOKEN_KEY = 'fin_auth_token';
const USER_KEY = 'fin_auth_user';

interface AuthStore {
  token: string | null;
  username: string | null;
  isAuthenticated: boolean;
  loading: boolean;
  error: string | null;
  login: (username: string, password: string) => Promise<boolean>;
  logout: () => void;
  checkAuth: () => Promise<void>;
}

export const useAuthStore = create<AuthStore>((set) => ({
  token: null,
  username: null,
  isAuthenticated: false,
  loading: true,
  error: null,

  login: async (username: string, password: string) => {
    set({ loading: true, error: null });
    try {
      const res: any = await api.post('/auth/login', { username, password });
      const token = res.token;
      localStorage.setItem(TOKEN_KEY, token);
      localStorage.setItem(USER_KEY, username);
      set({ token, username, isAuthenticated: true, loading: false, error: null });
      return true;
    } catch (err: any) {
      const msg = err?.response?.data?.detail || '登录失败';
      set({ loading: false, error: msg });
      return false;
    }
  },

  logout: () => {
    localStorage.removeItem(TOKEN_KEY);
    localStorage.removeItem(USER_KEY);
    set({ token: null, username: null, isAuthenticated: false, error: null });
  },

  checkAuth: async () => {
    const token = localStorage.getItem(TOKEN_KEY);
    const username = localStorage.getItem(USER_KEY);
    if (!token) {
      set({ token: null, username: null, isAuthenticated: false, loading: false });
      return;
    }
    try {
      const res: any = await api.get('/auth/verify', { params: { token } });
      if (res.valid) {
        set({ token, username, isAuthenticated: true, loading: false });
      } else {
        // token 确实无效（后端明确返回 invalid）
        localStorage.removeItem(TOKEN_KEY);
        localStorage.removeItem(USER_KEY);
        set({ token: null, username: null, isAuthenticated: false, loading: false });
      }
    } catch {
      // 网络错误 / 后端没启动 → 信任本地 token，不清除
      set({ token, username, isAuthenticated: true, loading: false });
    }
  },
}));

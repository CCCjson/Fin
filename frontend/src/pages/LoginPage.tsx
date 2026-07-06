import React, { useState } from 'react';

const API_BASE = import.meta.env.VITE_API_URL || '/api';
const TOKEN_KEY = 'fin_auth_token';

/**
 * 登录页 —— 挡在主 App 壳之前的鉴权门。
 * 用户名/密码 POST /auth/login，成功后把 JWT 存 localStorage['fin_auth_token']。
 */
export const LoginPage: React.FC<{ onSuccess: () => void }> = ({ onSuccess }) => {
  const [username, setUsername] = useState('');
  const [password, setPassword] = useState('');
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState('');

  const submit = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!username || !password) {
      setError('请输入用户名和密码');
      return;
    }
    setLoading(true);
    setError('');
    try {
      const resp = await fetch(`${API_BASE}/auth/login`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ username, password }),
      });
      if (resp.status === 401) {
        setError('用户名或密码错误');
        return;
      }
      if (!resp.ok) {
        setError(`登录失败（${resp.status}），请稍后再试`);
        return;
      }
      const data = await resp.json();
      if (!data?.token) {
        setError('登录响应异常：未拿到 token');
        return;
      }
      localStorage.setItem(TOKEN_KEY, data.token);
      onSuccess();
    } catch (err) {
      setError('网络异常，连不上后端');
      console.error('login error', err);
    } finally {
      setLoading(false);
    }
  };

  return (
    <div className="min-h-screen w-full flex items-center justify-center bg-[#05070A] text-white/90 px-4">
      <form
        onSubmit={submit}
        className="w-full max-w-sm rounded-2xl border border-white/10 bg-white/[0.03] p-8 shadow-2xl backdrop-blur"
      >
        <div className="mb-6 text-center">
          <div className="text-2xl font-semibold tracking-wide">Fin 量化</div>
          <div className="mt-1 text-sm text-white/40">请登录以继续</div>
        </div>

        <label className="block text-xs text-white/50 mb-1">用户名</label>
        <input
          type="text"
          autoComplete="username"
          value={username}
          onChange={(e) => setUsername(e.target.value)}
          className="mb-4 w-full rounded-lg border border-white/10 bg-black/30 px-3 py-2 text-sm outline-none focus:border-white/30"
          placeholder="用户名"
        />

        <label className="block text-xs text-white/50 mb-1">密码</label>
        <input
          type="password"
          autoComplete="current-password"
          value={password}
          onChange={(e) => setPassword(e.target.value)}
          className="mb-4 w-full rounded-lg border border-white/10 bg-black/30 px-3 py-2 text-sm outline-none focus:border-white/30"
          placeholder="密码"
        />

        {error && (
          <div className="mb-4 rounded-lg border border-red-500/30 bg-red-500/10 px-3 py-2 text-xs text-red-300">
            {error}
          </div>
        )}

        <button
          type="submit"
          disabled={loading}
          className="w-full rounded-lg bg-white/90 py-2 text-sm font-medium text-black transition hover:bg-white disabled:opacity-50"
        >
          {loading ? '登录中…' : '登 录'}
        </button>
      </form>
    </div>
  );
};

export default LoginPage;

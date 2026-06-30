import React, { useState, useRef, useEffect } from 'react';
import { useAuthStore } from '../stores/authStore';

export const LoginOverlay: React.FC = () => {
  const [username, setUsername] = useState('');
  const [password, setPassword] = useState('');
  const [shaking, setShaking] = useState(false);
  const { login, loading, error } = useAuthStore();
  const usernameRef = useRef<HTMLInputElement>(null);

  useEffect(() => {
    usernameRef.current?.focus();
  }, []);

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!username.trim() || !password.trim()) return;
    const ok = await login(username, password);
    if (!ok) {
      setShaking(true);
      setTimeout(() => setShaking(false), 500);
      setPassword('');
    }
  };

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/50 backdrop-blur-[6px]">
      <div
        className={`
          w-[90vw] max-w-sm bg-gradient-card border border-border rounded-2xl p-8
          shadow-2xl transition-transform
          ${shaking ? 'animate-jelly' : ''}
        `}
      >
        {/* 锁图标 */}
        <div className="flex justify-center mb-6">
          <div className="w-16 h-16 rounded-full bg-primary/10 flex items-center justify-center">
            <svg className="w-8 h-8 text-primary-light" fill="none" stroke="currentColor" viewBox="0 0 24 24">
              <path
                strokeLinecap="round"
                strokeLinejoin="round"
                strokeWidth={1.5}
                d="M12 15v2m-6 4h12a2 2 0 002-2v-6a2 2 0 00-2-2H6a2 2 0 00-2 2v6a2 2 0 002 2zm10-10V7a4 4 0 00-8 0v4h8z"
              />
            </svg>
          </div>
        </div>

        <h2 className="text-xl font-bold text-center text-white mb-1">
          登录 Fin
        </h2>
        <p className="text-gray-500 text-sm text-center mb-6">
          Fin 量化交易平台
        </p>

        <form onSubmit={handleSubmit} className="space-y-3">
          <input
            ref={usernameRef}
            type="text"
            value={username}
            onChange={(e) => setUsername(e.target.value)}
            placeholder="用户名"
            className="w-full px-4 py-3 rounded-xl bg-dark-lighter border border-border text-white placeholder-gray-500 focus:outline-none focus:border-primary focus:ring-1 focus:ring-primary transition-colors"
          />
          <input
            type="password"
            value={password}
            onChange={(e) => setPassword(e.target.value)}
            placeholder="密码"
            className="w-full px-4 py-3 rounded-xl bg-dark-lighter border border-border text-white placeholder-gray-500 focus:outline-none focus:border-primary focus:ring-1 focus:ring-primary transition-colors"
          />

          {error && (
            <p className="text-red-400 text-sm text-center">{error}</p>
          )}

          <button
            type="submit"
            disabled={loading || !username.trim() || !password.trim()}
            className="w-full py-3 rounded-xl bg-primary hover:bg-primary-dark text-dark font-medium transition-colors disabled:opacity-50 disabled:cursor-not-allowed"
          >
            {loading ? '验证中...' : '登 录'}
          </button>
        </form>
      </div>
    </div>
  );
};

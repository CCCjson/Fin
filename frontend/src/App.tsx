import { useEffect, useState } from 'react';
import { BrowserRouter, Routes, Route } from 'react-router-dom';
import { AppShell } from './components/shell/AppShell';
import { LoginPage } from './pages/LoginPage';
import { isTauri } from './utils/openExternal';

const TOKEN_KEY = 'fin_auth_token';
const API_BASE = import.meta.env.VITE_API_URL || '/api';

function useBackendReady(): boolean {
  // 桌面 App 加载的是打包好的静态产物，没有旧版加载页帮忙等后端冷启动
  // （pytdx 预热等大约 30-40s）；这里补一道轮询门，避免刚开 app 就先看到网络异常。
  // web 端走 vite dev，restart.sh 本身已经等后端就绪才提示"运行中"，不需要这道门。
  const [ready, setReady] = useState(() => !isTauri());

  useEffect(() => {
    if (ready) return;
    let cancelled = false;
    (async () => {
      while (!cancelled) {
        try {
          const res = await fetch(`${API_BASE}/health`);
          if (res.ok) {
            if (!cancelled) setReady(true);
            return;
          }
        } catch {
          // 后端还没起来，继续轮询
        }
        await new Promise((r) => setTimeout(r, 1000));
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [ready]);

  return ready;
}

function StartupScreen() {
  return (
    <div
      style={{
        display: 'flex',
        alignItems: 'center',
        justifyContent: 'center',
        height: '100vh',
        background: '#05070A',
        color: '#8B95A5',
        fontSize: 14,
      }}
    >
      正在启动后端服务…
    </div>
  );
}

function App() {
  const backendReady = useBackendReady();
  // 鉴权门：无 token 先挡登录页。api.ts 的 401 拦截器会清 token 并跳回 '/'，
  // 刷新后回到这里重新显示登录页。
  const [authed, setAuthed] = useState<boolean>(() => !!localStorage.getItem(TOKEN_KEY));

  if (!backendReady) {
    return <StartupScreen />;
  }

  if (!authed) {
    return <LoginPage onSuccess={() => setAuthed(true)} />;
  }

  return (
    <BrowserRouter>
      <Routes>
        {/* 统一 ChatGPT 外壳：聊天为主入口，工具页在主区 keep-alive 打开 */}
        <Route path="/*" element={<AppShell />} />
      </Routes>
    </BrowserRouter>
  );
}

export default App;

import { useState } from 'react';
import { BrowserRouter, Routes, Route } from 'react-router-dom';
import { AppShell } from './components/shell/AppShell';
import { LoginPage } from './pages/LoginPage';

const TOKEN_KEY = 'fin_auth_token';

function App() {
  // 鉴权门：无 token 先挡登录页。api.ts 的 401 拦截器会清 token 并跳回 '/'，
  // 刷新后回到这里重新显示登录页。
  const [authed, setAuthed] = useState<boolean>(() => !!localStorage.getItem(TOKEN_KEY));

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

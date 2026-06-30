import { BrowserRouter, Routes, Route } from 'react-router-dom';
import { AppShell } from './components/shell/AppShell';

function App() {
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

import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

// 只服务 Mac 桌面 App（2026-07-21 收敛，web 端已退役）：
// 产物由 `npm run build` 出静态文件，再被 `tauri build` 打进 App 包，
// 不存在 dev server 与 proxy —— API 基址由 .env 的 VITE_API_URL 绝对地址给出。
// （原先这里有 server.port=5174 / host=true / proxy→:8010 的一整块，随 web 端一起删除。）
// https://vite.dev/config/
export default defineConfig({
  plugins: [react()],
})

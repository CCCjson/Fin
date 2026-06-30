import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

// https://vite.dev/config/
export default defineConfig({
  plugins: [react()],
  server: {
    host: true,   // 监听 0.0.0.0，局域网内手机可访问
    port: 5174,
    proxy: {
      '/api': {
        target: 'http://127.0.0.1:8000',
        changeOrigin: true,
        ws: true,   // 代理 WebSocket 升级（订单簿实时推送依赖此项）
        rewrite: (path) => path.replace(/^\/api/, ''),
      },
    },
  },
})

import react from '@vitejs/plugin-react'
import { defineConfig } from 'vite'

// https://vite.dev/config/
export default defineConfig({
  plugins: [react()],
  server: {
    // 用 127.0.0.1 而非 localhost：避免 localhost 解析到 IPv6(::1) 导致代理连接失败
    proxy: { '/api': 'http://127.0.0.1:8000' },
  },
})

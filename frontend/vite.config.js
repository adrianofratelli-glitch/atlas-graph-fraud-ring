import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

export default defineConfig({
  plugins: [react()],
  server: {
    port: 5350,
    strictPort: true,
    proxy: { '/api': 'http://127.0.0.1:8350', '/health': 'http://127.0.0.1:8350' },
  },
  preview: {
    port: 5350,
    strictPort: true,
    proxy: { '/api': 'http://127.0.0.1:8350', '/health': 'http://127.0.0.1:8350' },
  },
})

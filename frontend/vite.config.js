import react from '@vitejs/plugin-react'
import { defineConfig } from 'vite'

export default defineConfig({
  plugins: [react()],
  server: {
    proxy: {
      '/applications': {
        target: 'http://127.0.0.1:8000',
        changeOrigin: true,
      },
    },
  },
})

import react from '@vitejs/plugin-react'
import { defineConfig } from 'vite'

export default defineConfig({
  plugins: [react()],
  server: {
    proxy: {
      '/applications': {
        target: 'http://127.0.0.1:8000',
        changeOrigin: true,
        // The page and API share a path: serve HTML navigation locally, proxy fetches.
        bypass(request) {
          if (request.method === 'GET' && request.headers.accept?.includes('text/html')) {
            return '/index.html'
          }
        },
      },
    },
  },
})

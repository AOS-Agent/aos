import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'
import path from 'path'

// Dev mode: VITE_API_PORT overrides the backend target (default: 4096 runtime)
const API_PORT = process.env.VITE_API_PORT ?? '4096'
const API_TARGET = `http://localhost:${API_PORT}`

export default defineConfig({
  plugins: [
    react(),
    // PWA is off. vite-plugin-pwa was removed from devDependencies: it was
    // already disabled here, and its vite@^7 peer cap broke `npm ci` for the
    // release frontend build. To re-enable, add vite-plugin-pwa@^1.3.0 (the
    // first release accepting vite@^8) and register VitePWA below.
  ],
  resolve: {
    alias: {
      '@': path.resolve(__dirname, './src'),
    },
  },
  server: {
    port: 5173,
    allowedHosts: true, // Allow all hosts (Tailscale, IP, hostname)
    proxy: {
      '/api/stream': {
        target: API_TARGET,
        changeOrigin: true,
        // SSE requires no buffering and no timeout
        configure: (proxy) => {
          proxy.on('proxyRes', (proxyRes) => {
            // Disable buffering for SSE
            proxyRes.headers['cache-control'] = 'no-cache';
            proxyRes.headers['x-accel-buffering'] = 'no';
          });
        },
      },
      '/api': {
        target: API_TARGET,
        changeOrigin: true,
      },
      '/ws': {
        target: API_TARGET,
        ws: true,
      },
      '/companion/stream': {
        target: API_TARGET,
        changeOrigin: true,
        configure: (proxy) => {
          proxy.on('proxyRes', (proxyRes) => {
            proxyRes.headers['cache-control'] = 'no-cache';
            proxyRes.headers['x-accel-buffering'] = 'no';
          });
        },
      },
      '/companion': {
        target: API_TARGET,
        changeOrigin: true,
      },
    },
  },
})

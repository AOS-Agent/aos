import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'
import path from 'path'

// Dev mode: VITE_API_PORT overrides the backend target (default: 4096 runtime)
const API_PORT = process.env.VITE_API_PORT ?? '4096'
const API_TARGET = `http://localhost:${API_PORT}`

export default defineConfig({
  plugins: [
    react(),
    // PWA disabled temporarily — workbox-build has a compatibility issue
    // with the current vite-plugin-pwa version (assignWith error).
    // Re-enable once vite-plugin-pwa is updated.
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

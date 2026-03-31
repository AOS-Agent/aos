import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'
import path from 'path'

export default defineConfig({
  plugins: [react()],
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
        target: 'http://localhost:7700',
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
        target: 'http://localhost:7700',
        changeOrigin: true,
      },
      '/ws': {
        target: 'http://localhost:7700',
        ws: true,
      },
      '/companion/stream': {
        target: 'http://localhost:7700',
        changeOrigin: true,
        // SSE requires no buffering and no timeout
        configure: (proxy) => {
          proxy.on('proxyRes', (proxyRes) => {
            proxyRes.headers['cache-control'] = 'no-cache';
            proxyRes.headers['x-accel-buffering'] = 'no';
          });
        },
      },
      '/companion': {
        target: 'http://localhost:7700',
        changeOrigin: true,
      },
    },
  },
})

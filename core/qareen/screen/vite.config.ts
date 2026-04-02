import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'
import path from 'path'

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
        configure: (proxy) => {
          proxy.on('proxyRes', (proxyRes) => {
            proxyRes.headers['cache-control'] = 'no-cache';
            proxyRes.headers['x-accel-buffering'] = 'no';
          });
        },
      },
      // CENTCOM meetings data lives on the companion service (port 7603)
      '/companion/meetings': {
        target: 'http://localhost:7603',
        changeOrigin: true,
        rewrite: (path) => path.replace('/companion/meetings', '/meetings'),
      },
      '/companion': {
        target: 'http://localhost:7700',
        changeOrigin: true,
      },
    },
  },
})

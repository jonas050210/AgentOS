import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';
import path from 'node:path';

export default defineConfig({
  plugins: [react()],
  resolve: {
    alias: {
      '@': path.resolve(__dirname, 'src'),
      '@shared': path.resolve(__dirname, 'shared'),
    },
  },
  server: {
    host: '0.0.0.0',
    port: 4318,
    strictPort: false,
    // The preview environment serves the app from a proxied host, so the
    // allow-list must not reject it.
    allowedHosts: true,
    proxy: {
      '/api': {
        target: `http://127.0.0.1:${process.env.AGENTOS_PORT ?? 4317}`,
        changeOrigin: true,
      },
    },
  },
  build: { outDir: 'dist', sourcemap: true },
  test: {
    environment: 'jsdom',
    globals: true,
    // jsdom matchers for the component tests, plus the node-side isolation of
    // the server stores (a throwaway AGENTOS_DATA_DIR per run).
    setupFiles: ['./src/test/setup.ts', './server/test-suite.setup.ts'],
    include: ['src/**/*.test.{ts,tsx}', 'server/**/*.test.ts'],
    exclude: ['node_modules', 'dist', 'dist-server'],
  },
});

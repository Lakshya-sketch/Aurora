import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';
import path from 'path';

// https://vitejs.dev/config/
export default defineConfig({
  plugins: [react()],

  // Vite uses renderer/ as the project root so index.html is at renderer/index.html
  root: path.resolve(__dirname, 'renderer'),

  build: {
    // Output to renderer/dist — Electron loads renderer/dist/index.html in production
    outDir: path.resolve(__dirname, 'renderer', 'dist'),
    emptyOutDir: true,
  },

  // CRITICAL for Electron file:// protocol: all asset references must be relative
  base: './',

  server: {
    port: 5173,
    strictPort: true,
  },

  resolve: {
    alias: {
      '@': path.resolve(__dirname, 'renderer', 'src'),
    },
  },
});

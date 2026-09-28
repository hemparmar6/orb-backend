import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';

// Admin dashboard is served by FastAPI under /api/admin-ui/.
// All asset URLs must be relative to that mount point.
export default defineConfig({
  plugins: [react()],
  base: '/api/admin-ui/',
  build: {
    outDir: 'dist',
    sourcemap: false,
    emptyOutDir: true,
  },
  server: {
    port: 5173,
    host: '0.0.0.0',
  },
});

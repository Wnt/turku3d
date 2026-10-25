import { defineConfig } from 'vite';
import wasm from 'vite-plugin-wasm';

// COOP/COEP so crossOriginIsolated / SharedArrayBuffer are available (tools/dev/serve-dist.mjs sends the same).
const isolation = { 'Cross-Origin-Opener-Policy': 'same-origin', 'Cross-Origin-Embedder-Policy': 'require-corp' };

export default defineConfig({
  plugins: [wasm()],
  worker: { format: 'es', plugins: () => [wasm()] },
  build: { target: 'es2022', chunkSizeWarningLimit: 4000 },
  optimizeDeps: { exclude: ['@dimforge/rapier3d'] },
  server: { port: 5173, headers: isolation },
  preview: { port: 4173, headers: isolation },
});

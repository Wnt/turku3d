import { defineConfig } from 'vite';
import { gameDist, port, workRoot } from '../tools/dev/environment.mjs';
import path from 'node:path';
import wasm from 'vite-plugin-wasm';

// COOP/COEP so crossOriginIsolated / SharedArrayBuffer are available (tools/dev/serve-dist.mjs sends the same).
const isolation = { 'Cross-Origin-Opener-Policy': 'same-origin', 'Cross-Origin-Embedder-Policy': 'require-corp' };

export default defineConfig({
  cacheDir: path.join(workRoot, 'cache/vite'),
  plugins: [wasm()],
  worker: { format: 'es', plugins: () => [wasm()] },
  build: { outDir: gameDist, emptyOutDir: true, target: 'es2022', chunkSizeWarningLimit: 4000 },
  optimizeDeps: { exclude: ['@dimforge/rapier3d'] },
  server: { port: port(), strictPort: true, headers: isolation },
  preview: { port: port(1), strictPort: true, headers: isolation },
});

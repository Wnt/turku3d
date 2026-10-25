import { defineConfig } from 'vite';
import path from 'node:path';
import { workRoot, port } from '../dev/environment.mjs';

export default defineConfig({
  cacheDir: path.join(workRoot, 'cache/engine-probe-vite'),
  build: { outDir: path.join(workRoot, 'build/engine-probe'), emptyOutDir: true, target: 'es2022', chunkSizeWarningLimit: 8000, rollupOptions: { input: { main: 'index.html', splat: 'splat.html', fx: 'fx.html', glb: 'glb.html' } } },
  // top-level await in main.js
  esbuild: { target: 'es2022' },
  optimizeDeps: { exclude: ['@dimforge/rapier3d-compat'] },
  // COOP/COEP so SharedArrayBuffer / WASM threads are available later (Jolt MT, Godot, etc.)
  server: { port: port(), strictPort: true, headers: { 'Cross-Origin-Opener-Policy': 'same-origin', 'Cross-Origin-Embedder-Policy': 'require-corp' } },
  preview: { port: port(3), strictPort: true, headers: { 'Cross-Origin-Opener-Policy': 'same-origin', 'Cross-Origin-Embedder-Policy': 'require-corp' } },
});

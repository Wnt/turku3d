import { defineConfig } from 'vite';

export default defineConfig({
  build: { target: 'es2022', chunkSizeWarningLimit: 8000, rollupOptions: { input: { main: 'index.html', splat: 'splat.html', fx: 'fx.html', glb: 'glb.html' } } },
  // top-level await in main.js
  esbuild: { target: 'es2022' },
  optimizeDeps: { exclude: ['@dimforge/rapier3d-compat'] },
  // COOP/COEP so SharedArrayBuffer / WASM threads are available later (Jolt MT, Godot, etc.)
  server: { headers: { 'Cross-Origin-Opener-Policy': 'same-origin', 'Cross-Origin-Embedder-Policy': 'require-corp' } },
  preview: { headers: { 'Cross-Origin-Opener-Policy': 'same-origin', 'Cross-Origin-Embedder-Policy': 'require-corp' } },
});

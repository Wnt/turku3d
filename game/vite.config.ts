import { defineConfig } from 'vite';
import { gameDist, port, workRoot } from '../tools/dev/environment.mjs';
import path from 'node:path';
import wasm from 'vite-plugin-wasm';

// COOP/COEP for local servers (tools/dev/serve-dist.mjs sends the same). The game uses no SharedArrayBuffer and runs
// without them, e.g. on GitHub Pages.
const isolation = { 'Cross-Origin-Opener-Policy': 'same-origin', 'Cross-Origin-Embedder-Policy': 'require-corp' };

export default defineConfig({
  // Relative asset URLs: the same build runs at / locally and under /turku-game/ on GitHub Pages.
  base: './',
  cacheDir: path.join(workRoot, 'cache/vite'),
  plugins: [wasm()],
  worker: { format: 'es', plugins: () => [wasm()] },
  build: { outDir: gameDist, emptyOutDir: true, target: 'es2022', chunkSizeWarningLimit: 4000 },
  optimizeDeps: { exclude: ['@dimforge/rapier3d'] },
  // /coarse/* reaches the localhost coarse tile service (tools/dev/coarse-tiles) same-origin, as through serve-dist.
  server: { port: port(), strictPort: true, headers: isolation, proxy: { '/coarse': { target: `http://127.0.0.1:${port(5)}`, configure: (proxy) => proxy.on('error', (_err, req, res) => {
    // Same contract as serve-dist: an offline service is a 200 answer on /coarse/status, never a page console error.
    if (!('writeHead' in res) || res.headersSent) return;
    res.writeHead(req.url?.split('?')[0] === '/coarse/status' ? 200 : 503, { 'Content-Type': 'application/json' });
    res.end('{"state":"offline"}\n');
  }) } } },
  preview: { port: port(1), strictPort: true, headers: isolation },
});

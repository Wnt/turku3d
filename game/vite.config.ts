import { defineConfig } from 'vite';
import { gameDist, port, tileDist, workRoot } from '../tools/dev/environment.mjs';
import { localhostGame } from '../tools/dev/localhost/vitePlugin.mjs';
import path from 'node:path';
import wasm from 'vite-plugin-wasm';
import { capturePlugin } from '../tools/dev/capture.mjs';
import { editSessionsPlugin } from '../tools/dev/editSessions.mjs';
import { execFileSync } from 'node:child_process';
const captureCommit = execFileSync('git', ['rev-parse', 'HEAD'], { encoding: 'utf8' }).trim();

// Local servers isolate directly; static hosts use public/isolation.js for the shared physics exchange.
const isolation = { 'Document-Policy': 'js-profiling', 'Cross-Origin-Opener-Policy': 'same-origin', 'Cross-Origin-Embedder-Policy': 'require-corp' };

export default defineConfig({
  // Relative asset URLs: the same build runs at / locally and under /turku-game/ on GitHub Pages.
  base: './',
  define: { 'import.meta.env.VITE_COMMIT': JSON.stringify(captureCommit) },
  cacheDir: path.join(workRoot, 'cache/vite'),
  // Dev server only: /tiles from the sandbox dist (or the localhost service's tile release, tools/dev/localhost-game),
  // POST /telemetry (also on `vite preview`, the browser checks' server) and the localhost build status.
  plugins: [wasm(), capturePlugin(), editSessionsPlugin(), {
    name: 'build-head',
    configureServer(server) {
      let cached = { at: 0, commit: '' };
      server.middlewares.use('/__build', (_req, res) => {
        if (Date.now() - cached.at > 3000) {
          try { cached = { at: Date.now(), commit: execFileSync('git', ['rev-parse', 'HEAD'], { encoding: 'utf8' }).trim() }; } catch { /* keep the last */ }
        }
        res.writeHead(200, { 'Content-Type': 'application/json', 'Cache-Control': 'no-store' });
        res.end(JSON.stringify({ commit: cached.commit }));
      });
    },
    // Every page load is stamped with the worktree it was served from (src/core/build.ts records it in captures).
    transformIndexHtml: { order: 'pre', handler() {
      let content = captureCommit;
      try {
        const head = execFileSync('git', ['rev-parse', 'HEAD'], { encoding: 'utf8' }).trim();
        const dirty = execFileSync('git', ['status', '--porcelain', '--untracked-files=no'], { encoding: 'utf8' }).trim() !== '';
        content = head + (dirty ? '+dirty' : '');
      } catch { /* keep the server's start commit */ }
      return [{ tag: 'meta', attrs: { name: 'turku3d-build', content }, injectTo: 'head' }];
    } },
  }, {
    name: 'preload-game-entry',
    transformIndexHtml: { order: 'post', handler(_html, context) {
      // bootstrap waits for isolation; discover the large dynamic entry during HTML parsing.
      const main = Object.values(context.bundle ?? {}).find(chunk => chunk.type === 'chunk'
        && chunk.moduleIds.some(id => id.endsWith('/src/main.ts')));
      return main ? [{ tag: 'link', attrs: { rel: 'modulepreload', href: `./${main.fileName}`, crossorigin: '' }, injectTo: 'head' }] : [];
    } },
  }, localhostGame({ statePath: process.env.TURKU3D_LOCALHOST_STATE, tiles: tileDist,
    telemetryDir: process.env.TURKU3D_TELEMETRY_DIR ?? path.join(workRoot, 'telemetry') })],
  worker: { format: 'es', plugins: () => [wasm()] },
  build: { outDir: gameDist, emptyOutDir: true, target: 'es2022', chunkSizeWarningLimit: 4000 },
  optimizeDeps: { exclude: ['@dimforge/rapier3d'] },
  // /coarse/* reaches the localhost coarse tile service (tools/dev/coarse-tiles) same-origin, as through serve-dist.
  // No HMR: merges land in the served worktree every few minutes and each one used to reload Jonni's test session.
  // The page polls /__build instead and shows a 'new build ready' notice (src/dev/buildNotice.ts); F5 loads it.
  server: { hmr: false, port: port(), strictPort: true, headers: isolation, proxy: { '/coarse': { target: process.env.TURKU3D_COARSE_PROXY ?? `http://127.0.0.1:${port(5)}`, configure: (proxy) => proxy.on('error', (_err, req, res) => {
    // Same contract as serve-dist: an offline service is a 200 answer on /coarse/status, never a page console error.
    if (!('writeHead' in res) || res.headersSent) return;
    res.writeHead(req.url?.split('?')[0] === '/coarse/status' ? 200 : 503, { 'Content-Type': 'application/json' });
    res.end('{"state":"offline"}\n');
  }) },
  // /pointcloud/* is the LiDAR overlay's COPC product (tools/pointcloud/serve.py on osgallery-dev; range requests).
  '/pointcloud': { target: process.env.TURKU3D_POINTCLOUD ?? 'http://192.168.1.114:7960', configure: (proxy) => proxy.on('error', (_err, _req, res) => {
    if (!('writeHead' in res) || res.headersSent) return;
    res.writeHead(503, { 'Content-Type': 'application/json' });
    res.end('{"state":"offline"}\n');
  }) } } },
  preview: { port: port(1), strictPort: true, headers: isolation },
});

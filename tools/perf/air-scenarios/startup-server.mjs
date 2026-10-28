// Capture-only Vite server with same-port multiplayer proxy for the exclusive Air tunnel.
import path from 'node:path';
import { serveFile, resolveUnder } from '../../dev/http.mjs';
import { createServer } from '../../../game/node_modules/vite/dist/node/index.js';
const [root = 'game', port = '5216', mpPort = '5214', production] = process.argv.slice(2);
const server = await createServer({ root: path.resolve(root), configFile: path.resolve(root, 'vite.config.ts'),
  server: { host: '127.0.0.1', port: Number(port), strictPort: true,
    proxy: { '/startup-mp': { target: `ws://127.0.0.1:${mpPort}`, ws: true, rewrite: () => '/' } } },
  plugins: [{ name: 'startup-blank', configureServer(vite) {
    if (production) vite.middlewares.use((req, res, next) => {
      if (!req.url.startsWith('/game/')) return next();
      void serveFile(req, res, resolveUnder(path.resolve(production), req.url.slice('/game'.length)));
    });
    vite.middlewares.use('/__startup-blank', (_req, res) => { res.setHeader('Content-Type', 'text/html'); res.end('<title>Startup measurement</title>'); });
  } }] });
await server.listen();
server.printUrls();

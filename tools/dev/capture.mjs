// Local Vite/preview and private-share capture storage. No production endpoint is shipped.
import fs from 'node:fs/promises';
import path from 'node:path';
import os from 'node:os';
const files = ['poses', 'frames', 'loaf', 'stats', 'marks', 'js'];
export function captureMiddleware(root = process.env.TURKU3D_CAPTURES ?? path.join(os.homedir(), 'turku3d/captures')) {
  const queues = new Map();
  return async (req, res, next) => {
    const url = new URL(req.url, 'http://localhost');
    if (!url.pathname.startsWith('/__capture/')) return next();
    const parts = url.pathname.slice(11).split('/');
    const [id, file] = parts;
    const reply = (status, value) => { res.writeHead(status, { 'Content-Type': 'application/json', 'Cache-Control': 'no-store' }); res.end(JSON.stringify(value)); };
    if (!/^[a-zA-Z0-9][a-zA-Z0-9_-]{0,79}$/.test(id ?? '') || parts.length > 2) return reply(400, { error: 'Invalid capture path' });
    if (req.headers.origin && new URL(req.headers.origin).host !== req.headers.host) return reply(403, { error: 'Same origin required' });
    const dir = path.join(root, id);
    try {
      if (req.method === 'GET' && ['meta.json', ...files.map(f => `${f}.jsonl`)].includes(file)) {
        const body = await fs.readFile(path.join(dir, file)); res.writeHead(200, { 'Content-Type': 'application/json', 'Cache-Control': 'no-store' }); return res.end(body);
      }
      if (req.method !== 'POST' || file) return reply(405, { error: 'Unsupported capture request' });
      let body = '', size = 0;
      for await (const chunk of req) { size += chunk.length; if (size > 8 * 1024 * 1024) return reply(413, { error: 'Capture batch too large' }); body += chunk; }
      const batch = JSON.parse(body);
      if (!Number.isSafeInteger(batch.batch) || batch.batch < 0 || files.some(f => batch[f] !== undefined && !Array.isArray(batch[f]))) return reply(400, { error: 'Invalid batch' });
      const write = (queues.get(id) ?? Promise.resolve()).catch(() => {}).then(async () => {
        await fs.mkdir(dir, { recursive: true });
        // A retry after a lost response must not duplicate a successful batch.
        const receipt = path.join(dir, `.batch-${batch.batch}`);
        try { await fs.access(receipt); return; } catch {}
        if (batch.meta) await fs.writeFile(path.join(dir, 'meta.json'), JSON.stringify(batch.meta, null, 1));
        for (const f of files) {
          const rows = batch[f] ?? [];
          const data = f === 'frames' ? (rows.length ? JSON.stringify(rows) + '\n' : '') : rows.map(row => JSON.stringify(row) + '\n').join('');
          await fs.appendFile(path.join(dir, `${f}.jsonl`), data);
        }
        await fs.writeFile(receipt, 'ok');
      });
      queues.set(id, write);
      try { await write; } finally { if (queues.get(id) === write) queues.delete(id); }
      return reply(200, { id, batch: batch.batch });
    } catch (error) { return reply(error.code === 'ENOENT' ? 404 : 500, { error: String(error.message) }); }
  };
}
export function capturePlugin() {
  return { name: 'local-performance-capture', configureServer(server) { server.middlewares.use(captureMiddleware()); },
    configurePreviewServer(server) { server.middlewares.use(captureMiddleware()); } };
}

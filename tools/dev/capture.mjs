// Local Vite/preview and private-share capture storage. No production endpoint is shipped.
import fs from 'node:fs/promises';
import path from 'node:path';
import os from 'node:os';
const files = ['poses', 'frames', 'loaf', 'stats', 'marks', 'js', 'network'];
// Keep the unfinished batch and its original file lengths until every append commits.
// A failed write can be retried after a Vite restart without duplicating a partial row.
async function applyBatch(dir, pending, transaction) {
  const { batch, offsets } = transaction;
  const receipt = path.join(dir, `.batch-${batch.batch}`);
  let committed = false;
  try { await fs.access(receipt); committed = true; } catch (error) { if (error.code !== 'ENOENT') throw error; }
  if (committed) {
    try { await fs.unlink(pending); } catch (error) { if (error.code !== 'ENOENT') throw error; }
    return;
  }
  for (const file of files) {
    const target = path.join(dir, `${file}.jsonl`);
    try { await fs.truncate(target, offsets[file]); } catch (error) { if (error.code !== 'ENOENT' || offsets[file] !== 0) throw error; }
    const rows = batch[file] ?? [];
    const data = file === 'frames' ? (rows.length ? JSON.stringify(rows) + '\n' : '') : rows.map(row => JSON.stringify(row) + '\n').join('');
    await fs.appendFile(target, data);
  }
  if (batch.meta) {
    const temporary = path.join(dir, 'meta.json.pending');
    await fs.writeFile(temporary, JSON.stringify(batch.meta, null, 1));
    await fs.rename(temporary, path.join(dir, 'meta.json'));
  }
  // Publish the receipt before removing recovery data. Recovery skips already committed batches.
  const temporary = `${receipt}.tmp`;
  await fs.writeFile(temporary, 'ok'); await fs.rename(temporary, receipt); await fs.unlink(pending);
}

async function recoverBatches(dir) {
  let entries;
  try { entries = await fs.readdir(dir); } catch (error) { if (error.code === 'ENOENT') return; throw error; }
  const pending = entries.filter(name => /^\.batch-\d+\.pending$/.test(name))
    .sort((a, b) => Number(a.split('-')[1].split('.')[0]) - Number(b.split('-')[1].split('.')[0]));
  for (const name of pending) {
    const file = path.join(dir, name);
    await applyBatch(dir, file, JSON.parse(await fs.readFile(file, 'utf8')));
  }
}

export function captureMiddleware(root = process.env.TURKU3D_CAPTURES ?? path.join(os.homedir(), 'turku3d/captures')) {
  const queues = new Map();
  const serial = (id, operation) => {
    const task = (queues.get(id) ?? Promise.resolve()).catch(() => {}).then(operation);
    queues.set(id, task);
    return task.finally(() => { if (queues.get(id) === task) queues.delete(id); });
  };
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
        const body = await serial(id, async () => { await recoverBatches(dir); return fs.readFile(path.join(dir, file)); }); res.writeHead(200, { 'Content-Type': 'application/json', 'Cache-Control': 'no-store' }); return res.end(body);
      }
      if (req.method !== 'POST' || file) return reply(405, { error: 'Unsupported capture request' });
      let body = '', size = 0;
      for await (const chunk of req) { size += chunk.length; if (size > 8 * 1024 * 1024) return reply(413, { error: 'Capture batch too large' }); body += chunk; }
      const batch = JSON.parse(body);
      if (!Number.isSafeInteger(batch.batch) || batch.batch < 0 || files.some(f => batch[f] !== undefined && !Array.isArray(batch[f]))) return reply(400, { error: 'Invalid batch' });
      await serial(id, async () => {
        await fs.mkdir(dir, { recursive: true }); await recoverBatches(dir);
        const receipt = path.join(dir, `.batch-${batch.batch}`);
        try { await fs.access(receipt); return; } catch (error) { if (error.code !== 'ENOENT') throw error; }
        const offsets = Object.fromEntries(await Promise.all(files.map(async file => {
          try { return [file, (await fs.stat(path.join(dir, `${file}.jsonl`))).size]; }
          catch (error) { if (error.code === 'ENOENT') return [file, 0]; throw error; }
        })));
        const pending = `${receipt}.pending`;
        const temporary = `${pending}.tmp`;
        const transaction = { batch, offsets };
        await fs.writeFile(temporary, JSON.stringify(transaction)); await fs.rename(temporary, pending);
        await applyBatch(dir, pending, transaction);
      });
      return reply(200, { id, batch: batch.batch });
    } catch (error) { return reply(error.code === 'ENOENT' ? 404 : 500, { error: String(error.message) }); }
  };
}
export function capturePlugin() {
  return { name: 'local-performance-capture', configureServer(server) { server.middlewares.use(captureMiddleware()); },
    configurePreviewServer(server) { server.middlewares.use(captureMiddleware()); } };
}

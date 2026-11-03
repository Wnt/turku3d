// Local Vite/preview storage for the in-game object editor's edit sessions (game/src/dev/editor/session.ts).
// ~/turku3d/edits/<session>/session.json, edits.jsonl (one line per row id, in seq order; a resent row replaces its
// line) and shots/<edit>-before.jpg|after.jpg. Format: ~/turku3d/loops/editor/EDIT-FORMAT.md. No production endpoint.
import fs from 'node:fs/promises';
import path from 'node:path';
import os from 'node:os';

const ID = /^[a-zA-Z0-9][a-zA-Z0-9_-]{0,79}$/;
const SHOT = /^shots\/[a-zA-Z0-9][a-zA-Z0-9_-]{0,100}\.(jpg|png)$/;
const MAX_JSON = 4 * 1024 * 1024, MAX_SHOT = 2 * 1024 * 1024;

async function body(req, limit) {
  const chunks = []; let size = 0;
  for await (const chunk of req) { size += chunk.length; if (size > limit) throw Object.assign(new Error('Too large'), { status: 413 }); chunks.push(chunk); }
  return Buffer.concat(chunks);
}

async function atomicWrite(file, data) {
  await fs.mkdir(path.dirname(file), { recursive: true });
  const temporary = `${file}.${process.pid}.${Date.now()}.tmp`;
  await fs.writeFile(temporary, data); await fs.rename(temporary, file);
}

/** Merge rows into edits.jsonl by id (last write wins), ordered by seq then id. */
export async function mergeRows(file, rows) {
  const byId = new Map();
  try {
    for (const line of (await fs.readFile(file, 'utf8')).split('\n')) {
      if (!line.trim()) continue;
      try { const row = JSON.parse(line); if (row?.id) byId.set(row.id, row); } catch { /* keep going: a torn line is dropped */ }
    }
  } catch (error) { if (error.code !== 'ENOENT') throw error; }
  for (const row of rows) byId.set(row.id, row);
  const ordered = [...byId.values()].sort((a, b) => (a.seq ?? 0) - (b.seq ?? 0) || String(a.id).localeCompare(String(b.id)));
  await atomicWrite(file, ordered.map(row => JSON.stringify(row)).join('\n') + (ordered.length ? '\n' : ''));
  return ordered.length;
}

export function editSessionsMiddleware(root = process.env.TURKU3D_EDITS ?? path.join(os.homedir(), 'turku3d/edits')) {
  const queues = new Map();
  const serial = (id, operation) => {
    const task = (queues.get(id) ?? Promise.resolve()).catch(() => {}).then(operation);
    queues.set(id, task);
    return task.finally(() => { if (queues.get(id) === task) queues.delete(id); });
  };
  return async (req, res, next) => {
    const url = new URL(req.url, 'http://localhost');
    if (!url.pathname.startsWith('/__edits/')) return next();
    const reply = (status, value) => { res.writeHead(status, { 'Content-Type': 'application/json', 'Cache-Control': 'no-store' }); res.end(JSON.stringify(value)); };
    const [id, ...rest] = url.pathname.slice('/__edits/'.length).split('/');
    const file = rest.join('/');
    if (!ID.test(id ?? '')) return reply(400, { error: 'Invalid session id' });
    if (req.headers.origin && new URL(req.headers.origin).host !== req.headers.host) return reply(403, { error: 'Same origin required' });
    const dir = path.join(root, id);
    try {
      if (req.method === 'GET' && (file === 'session.json' || file === 'edits.jsonl')) {
        const data = await fs.readFile(path.join(dir, file));
        res.writeHead(200, { 'Content-Type': 'application/json', 'Cache-Control': 'no-store' }); return res.end(data);
      }
      if (req.method === 'PUT' && file === 'session.json') {
        const meta = JSON.parse((await body(req, MAX_JSON)).toString('utf8'));
        if (meta?.id !== id) return reply(400, { error: 'Session id mismatch' });
        await serial(id, () => atomicWrite(path.join(dir, 'session.json'), JSON.stringify(meta, null, 1) + '\n'));
        return reply(200, { id });
      }
      if (req.method === 'POST' && file === 'edits.jsonl') {
        const rows = JSON.parse((await body(req, MAX_JSON)).toString('utf8'));
        if (!Array.isArray(rows) || rows.some(r => !r || typeof r.id !== 'string' || r.session !== id || !Number.isSafeInteger(r.seq)))
          return reply(400, { error: 'Invalid rows' });
        const count = await serial(id, () => mergeRows(path.join(dir, 'edits.jsonl'), rows));
        return reply(200, { id, rows: count });
      }
      if (req.method === 'PUT' && SHOT.test(file)) {
        const data = await body(req, MAX_SHOT);
        await serial(id, () => atomicWrite(path.join(dir, file), data));
        return reply(200, { id, file });
      }
      return reply(405, { error: 'Unsupported edit-session request' });
    } catch (error) { return reply(error.status ?? (error.code === 'ENOENT' ? 404 : 500), { error: String(error.message) }); }
  };
}

export function editSessionsPlugin() {
  return { name: 'local-edit-sessions', configureServer(server) { server.middlewares.use(editSessionsMiddleware()); },
    configurePreviewServer(server) { server.middlewares.use(editSessionsMiddleware()); } };
}

// HTTP handlers shared by the static dist server (serve-dist.mjs) and the localhost game's Vite dev server
// (localhost/vitePlugin.mjs):
//  - static files with COOP/COEP + CORP + CORS, brotli (precompressed <file>.br, else on the fly with an in-memory cache)
//    and gzip fallback, byte ranges (206) on the identity representation, ETag / If-None-Match, game MIME types
//  - POST /telemetry: play telemetry JSON lines (game/src/telemetry/README.md) appended to
//    <dir>/session-<YYYYMMDD>.jsonl (local date); 1 MB per request, 204
//  - /coarse/* proxied to the localhost coarse tile service; when it is not running /coarse/status answers 200
//    {"state":"offline"} and other paths 503
// No dependencies (Node >= 20).
import http from 'node:http';
import fs from 'node:fs';
import fsp from 'node:fs/promises';
import path from 'node:path';
import zlib from 'node:zlib';

const ISOLATION = {
  'Cross-Origin-Opener-Policy': 'same-origin',
  'Cross-Origin-Embedder-Policy': 'require-corp',
  'Cross-Origin-Resource-Policy': 'cross-origin',
};

const MIME = {
  '.html': 'text/html; charset=utf-8', '.js': 'text/javascript; charset=utf-8', '.mjs': 'text/javascript; charset=utf-8',
  '.css': 'text/css; charset=utf-8', '.json': 'application/json; charset=utf-8', '.wasm': 'application/wasm',
  '.glb': 'model/gltf-binary', '.gltf': 'model/gltf+json', '.bin': 'application/octet-stream', '.ktx2': 'image/ktx2',
  '.col': 'application/octet-stream', '.png': 'image/png', '.jpg': 'image/jpeg', '.jpeg': 'image/jpeg', '.webp': 'image/webp',
  '.svg': 'image/svg+xml', '.txt': 'text/plain; charset=utf-8', '.md': 'text/markdown; charset=utf-8', '.map': 'application/json',
};
// Compressible types (ktx2 ETC1S/png/jpg are already compressed; glb/col/bin/wasm/json/js benefit from brotli)
const COMPRESSIBLE = new Set(['.html', '.js', '.mjs', '.css', '.json', '.wasm', '.glb', '.gltf', '.bin', '.col', '.svg', '.txt', '.md', '.map']);
const brCache = new Map(); // key path|mtime -> Buffer
const BR_CACHE_MAX = 512 * 1024 * 1024;
let brCacheBytes = 0;

/** `urlPath` under `base`, or null when it escapes it (traversal). */
export function resolveUnder(base, urlPath) {
  const full = path.join(base, path.normalize(decodeURIComponent(urlPath.split('?')[0])));
  return full === base || full.startsWith(base + path.sep) ? full : null;
}

function baseHeaders(ext, stat, cacheControl) {
  return {
    ...ISOLATION,
    'Access-Control-Allow-Origin': '*',
    'Access-Control-Expose-Headers': 'Content-Length, Content-Range, Content-Encoding, ETag',
    'Accept-Ranges': 'bytes',
    'Content-Type': MIME[ext] ?? 'application/octet-stream',
    'Last-Modified': stat.mtime.toUTCString(),
    ETag: `"${stat.size.toString(16)}-${Math.floor(stat.mtimeMs).toString(16)}"`,
    'Cache-Control': cacheControl ?? (ext === '.html' || ext === '.json' ? 'no-cache' : 'public, max-age=60'),
    Vary: 'Accept-Encoding',
  };
}

async function brotli(file, stat) {
  const key = `${file}|${stat.mtimeMs}`;
  const hit = brCache.get(key);
  if (hit) return hit;
  const raw = await fsp.readFile(file);
  const out = zlib.brotliCompressSync(raw, { params: { [zlib.constants.BROTLI_PARAM_QUALITY]: 5, [zlib.constants.BROTLI_PARAM_SIZE_HINT]: raw.length } });
  if (brCacheBytes + out.length < BR_CACHE_MAX) { brCache.set(key, out); brCacheBytes += out.length; }
  return out;
}

/** Serves `file` (a directory serves its index.html); 404 when missing. `cacheControl` overrides the per-type
 *  default (no-cache for HTML/JSON, 60 s for the rest). */
export async function serveFile(req, res, file, { compress = true, cacheControl } = {}) {
  let stat;
  try { stat = await fsp.stat(file); if (stat.isDirectory()) { file = path.join(file, 'index.html'); stat = await fsp.stat(file); } }
  catch { res.writeHead(404, { 'Content-Type': 'text/plain', 'Cross-Origin-Resource-Policy': 'cross-origin' }); return res.end('not found'); }
  const ext = path.extname(file).toLowerCase();
  const h = baseHeaders(ext, stat, cacheControl);
  if (req.headers['if-none-match'] === h.ETag) { res.writeHead(304, h); return res.end(); }
  const range = req.headers.range;
  const ae = String(req.headers['accept-encoding'] ?? '');

  // Range requests are served on the identity (uncompressed) representation.
  if (range) {
    const m = /^bytes=(\d*)-(\d*)$/.exec(range);
    if (!m || (m[1] === '' && m[2] === '')) { res.writeHead(416, { ...h, 'Content-Range': `bytes */${stat.size}` }); return res.end(); }
    const start = m[1] === '' ? Math.max(0, stat.size - Number(m[2])) : Number(m[1]);
    const end = m[1] === '' ? stat.size - 1 : m[2] === '' ? stat.size - 1 : Math.min(Number(m[2]), stat.size - 1);
    if (start > end || start >= stat.size) { res.writeHead(416, { ...h, 'Content-Range': `bytes */${stat.size}` }); return res.end(); }
    res.writeHead(206, { ...h, 'Content-Range': `bytes ${start}-${end}/${stat.size}`, 'Content-Length': end - start + 1 });
    if (req.method === 'HEAD') return res.end();
    return fs.createReadStream(file, { start, end }).pipe(res);
  }
  if (compress && COMPRESSIBLE.has(ext) && /\bbr\b/.test(ae)) {
    let body;
    try { body = await fsp.readFile(file + '.br'); } catch { body = await brotli(file, stat); }
    res.writeHead(200, { ...h, 'Content-Encoding': 'br', 'Content-Length': body.length });
    return res.end(req.method === 'HEAD' ? undefined : body);
  }
  if (compress && COMPRESSIBLE.has(ext) && /\bgzip\b/.test(ae)) {
    res.writeHead(200, { ...h, 'Content-Encoding': 'gzip' });
    if (req.method === 'HEAD') return res.end();
    return fs.createReadStream(file).pipe(zlib.createGzip({ level: 6 })).pipe(res);
  }
  res.writeHead(200, { ...h, 'Content-Length': stat.size });
  if (req.method === 'HEAD') return res.end();
  fs.createReadStream(file).pipe(res);
}

const TELEMETRY_MAX_BYTES = 1024 * 1024;
const pad = (n) => String(n).padStart(2, '0');

/** POST /telemetry handler appending to today's session file in `dir`; every non-empty line must be a JSON object. */
export function telemetryHandler(dir) {
  let writes = Promise.resolve();
  return async (req, res) => {
    const cors = { 'Access-Control-Allow-Origin': '*', 'Cross-Origin-Resource-Policy': 'cross-origin' };
    const chunks = [];
    let bytes = 0;
    for await (const chunk of req) { // drained to the end, so the 413 reaches the client
      bytes += chunk.length;
      if (bytes <= TELEMETRY_MAX_BYTES) chunks.push(chunk);
    }
    if (bytes > TELEMETRY_MAX_BYTES) { res.writeHead(413, cors); return res.end('telemetry batch over 1 MB'); }
    const lines = Buffer.concat(chunks).toString('utf8').split('\n').map((l) => l.trim()).filter(Boolean);
    for (const line of lines) {
      let value;
      try { value = JSON.parse(line); } catch { value = null; }
      if (value === null || typeof value !== 'object' || Array.isArray(value)) { res.writeHead(400, cors); return res.end('each line must be a JSON object'); }
    }
    if (lines.length) {
      const d = new Date();
      const file = path.join(dir, `session-${d.getFullYear()}${pad(d.getMonth() + 1)}${pad(d.getDate())}.jsonl`);
      const write = writes.then(async () => {
        await fsp.mkdir(dir, { recursive: true });
        await fsp.appendFile(file, lines.join('\n') + '\n');
      });
      writes = write.catch(() => {});
      await write;
    }
    res.writeHead(204, cors);
    res.end();
  };
}

/** Same-origin access to the coarse tile service at `target` (a URL). */
export function coarseProxy(target) {
  return (req, res) => {
    const upstream = http.request({ host: target.hostname, port: target.port, path: req.url, method: req.method, headers: { accept: req.headers.accept ?? '*/*' } }, (up) => {
      res.writeHead(up.statusCode ?? 502, { ...up.headers, ...ISOLATION });
      up.pipe(res);
    });
    upstream.on('error', () => {
      if (res.headersSent) return res.destroy();
      // The game probes /coarse/status on every local page load: an offline service is an answer, not an error.
      res.writeHead(req.url.split('?')[0] === '/coarse/status' ? 200 : 503, { 'Content-Type': 'application/json', ...ISOLATION });
      res.end('{"state":"offline"}\n');
    });
    upstream.end();
  };
}

// HTTP handlers shared by the static dist server (serve-dist.mjs) and the localhost game's Vite dev server
// (localhost/vitePlugin.mjs):
//  - static files with COOP/COEP + CORP + CORS, brotli (precompressed <file>.br, else compressed whole with an in-memory
//    cache) and gzip fallback, every body with its exact Content-Length, byte ranges (206) on the identity
//    representation, ETag / If-None-Match, game MIME types
//  - POST /telemetry: play telemetry JSON lines (game/src/telemetry/README.md) appended to
//    <dir>/session-<YYYYMMDD>.jsonl (local date); 1 MB per request, 204
//  - /coarse/* proxied to the localhost coarse tile service; when it is not running /coarse/status answers 200
//    {"state":"offline"} and other paths 503
//  - publicProxy: a public share's compressed GET/HEAD proxy (serve-dist.mjs --public), exact lengths, cut bodies stay cut
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
const brCache = new Map(); // key encoding|path|mtime -> Buffer
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

const encoding = new Map(); // key -> the compression in flight, shared by every request for it
const brotliParams = (raw) => ({ params: { [zlib.constants.BROTLI_PARAM_QUALITY]: 5, [zlib.constants.BROTLI_PARAM_SIZE_HINT]: raw.length } });
/** A `.br` sidecar next to `file` that is at least as new as it (an older one belongs to replaced content), or null. */
async function sidecar(file, stat) {
  try {
    const br = await fsp.stat(file + '.br');
    return br.mtimeMs >= stat.mtimeMs ? await fsp.readFile(file + '.br') : null;
  } catch { return null; }
}

/** Writes `<file>.br` (as served at runtime) for every compressible file of at least `minBytes` under `dir` that has no
 *  current one, `parallel` at a time on zlib's thread pool. The share deploy runs it on each dist (tools/share/
 *  precompress.mjs), so city-web never compresses a big file on a player's request. */
export async function precompress(dir, { minBytes = 256 * 1024, parallel = 4 } = {}) {
  const todo = [];
  const walk = async (d) => {
    for (const e of await fsp.readdir(d, { withFileTypes: true })) {
      const f = path.join(d, e.name);
      if (e.isDirectory()) await walk(f);
      else if (e.isFile() && COMPRESSIBLE.has(path.extname(f).toLowerCase())) {
        const st = await fsp.stat(f);
        if (st.size >= minBytes && !(await fsp.stat(f + '.br').then((b) => b.mtimeMs >= st.mtimeMs, () => false))) todo.push(f);
      }
    }
  };
  await walk(dir);
  const done = { files: 0, bytesIn: 0, bytesOut: 0 };
  const next = async () => {
    for (let f; (f = todo.shift());) {
      const raw = await fsp.readFile(f);
      const out = await new Promise((resolve, reject) => zlib.brotliCompress(raw, brotliParams(raw), (e, b) => (e ? reject(e) : resolve(b))));
      await fsp.writeFile(f + '.br.tmp', out); await fsp.rename(f + '.br.tmp', f + '.br');
      done.files++; done.bytesIn += raw.length; done.bytesOut += out.length;
    }
  };
  await Promise.all(Array.from({ length: parallel }, next));
  return done;
}
/** `file` compressed whole (`br` or `gzip`), kept in memory per encoding and mtime: it goes out with its exact length.
 *  Compressed off the event loop (zlib's thread pool), once however many requests ask meanwhile: compressed in place,
 *  the first request for the 33 MB walkers.bin after each deploy held every other request on city.lab for 3.5-4.7 s
 *  (a cold boot's collision and tile requests among them; loadspeed 2026-11-09). */
async function encodedFile(file, stat, kind) {
  const key = `${kind}|${file}|${stat.mtimeMs}`;
  const hit = brCache.get(key);
  if (hit) { brCache.delete(key); brCache.set(key, hit); return hit; }
  let pending = encoding.get(key);
  if (!pending) {
    pending = (async () => {
      const raw = await fsp.readFile(file);
      const out = await new Promise((resolve, reject) => {
        const done = (error, body) => (error ? reject(error) : resolve(body));
        if (kind === 'br') zlib.brotliCompress(raw, brotliParams(raw), done);
        else zlib.gzip(raw, { level: 6 }, done);
      });
      // Least recently compressed first out: a long-running share server sees each deploy's files under new mtimes.
      if (out.length < BR_CACHE_MAX / 8) {
        brCache.set(key, out); brCacheBytes += out.length;
        for (const [old, body] of brCache) {
          if (brCacheBytes <= BR_CACHE_MAX) break;
          brCache.delete(old); brCacheBytes -= body.length;
        }
      }
      return out;
    })().finally(() => encoding.delete(key));
    encoding.set(key, pending);
  }
  return pending;
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
    const body = (await sidecar(file, stat)) ?? (await encodedFile(file, stat, 'br'));
    res.writeHead(200, { ...h, 'Content-Encoding': 'br', 'Content-Length': body.length });
    return res.end(req.method === 'HEAD' ? undefined : body);
  }
  if (compress && COMPRESSIBLE.has(ext) && /\bgzip\b/.test(ae)) {
    const body = await encodedFile(file, stat, 'gzip');
    res.writeHead(200, { ...h, 'Content-Encoding': 'gzip', 'Content-Length': body.length });
    return res.end(req.method === 'HEAD' ? undefined : body);
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

const ENCODABLE = /^(?:model\/gltf-binary|application\/(?:json|octet-stream)|text\/)/;
// Answers up to this size are read whole before the first byte goes out; a larger one declares its own length and passes through.
const WHOLE_MAX = 128 * 1024 * 1024;
const brotliAsync = (body) => new Promise((resolve, reject) => zlib.brotliCompress(body, {
  params: { [zlib.constants.BROTLI_PARAM_QUALITY]: 4, [zlib.constants.BROTLI_PARAM_SIZE_HINT]: body.length },
}, (error, out) => (error ? reject(error) : resolve(out))));
const gzipAsync = (body) => new Promise((resolve, reject) => zlib.gzip(body, { level: 6 }, (error, out) => (error ? reject(error) : resolve(out))));

/** A public share's same-origin GET/HEAD access to `target` (a URL): the upstream answer with the isolation headers,
 *  compressed for the browser (tile payloads shrink about 3x, which is what the tunnel carries). `reduce(json)` rewrites
 *  a JSON answer, e.g. a service status to the fields a game reads. `cacheBytes` keeps that many bytes of encoded
 *  immutable answers in memory, per encoding and URL.
 *
 *  Every answer leaves with an exact Content-Length of the bytes sent (encoded ones too): the upstream body is read
 *  whole and checked against its own framing first, then compressed in one piece. A body the upstream cut short is a
 *  502 (no-store) when nothing has been sent yet, else a reset connection, never a shorter "complete" answer: the
 *  tunnel and its proxies end a length-less (chunked) body cleanly when this process dies mid-stream, and Chrome then
 *  keeps the prefix under the payload's one-year immutable Cache-Control (city.lab 2026-11-07: a 385,991-byte prefix
 *  of a 1,042,328-byte GLB held a Windows player on the loading screen). An upstream body with no framing (neither a
 *  length nor chunked: it ends when the connection closes) cannot be told from a cut one, so it is served no-cache. */
export function publicProxy(target, { reduce, headers: forwarded = ['accept', 'origin', 'if-none-match'], cacheBytes = 0,
  retryMs = 0 } = {}) {
  const cache = encodedCache(cacheBytes);
  return (req, res) => {
    const ae = String(req.headers['accept-encoding'] ?? '');
    const wanted = /\bbr\b/.test(ae) ? 'br' : /\bgzip\b/.test(ae) ? 'gzip' : null;
    const key = wanted && !req.headers.range && (req.method === 'GET' || req.method === 'HEAD') ? `${wanted} ${req.url}` : null;
    const hit = key && cache.get(key);
    if (hit) {
      res.writeHead(200, { ...hit.headers, 'content-length': hit.body.length });
      return res.end(req.method === 'HEAD' ? undefined : hit.body);
    }
    const headers = Object.fromEntries(forwarded.filter((h) => req.headers[h] !== undefined).map((h) => [h, req.headers[h]]));
    // `retryMs`: an upstream that refuses, resets or cuts a GET before anything went out is asked again (backoff up
    // to 1 s) for this long, so a tile service's restart is a wait, not an error: a city service restarting during
    // a Windows load turned 299 texture requests into 503s within 6 s, and the client gave ~20 up (verify-all, 08:45).
    const started = Date.now();
    let current = null, gone = false;
    res.on('close', () => { if (!res.writableFinished) { gone = true; current?.destroy(); } }); // the player moved on
    const retry = (n) => {
      if (!retryMs || gone || res.headersSent || req.method !== 'GET' || Date.now() - started >= retryMs) return false;
      setTimeout(() => attempt(n + 1), Math.min(1000, 100 * 2 ** n));
      return true;
    };
    const attempt = (n = 0) => {
    let over = false; // one outcome per attempt: a retry, an answer or a reset
    const failed = () => {
      if (over) return;
      over = true;
      if (retry(n)) return;
      if (res.headersSent) return res.destroy();
      res.writeHead(502, { 'Content-Type': 'application/json', 'Cache-Control': 'no-store', 'Retry-After': '1', ...ISOLATION });
      res.end('{"error":"upstream answer incomplete"}\n');
    };
    const upstream = current = http.request({ host: target.hostname, port: target.port, path: req.url, method: req.method, headers }, (up) => {
      const out = { ...up.headers, ...ISOLATION };
      for (const h of ['access-control-allow-origin', 'connection', 'keep-alive', 'transfer-encoding']) delete out[h]; // hop-by-hop or same-origin
      const declared = up.headers['content-length'] === undefined ? null : Number(up.headers['content-length']);
      if (req.method === 'HEAD' || declared > WHOLE_MAX) {
        // The upstream's own length goes out with it; a cut body resets the connection, so no hop can end it cleanly.
        over = true;
        res.writeHead(up.statusCode ?? 502, out);
        up.on('close', () => { if (!up.complete) res.destroy(); });
        return up.pipe(res);
      }
      const chunks = [];
      let bytes = 0, settled = false;
      up.on('data', (c) => { chunks.push(c); bytes += c.length; });
      up.on('error', () => {});
      up.on('close', () => { if (!settled && !up.complete) { settled = true; failed(); } });
      up.on('end', () => {
        if (settled) return;
        settled = true;
        if (!up.complete || (declared !== null && bytes !== declared)) return failed();
        const body = Buffer.concat(chunks, bytes);
        const framed = declared !== null || /\bchunked\b/i.test(up.headers['transfer-encoding'] ?? '');
        if (!framed) out['cache-control'] = 'no-cache';
        if (reduce && /json/.test(up.headers['content-type'] ?? '')) {
          let reduced;
          try { reduced = Buffer.from(JSON.stringify(reduce(JSON.parse(body.toString('utf8') || 'null'))) + '\n'); }
          catch { reduced = Buffer.from('{}\n'); }
          over = true;
          res.writeHead(up.statusCode ?? 502, { ...out, 'content-length': reduced.length });
          return res.end(reduced);
        }
        const encodable = up.statusCode === 200 && !up.headers['content-encoding'] && !req.headers.range
          && ENCODABLE.test(up.headers['content-type'] ?? '');
        if (!encodable || !wanted) {
          over = true;
          res.writeHead(up.statusCode ?? 502, { ...out, ...(encodable ? { vary: 'Accept-Encoding' } : {}), 'content-length': body.length });
          return res.end(body);
        }
        (wanted === 'br' ? brotliAsync(body) : gzipAsync(body)).then((encoded) => {
          delete out['content-length'];
          delete out.date;
          const sent = { ...out, 'content-encoding': wanted, vary: 'Accept-Encoding' };
          // An immutable payload (revision-named) is the same for every player: keep its encoded bytes, so a repeat
          // costs neither the farm's read nor another compression on the shared host.
          if (key && cache.max && framed && /\bimmutable\b/.test(up.headers['cache-control'] ?? '') && encoded.length <= cache.max / 8) {
            cache.set(key, { headers: sent, body: encoded });
          }
          if (res.destroyed || over) return;
          over = true;
          res.writeHead(200, { ...sent, 'content-length': encoded.length });
          res.end(encoded);
        }, failed);
      });
    });
    upstream.on('error', () => {
      if (over) return;
      over = true;
      if (retry(n)) return;
      if (res.headersSent) return res.destroy();
      res.writeHead(503, { 'Content-Type': 'application/json', 'Retry-After': '5', ...ISOLATION });
      res.end('{"state":"offline"}\n');
    });
    upstream.end();
    };
    attempt();
  };
}

/** A least-recently-used map of encoded answers holding at most `max` body bytes (0 disables it). */
function encodedCache(max) {
  const entries = new Map();
  let bytes = 0;
  return {
    max,
    get(key) {
      const hit = entries.get(key);
      if (hit) { entries.delete(key); entries.set(key, hit); }
      return hit;
    },
    set(key, value) {
      if (!max || entries.has(key)) return;
      entries.set(key, value); bytes += value.body.length;
      for (const [old, entry] of entries) {
        if (bytes <= max) break;
        entries.delete(old); bytes -= entry.body.length;
      }
    },
  };
}

/** Same-origin access to the coarse tile service at `target` (a URL). */
export function coarseProxy(target) {
  return (req, res) => {
    const upstream = http.request({ host: target.hostname, port: target.port, path: req.url, method: req.method, headers: { accept: req.headers.accept ?? '*/*' } }, (up) => {
      res.writeHead(up.statusCode ?? 502, { ...up.headers, ...ISOLATION });
      up.on('close', () => { if (!up.complete) res.destroy(); }); // a cut body stays cut, never a shorter complete answer
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

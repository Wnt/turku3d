#!/usr/bin/env node
// Static server for a dist directory (game build and/or tile dist) with:
//  - COOP/COEP (crossOriginIsolated, SharedArrayBuffer) + CORP + CORS
//  - brotli (precompressed <file>.br if present, else on-the-fly brotli with an in-memory cache) and gzip fallback
//  - HTTP range requests (206) on uncompressed responses, ETag / If-None-Match, correct MIME for .wasm/.glb/.ktx2/.col
//  - optional --mount /tiles=<dir> to serve a tile dist next to the game build
//  - POST /telemetry: play telemetry JSON lines (game/src/telemetry/README.md) appended to
//    <telemetry dir>/session-<YYYYMMDD>.jsonl (local date), default $TURKU3D_WORK/telemetry; 1 MB per request, 204
//  - /coarse/* proxied to the localhost coarse tile service (tools/dev/coarse-tiles, port base + 5), 503 when it is not running
// No dependencies (Node >= 20).
//
// Usage: node tools/dev/serve-dist.mjs [--root <dir>] [--port PORT] [--host 127.0.0.1] [--mount /tiles=$TURKU3D_WORK/dist/tiles] [--no-compress]
//          [--telemetry-dir <dir>] [--coarse http://127.0.0.1:PORT]
import http from 'node:http';
import { workRoot, port } from './environment.mjs';
import fs from 'node:fs';
import fsp from 'node:fs/promises';
import path from 'node:path';
import zlib from 'node:zlib';
import os from 'node:os';

const args = process.argv.slice(2);
const opt = (name, def) => { const i = args.indexOf(name); return i >= 0 ? args[i + 1] : def; };
const expand = (p) => path.resolve(p.replace(/^~(?=$|\/)/, os.homedir()));
const ROOT = expand(opt('--root', args.find((a, i) => !a.startsWith('--') && (i === 0 || !args[i - 1].startsWith('--'))) ?? path.join(workRoot, 'dist')));
const PORT = Number(opt('--port', port(1)));
const HOST = opt('--host', '127.0.0.1');
const COMPRESS = !args.includes('--no-compress');
const TELEMETRY_DIR = expand(opt('--telemetry-dir', path.join(workRoot, 'telemetry')));
const TELEMETRY_MAX_BYTES = 1024 * 1024;
const COARSE = new URL(opt('--coarse', `http://127.0.0.1:${port(5)}`));
const MOUNTS = []; // [prefix, dir]
args.forEach((a, i) => { if (a === '--mount') { const [pre, dir] = args[i + 1].split('='); MOUNTS.push([pre.replace(/\/$/, ''), expand(dir)]); } });
MOUNTS.sort((a, b) => b[0].length - a[0].length);

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

function resolvePath(urlPath) {
  let p = decodeURIComponent(urlPath.split('?')[0]);
  let base = ROOT;
  for (const [pre, dir] of MOUNTS) if (p === pre || p.startsWith(pre + '/')) { base = dir; p = p.slice(pre.length) || '/'; break; }
  const full = path.join(base, path.normalize(p));
  if (full !== base && !full.startsWith(base + path.sep)) return null; // traversal
  return full;
}

function baseHeaders(ext, stat) {
  return {
    'Cross-Origin-Opener-Policy': 'same-origin',
    'Cross-Origin-Embedder-Policy': 'require-corp',
    'Cross-Origin-Resource-Policy': 'cross-origin',
    'Access-Control-Allow-Origin': '*',
    'Access-Control-Expose-Headers': 'Content-Length, Content-Range, Content-Encoding, ETag',
    'Accept-Ranges': 'bytes',
    'Content-Type': MIME[ext] ?? 'application/octet-stream',
    'Last-Modified': stat.mtime.toUTCString(),
    ETag: `"${stat.size.toString(16)}-${Math.floor(stat.mtimeMs).toString(16)}"`,
    'Cache-Control': ext === '.html' || ext === '.json' ? 'no-cache' : 'public, max-age=60',
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

const pad = (n) => String(n).padStart(2, '0');
let telemetryWrites = Promise.resolve();

/** Appends the request's JSON lines to today's session file; every non-empty line must be a JSON object. */
async function telemetry(req, res) {
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
    const file = path.join(TELEMETRY_DIR, `session-${d.getFullYear()}${pad(d.getMonth() + 1)}${pad(d.getDate())}.jsonl`);
    const write = telemetryWrites.then(async () => {
      await fsp.mkdir(TELEMETRY_DIR, { recursive: true });
      await fsp.appendFile(file, lines.join('\n') + '\n');
    });
    telemetryWrites = write.catch(() => {});
    await write;
  }
  res.writeHead(204, cors);
  res.end();
}

// Same-origin access to the coarse tile service; the game treats 503 as "no coarse tiles".
function proxyCoarse(req, res) {
  const isolation = { 'Cross-Origin-Opener-Policy': 'same-origin', 'Cross-Origin-Embedder-Policy': 'require-corp', 'Cross-Origin-Resource-Policy': 'cross-origin' };
  const upstream = http.request({ host: COARSE.hostname, port: COARSE.port, path: req.url, method: req.method, headers: { accept: req.headers.accept ?? '*/*' } }, (up) => {
    res.writeHead(up.statusCode ?? 502, { ...up.headers, ...isolation });
    up.pipe(res);
  });
  upstream.on('error', () => {
    if (res.headersSent) return res.destroy();
    res.writeHead(503, { 'Content-Type': 'application/json', ...isolation });
    res.end('{"state":"offline"}\n');
  });
  upstream.end();
}

async function handler(req, res) {
  if (req.method === 'OPTIONS') {
    res.writeHead(204, { 'Access-Control-Allow-Origin': '*', 'Access-Control-Allow-Headers': 'Range, If-None-Match, Content-Type', 'Access-Control-Allow-Methods': 'GET, HEAD, POST, OPTIONS' });
    return res.end();
  }
  if (req.method === 'POST' && req.url.split('?')[0] === '/telemetry') return telemetry(req, res);
  if (req.method !== 'GET' && req.method !== 'HEAD') { res.writeHead(405); return res.end(); }
  if (req.url === '/coarse' || req.url.startsWith('/coarse/')) return proxyCoarse(req, res);
  let file = resolvePath(req.url);
  if (!file) { res.writeHead(403); return res.end('forbidden'); }
  let stat;
  try { stat = await fsp.stat(file); if (stat.isDirectory()) { file = path.join(file, 'index.html'); stat = await fsp.stat(file); } }
  catch { res.writeHead(404, { 'Content-Type': 'text/plain', 'Cross-Origin-Resource-Policy': 'cross-origin' }); return res.end('not found'); }
  const ext = path.extname(file).toLowerCase();
  const h = baseHeaders(ext, stat);
  if (req.headers['if-none-match'] === h.ETag) { res.writeHead(304, h); return res.end(); }
  const range = req.headers.range;
  const ae = String(req.headers['accept-encoding'] ?? '');

  // Range requests are served on the identity (uncompressed) representation.
  if (range) {
    const m = /^bytes=(\d*)-(\d*)$/.exec(range);
    if (!m || (m[1] === '' && m[2] === '')) { res.writeHead(416, { ...h, 'Content-Range': `bytes */${stat.size}` }); return res.end(); }
    let start = m[1] === '' ? Math.max(0, stat.size - Number(m[2])) : Number(m[1]);
    let end = m[1] === '' ? stat.size - 1 : m[2] === '' ? stat.size - 1 : Math.min(Number(m[2]), stat.size - 1);
    if (start > end || start >= stat.size) { res.writeHead(416, { ...h, 'Content-Range': `bytes */${stat.size}` }); return res.end(); }
    res.writeHead(206, { ...h, 'Content-Range': `bytes ${start}-${end}/${stat.size}`, 'Content-Length': end - start + 1 });
    if (req.method === 'HEAD') return res.end();
    return fs.createReadStream(file, { start, end }).pipe(res);
  }
  if (COMPRESS && COMPRESSIBLE.has(ext) && /\bbr\b/.test(ae)) {
    let body;
    try { body = await fsp.readFile(file + '.br'); } catch { body = await brotli(file, stat); }
    res.writeHead(200, { ...h, 'Content-Encoding': 'br', 'Content-Length': body.length });
    return res.end(req.method === 'HEAD' ? undefined : body);
  }
  if (COMPRESS && COMPRESSIBLE.has(ext) && /\bgzip\b/.test(ae)) {
    res.writeHead(200, { ...h, 'Content-Encoding': 'gzip' });
    if (req.method === 'HEAD') return res.end();
    return fs.createReadStream(file).pipe(zlib.createGzip({ level: 6 })).pipe(res);
  }
  res.writeHead(200, { ...h, 'Content-Length': stat.size });
  if (req.method === 'HEAD') return res.end();
  fs.createReadStream(file).pipe(res);
}

if (import.meta.url === `file://${process.argv[1]}` || process.argv[1]?.endsWith('serve-dist.mjs')) {
  if (!fs.existsSync(ROOT)) { console.error(`root ${ROOT} does not exist`); process.exit(2); }
  const server = http.createServer((req, res) => handler(req, res).catch((e) => { console.error(e); if (!res.headersSent) res.writeHead(500); res.end(); }));
  server.listen(PORT, HOST, () => {
    console.log(`serve-dist: http://${HOST}:${server.address().port}/ -> ${ROOT}${MOUNTS.map(([p, d]) => `\n  ${p}/ -> ${d}`).join('')}`);
    console.log(`  COOP/COEP on, brotli ${COMPRESS ? 'on' : 'off'}, ranges on, POST /telemetry -> ${TELEMETRY_DIR}`);
  });
}

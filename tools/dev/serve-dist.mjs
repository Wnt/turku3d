#!/usr/bin/env node
// Static server for a dist directory (game build and/or tile dist) with:
//  - COOP/COEP (crossOriginIsolated, SharedArrayBuffer) + CORP + CORS
//  - brotli (precompressed <file>.br if present, else on-the-fly brotli with an in-memory cache) and gzip fallback
//  - HTTP range requests (206) on uncompressed responses, ETag / If-None-Match, correct MIME for .wasm/.glb/.ktx2/.col
//  - optional --mount /tiles=<dir> to serve a tile dist next to the game build
// No dependencies (Node >= 20).
//
// Usage: node tools/dev/serve-dist.mjs [--root <dir>] [--port 8080] [--host 127.0.0.1] [--mount /tiles=~/turku3d-data/dist/tiles] [--no-compress]
import http from 'node:http';
import fs from 'node:fs';
import fsp from 'node:fs/promises';
import path from 'node:path';
import zlib from 'node:zlib';
import os from 'node:os';

const args = process.argv.slice(2);
const opt = (name, def) => { const i = args.indexOf(name); return i >= 0 ? args[i + 1] : def; };
const expand = (p) => path.resolve(p.replace(/^~(?=$|\/)/, os.homedir()));
const ROOT = expand(opt('--root', args.find((a, i) => !a.startsWith('--') && (i === 0 || !args[i - 1].startsWith('--'))) ?? 'dist'));
const PORT = Number(opt('--port', 8080));
const HOST = opt('--host', '127.0.0.1');
const COMPRESS = !args.includes('--no-compress');
const MOUNTS = []; // [prefix, dir]
args.forEach((a, i) => { if (a === '--mount') { const [pre, dir] = args[i + 1].split('='); MOUNTS.push([pre.replace(/\/$/, ''), expand(dir)]); } });
MOUNTS.sort((a, b) => b[0].length - a[0].length);

export const MIME = {
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

export async function handler(req, res) {
  if (req.method === 'OPTIONS') {
    res.writeHead(204, { 'Access-Control-Allow-Origin': '*', 'Access-Control-Allow-Headers': 'Range, If-None-Match', 'Access-Control-Allow-Methods': 'GET, HEAD, OPTIONS' });
    return res.end();
  }
  if (req.method !== 'GET' && req.method !== 'HEAD') { res.writeHead(405); return res.end(); }
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
    console.log(`serve-dist: http://${HOST}:${PORT}/ -> ${ROOT}${MOUNTS.map(([p, d]) => `\n  ${p}/ -> ${d}`).join('')}`);
    console.log(`  COOP/COEP on, brotli ${COMPRESS ? 'on' : 'off'}, ranges on`);
  });
}

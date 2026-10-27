#!/usr/bin/env node
// Static server for a dist directory (game build and/or tile dist); the handlers are in http.mjs:
//  - COOP/COEP (crossOriginIsolated, SharedArrayBuffer) + CORP + CORS, brotli/gzip, byte ranges, ETag / 304
//  - optional --mount /tiles=<dir> to serve a tile dist next to the game build
//  - POST /telemetry appended to <telemetry dir>/session-<YYYYMMDD>.jsonl, default $TURKU3D_WORK/telemetry
//  - /coarse/* proxied to the localhost coarse tile service (tools/dev/coarse-tiles, port base + 5)
// No dependencies (Node >= 20).
//
// Usage: node tools/dev/serve-dist.mjs [--root <dir>] [--port PORT] [--host 127.0.0.1] [--mount /tiles=$TURKU3D_WORK/dist/tiles] [--no-compress]
//          [--telemetry-dir <dir>] [--coarse http://127.0.0.1:PORT]
import http from 'node:http';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import { workRoot, port } from './environment.mjs';
import { coarseProxy, resolveUnder, serveFile, telemetryHandler } from './http.mjs';

const args = process.argv.slice(2);
const opt = (name, def) => { const i = args.indexOf(name); return i >= 0 ? args[i + 1] : def; };
const expand = (p) => path.resolve(p.replace(/^~(?=$|\/)/, os.homedir()));
const ROOT = expand(opt('--root', args.find((a, i) => !a.startsWith('--') && (i === 0 || !args[i - 1].startsWith('--'))) ?? path.join(workRoot, 'dist')));
const PORT = Number(opt('--port', port(1)));
const HOST = opt('--host', '127.0.0.1');
const COMPRESS = !args.includes('--no-compress');
const TELEMETRY_DIR = expand(opt('--telemetry-dir', path.join(workRoot, 'telemetry')));
const MOUNTS = []; // [prefix, dir]
args.forEach((a, i) => { if (a === '--mount') { const [pre, dir] = args[i + 1].split('='); MOUNTS.push([pre.replace(/\/$/, ''), expand(dir)]); } });
MOUNTS.sort((a, b) => b[0].length - a[0].length);
const telemetry = telemetryHandler(TELEMETRY_DIR);
const coarse = coarseProxy(new URL(opt('--coarse', `http://127.0.0.1:${port(5)}`)));

function resolvePath(urlPath) {
  let p = urlPath.split('?')[0];
  let base = ROOT;
  for (const [pre, dir] of MOUNTS) if (p === pre || p.startsWith(pre + '/')) { base = dir; p = p.slice(pre.length) || '/'; break; }
  return resolveUnder(base, p);
}

async function handler(req, res) {
  if (req.method === 'OPTIONS') {
    res.writeHead(204, { 'Access-Control-Allow-Origin': '*', 'Access-Control-Allow-Headers': 'Range, If-None-Match, Content-Type', 'Access-Control-Allow-Methods': 'GET, HEAD, POST, OPTIONS' });
    return res.end();
  }
  if (req.method === 'POST' && req.url.split('?')[0] === '/telemetry') return telemetry(req, res);
  if (req.method !== 'GET' && req.method !== 'HEAD') { res.writeHead(405); return res.end(); }
  if (req.url === '/coarse' || req.url.startsWith('/coarse/')) return coarse(req, res);
  const file = resolvePath(req.url);
  if (!file) { res.writeHead(403); return res.end('forbidden'); }
  return serveFile(req, res, file, { compress: COMPRESS });
}

if (!fs.existsSync(ROOT)) { console.error(`root ${ROOT} does not exist`); process.exit(2); }
const server = http.createServer((req, res) => handler(req, res).catch((e) => { console.error(e); if (!res.headersSent) res.writeHead(500); res.end(); }));
server.listen(PORT, HOST, () => {
  console.log(`serve-dist: http://${HOST}:${server.address().port}/ -> ${ROOT}${MOUNTS.map(([p, d]) => `\n  ${p}/ -> ${d}`).join('')}`);
  console.log(`  COOP/COEP on, brotli ${COMPRESS ? 'on' : 'off'}, ranges on, POST /telemetry -> ${TELEMETRY_DIR}`);
});

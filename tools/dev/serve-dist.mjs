#!/usr/bin/env node
// Static server for a dist directory (game build and/or tile dist); the handlers are in http.mjs:
//  - COOP/COEP (crossOriginIsolated, SharedArrayBuffer) + CORP + CORS, brotli/gzip, byte ranges, ETag / 304
//  - optional --mount /tiles=<dir> to serve a tile dist next to the game build
//  - POST /telemetry appended to <telemetry dir>/session-<YYYYMMDD>.jsonl, default $TURKU3D_WORK/telemetry
//  - /coarse/* proxied to the localhost coarse tile service (tools/dev/coarse-tiles, port base + 5)
//  - GET /__localhost/status answers { service: false }: a dist server is never the localhost game service
//    (tools/dev/localhost/vitePlugin.mjs gives the same answer without its service), so pages never log a 404
//  - GET /facades/candidates/: every reviewed generated facade candidate (facadeCandidates.mjs), from --candidates
//    (default $TURKU3D_DATA/facade-approvals/candidates)
//  - --public: a public share (tools/share/deploy.sh) answers only GET/HEAD for the game, its mounts and the tile routes
//    the game uses; no capture, telemetry, facade candidates, dotfiles or service internals (a status answers
//    {state, version}); /coarse payloads are compressed for the tunnel
//  - --edits <dir>: the object editor's edit sessions under /__edits/ (editSessions.mjs). With --public they are
//    write-only (PUT/POST, same origin, 512 MB quota, --edits-quota-mb), for `?editor=1` on the share host
//  - --cities <file>: another city's coarse tile service at /cities/<name>/coarse/* (game/src/core/city.ts), the
//    upstream origin per city from a JSON file {"espoo": "http://127.0.0.1:7970"} read again when it changes; a city
//    missing there answers {"state":"offline"} (the game shows its portal as "coming soon")
//  - --mp http://127.0.0.1:PORT: WebSocket upgrades and GET /live.json, /scores/* go to that multiplayer server, so
//    a share host's game plays on its own origin (game/src/net/config.ts)
// No dependencies (Node >= 20).
//
// Usage: node tools/dev/serve-dist.mjs [--root <dir>] [--port PORT] [--host 127.0.0.1] [--mount /tiles=$TURKU3D_WORK/dist/tiles] [--no-compress]
//          [--telemetry-dir <dir>] [--coarse http://127.0.0.1:PORT] [--candidates <dir>] [--public] [--mp http://127.0.0.1:PORT]
//          [--edits <dir> [--edits-quota-mb N]] [--payload-cache-mb N]
import http from 'node:http';
import net from 'node:net';
import { captureMiddleware } from './capture.mjs';
import { editSessionsMiddleware } from './editSessions.mjs';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import { workRoot, port } from './environment.mjs';
import { candidatesRoot, facadeCandidates, isFacadeCandidates } from './facadeCandidates.mjs';
import { coarseProxy, publicProxy, resolveUnder, serveFile, telemetryHandler } from './http.mjs';
import { listen } from './systemdListen.mjs';

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
const COARSE = new URL(opt('--coarse', `http://127.0.0.1:${port(5)}`));
const coarse = coarseProxy(COARSE);
const candidates = facadeCandidates({ root: expand(opt('--candidates', candidatesRoot)) });
const PUBLIC = args.includes('--public');
const MP = opt('--mp') ? new URL(opt('--mp')) : null;
// Plain tile, payload and scenery paths only: no dot segments or escapes reach the tile proxy's URL normalisation.
const PUBLIC_COARSE = /^\/coarse(?:\/[A-Za-z0-9_,=+-][A-Za-z0-9_.,=+-]*)*\/?$/;
const STATUS = /^\/coarse\/(?:_v\/[a-f0-9]{40,64}\/)?(?:status|health|want|demand)$/;
// The game reads only these of a service status (world/coarseTiles.ts); queues, failures and generations stay inside.
const publicStatus = publicProxy(COARSE, { reduce: (s) => ({ state: s?.state ?? 'offline', ...(typeof s?.version === 'number' ? { version: s.version } : {}) }) });
// Encoded revision-named tile payloads for every player (a city's hot set is a few hundred MB compressed).
const publicCoarse = publicProxy(COARSE, { cacheBytes: Number(opt('--payload-cache-mb', 1024)) * 1024 * 1024 });
const mp = MP && publicProxy(MP);
// Other cities' tile services (--cities FILE): one status and one payload proxy per upstream, the file re-read on change.
const CITIES_FILE = opt('--cities') ? expand(opt('--cities')) : null;
const CITY_TILES = /^\/cities\/([a-z]{2,24})(\/coarse(?:\/.*)?)$/;
let cityMap = { mtime: -1, upstreams: {} };
const cityProxies = new Map();
// Re-read in the background at most every second: a synchronous stat per request stalled this whole event loop
// whenever the loaded host's disk did (osgallery-dev, 2026-11-07).
let citiesCheckedAt = 0, citiesReading = null;
function refreshCities() {
  citiesCheckedAt = Date.now();
  citiesReading ??= fs.promises.stat(CITIES_FILE).then(async (st) => {
    if (st.mtimeMs !== cityMap.mtime) cityMap = { mtime: st.mtimeMs, upstreams: JSON.parse(await fs.promises.readFile(CITIES_FILE, 'utf8')) ?? {} };
  }).catch(() => { cityMap = { mtime: -1, upstreams: {} }; }).finally(() => { citiesReading = null; });
}
if (CITIES_FILE) {
  try { cityMap = { mtime: fs.statSync(CITIES_FILE).mtimeMs, upstreams: JSON.parse(fs.readFileSync(CITIES_FILE, 'utf8')) ?? {} }; }
  catch { cityMap = { mtime: -1, upstreams: {} }; }
  citiesCheckedAt = Date.now();
}
function cityUpstream(name) {
  if (!CITIES_FILE) return null;
  if (Date.now() - citiesCheckedAt > 1000) refreshCities();
  const origin = typeof cityMap.upstreams[name] === 'string' ? cityMap.upstreams[name] : null;
  if (!origin) return null;
  if (!cityProxies.has(origin)) {
    const target = new URL(origin);
    cityProxies.set(origin, { status: publicProxy(target, { reduce: (s) => ({ state: s?.state ?? 'offline', ...(typeof s?.version === 'number' ? { version: s.version } : {}) }) }),
      payload: publicProxy(target, { cacheBytes: 256 * 1024 * 1024 }), local: coarseProxy(target) });
  }
  return cityProxies.get(origin);
}
/** /cities/<name>/coarse/... -> that city's service as /coarse/...; null when the path is no city tile path. */
function cityTiles(req, res, publicOnly) {
  const [pathname, query] = req.url.split(/\?(.*)/s);
  const m = CITY_TILES.exec(pathname);
  if (!m) return null;
  const tilePath = m[2];
  if (!PUBLIC_COARSE.test(tilePath)) { res.writeHead(404); return res.end(); }
  const proxy = cityUpstream(m[1]);
  if (!proxy) {
    res.writeHead(404, { 'Content-Type': 'application/json', 'Cache-Control': 'no-store' });
    return res.end('{"state":"offline"}\n');
  }
  req.url = query ? `${tilePath}?${query}` : tilePath;
  return (publicOnly ? STATUS.test(tilePath) ? proxy.status : proxy.payload : proxy.local)(req, res);
}
const EDITS = opt('--edits') ? expand(opt('--edits')) : null;
const edits = EDITS && editSessionsMiddleware(EDITS, PUBLIC
  ? { writeOnly: true, quotaBytes: Number(opt('--edits-quota-mb', 512)) * 1024 * 1024 } : {});
const noEdits = (_req, res) => { res.writeHead(404); res.end(); };

function resolvePath(urlPath) {
  let p = urlPath.split('?')[0];
  let base = ROOT;
  for (const [pre, dir] of MOUNTS) if (p === pre || p.startsWith(pre + '/')) { base = dir; p = p.slice(pre.length) || '/'; break; }
  return resolveUnder(base, p);
}

const capture = captureMiddleware();
async function publicHandler(req, res) {
  const pathname = req.url.split('?')[0];
  if (edits && pathname.startsWith('/__edits/')) return edits(req, res, () => noEdits(req, res));
  if (req.method !== 'GET' && req.method !== 'HEAD') { res.writeHead(405, { Allow: 'GET, HEAD' }); return res.end(); }
  if (mp && (pathname === '/live.json' || pathname.startsWith('/scores/'))) return mp(req, res);
  if (pathname.startsWith('/cities/') && cityTiles(req, res, true) !== null) return;
  if (pathname === '/coarse' || pathname.startsWith('/coarse/')) {
    if (!PUBLIC_COARSE.test(pathname)) { res.writeHead(404); return res.end(); }
    return (STATUS.test(pathname) ? publicStatus : publicCoarse)(req, res);
  }
  if (pathname.split('/').some((part) => part.startsWith('.'))) { res.writeHead(404); return res.end('not found'); }
  const file = resolvePath(req.url);
  if (!file) { res.writeHead(403); return res.end('forbidden'); }
  return serveFile(req, res, file, { compress: COMPRESS });
}

async function handler(req, res) {
  res.setHeader('Document-Policy', 'js-profiling');
  if (PUBLIC) return publicHandler(req, res);
  if (req.url.startsWith('/__capture/')) return capture(req, res, () => { res.writeHead(404); res.end(); });
  if (edits && req.url.startsWith('/__edits/')) return edits(req, res, () => noEdits(req, res));
  if (req.method === 'OPTIONS') {
    res.writeHead(204, { 'Access-Control-Allow-Origin': '*', 'Access-Control-Allow-Headers': 'Range, If-None-Match, Content-Type', 'Access-Control-Allow-Methods': 'GET, HEAD, POST, OPTIONS' });
    return res.end();
  }
  if (req.method === 'POST' && req.url.split('?')[0] === '/telemetry') return telemetry(req, res);
  if (req.method !== 'GET' && req.method !== 'HEAD') { res.writeHead(405); return res.end(); }
  if (req.url === '/coarse' || req.url.startsWith('/coarse/')) return coarse(req, res);
  if (req.url.startsWith('/cities/') && cityTiles(req, res, false) !== null) return;
  if (req.url.split('?')[0] === '/__localhost/status') {
    res.writeHead(200, { 'Content-Type': 'application/json', 'Cache-Control': 'no-store' });
    return res.end('{"service":false}\n');
  }
  if (isFacadeCandidates(req.url)) return candidates(req, res);
  const file = resolvePath(req.url);
  if (!file) { res.writeHead(403); return res.end('forbidden'); }
  return serveFile(req, res, file, { compress: COMPRESS });
}

if (!fs.existsSync(ROOT)) { console.error(`root ${ROOT} does not exist`); process.exit(2); }
const server = http.createServer((req, res) => handler(req, res).catch((e) => { console.error(e); if (!res.headersSent) res.writeHead(500); res.end(); }));
// The multiplayer socket: the raw upgrade request goes to the server, which checks its Origin and path itself.
const upgraded = new Set(); // multiplayer pipes: a restart ends them at once (clients reconnect) instead of holding its drain
if (MP) server.on('upgrade', (req, socket, head) => {
  upgraded.add(socket);
  socket.on('close', () => upgraded.delete(socket));
  const upstream = net.connect(Number(MP.port), MP.hostname, () => {
    const lines = [`${req.method} ${req.url} HTTP/${req.httpVersion}`];
    for (let i = 0; i < req.rawHeaders.length; i += 2) lines.push(`${req.rawHeaders[i]}: ${req.rawHeaders[i + 1]}`);
    upstream.write(`${lines.join('\r\n')}\r\n\r\n`);
    if (head?.length) upstream.write(head);
    socket.pipe(upstream).pipe(socket);
  });
  upstream.setNoDelay(true); socket.setNoDelay(true);
  upstream.on('error', () => socket.destroy());
  socket.on('error', () => upstream.destroy());
  socket.on('close', () => upstream.destroy());
  upstream.on('close', () => socket.destroy());
});
// Under systemd the unit's socket is inherited (connections wait across a restart); SIGTERM drains (systemdListen.mjs).
listen(server, { port: PORT, host: HOST, onStop: () => upgraded.forEach((s) => s.destroy()), onListening: (inherited) => {
  const a = server.address();
  console.log(`serve-dist: http://${a.address}:${a.port}/ -> ${ROOT}${MOUNTS.map(([p, d]) => `\n  ${p}/ -> ${d}`).join('')}${inherited ? ' (systemd socket)' : ''}`);
  console.log(PUBLIC ? `  public: GET/HEAD only, /coarse -> ${COARSE.origin}${MP ? `, multiplayer -> ${MP.origin}` : ''}`
    : `  COOP/COEP on, brotli ${COMPRESS ? 'on' : 'off'}, ranges on, POST /telemetry -> ${TELEMETRY_DIR}`);
} });

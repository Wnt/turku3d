#!/usr/bin/env node
// Read-only local integration tiles. Earlier content-addressed assets survive manifest replacement.
import http from 'node:http';
import fs from 'node:fs';
import path from 'node:path';
import { pathToFileURL } from 'node:url';

const TILE = /^(t0_-?\d+_-?\d+)\/(.+)$/;
const HASHED = /(?:^|[./_-])[a-f0-9]{64}(?:[./_-]|$)/;
const PUBLIC = ['coarse', 'tile', 'visual', 'collision', 'roads', 'pass_kind', 'vehicles', 'lights', 'vehicle_models', 'stale', 'generation'];
const types = { '.json': 'application/json', '.ktx2': 'image/ktx2', '.glb': 'model/gltf-binary', '.png': 'image/png', '.jpg': 'image/jpeg', '.webp': 'image/webp' };
const isFile = file => { try { return fs.statSync(file).isFile(); } catch { return false; } };
const inside = (root, rel) => { const file = path.resolve(root, rel); return file.startsWith(root + path.sep) ? file : null; };

export function tileFiles(inputRoots) {
  const roots = inputRoots.map(root => path.resolve(root));
  const tileRoot = id => roots.find(root => isFile(path.join(root, id, 'manifest.json'))) ?? null;
  const find = rel => {
    if (path.isAbsolute(rel) || rel.split(/[\\/]/).includes('..')) return null;
    const tile = TILE.exec(rel);
    // Mutable tile files belong to the current manifest. Hashed assets may come from any retained generation.
    const candidates = tile && !HASHED.test(rel) ? [tileRoot(tile[1])].filter(Boolean) : roots;
    for (const root of candidates) {
      const file = inside(root, rel);
      if (file && isFile(file)) return file;
    }
    return null;
  };
  return { roots, tileRoot, find };
}

export function createTileServer(inputRoots) {
  const { roots, tileRoot, find } = tileFiles(inputRoots);
  const counts = { manifest: 0, file: 0, missing: 0 };
  const json = (res, status, body) => {
    res.writeHead(status, { 'Content-Type': 'application/json', 'Cache-Control': 'no-cache', 'Access-Control-Allow-Origin': '*' });
    res.end(JSON.stringify(body));
  };
  return http.createServer((req, res) => {
    let rel;
    try { rel = decodeURIComponent(new URL(req.url, 'http://local').pathname.replace(/^\/coarse\//, '').replace(/^\//, '')); }
    catch { return json(res, 400, { error: 'invalid path' }); }
    if (rel === 'status' || rel === 'health') return json(res, 200, { state: 'ready', served: counts, roots });
    if (rel === 'want') return json(res, 200, { version: 1, queued: 0, building: [] });
    if (rel === 'demand') return json(res, 200, { version: 1 });
    const m = /^(t0_-?\d+_-?\d+)\/(collision|manifest)\.json$/.exec(rel);
    if (m) {
      const root = tileRoot(m[1]);
      // Unbuilt lookahead tiles are absent, not a service outage that pauses all nearby streaming.
      if (!root) { counts.missing++; return json(res, 404, { error: 'not built locally' }); }
      counts.manifest++;
      const manifest = JSON.parse(fs.readFileSync(path.join(root, m[1], 'manifest.json'), 'utf8'));
      return json(res, 200, Object.fromEntries(PUBLIC.filter(k => k in manifest).map(k => [k, manifest[k]])));
    }
    const file = find(rel);
    if (!file) return json(res, 404, { error: 'not found' });
    counts.file++;
    res.writeHead(200, { 'Content-Type': types[path.extname(file)] ?? 'application/octet-stream',
      'Content-Length': fs.statSync(file).size, 'Access-Control-Allow-Origin': '*',
      'Cache-Control': HASHED.test(rel) ? 'public, max-age=31536000, immutable' : 'no-cache' });
    if (req.method === 'HEAD') return res.end();
    fs.createReadStream(file).on('error', () => res.destroy()).pipe(res);
  });
}

if (process.argv[1] && import.meta.url === pathToFileURL(path.resolve(process.argv[1])).href) {
  const [host, port, rootList] = process.argv.slice(2);
  if (!host || !port || !rootList) throw new Error('Usage: loop-tile-server.mjs HOST PORT ROOT[:ROOT...]');
  createTileServer(rootList.split(':')).listen(Number(port), host, () => console.log(`integration tiles on ${host}:${port}`));
}

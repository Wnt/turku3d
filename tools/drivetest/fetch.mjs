// Read-only cache of the pinned tile version for the drive sweep: which tiles are finished (ready.json HEAD probes,
// never a build request), their manifests, roads.json and collision cells. Files land under DIR/cache and are
// reused across shards and resumed runs (payload names carry their sha256).
//
//   node tools/drivetest/fetch.mjs DIR [TILE_SERVICE_JSON] [--tiles i0,j0,i1,j1]
import fs from 'node:fs';
import path from 'node:path';

export function farm(configFile = path.join(process.env.HOME, 'turku3d/loops/integrator/tile-service.json')) {
  const config = JSON.parse(fs.readFileSync(configFile, 'utf8'));
  return { root: `${config.service}/v/${encodeURIComponent(config.version)}/coarse/`, service: config.service, version: config.version };
}

async function get(url, kind = 'json', attempts = 4) {
  for (let a = 0; ; a++) {
    try {
      const r = await fetch(url, { signal: AbortSignal.timeout(30000) });
      if (r.status === 404) return null;
      if (!r.ok) throw new Error(`HTTP ${r.status}`);
      return kind === 'json' ? await r.json() : Buffer.from(await r.arrayBuffer());
    } catch (e) {
      if (a + 1 >= attempts) throw new Error(`${url}: ${e.message}`);
      await new Promise(r => setTimeout(r, 500 * (a + 1)));
    }
  }
}

async function pool(items, n, fn) {
  const out = new Array(items.length); let next = 0;
  await Promise.all(Array.from({ length: n }, async () => { while (next < items.length) { const i = next++; out[i] = await fn(items[i], i); } }));
  return out;
}

export class TileCache {
  constructor(dir, source = farm()) { this.dir = path.join(dir, 'cache'); this.source = source; fs.mkdirSync(this.dir, { recursive: true }); }
  file(...p) { const f = path.join(this.dir, ...p); fs.mkdirSync(path.dirname(f), { recursive: true }); return f; }
  async ready(tile) {
    const r = await fetch(new URL(`${tile}/ready.json`, this.source.root), { method: 'HEAD', signal: AbortSignal.timeout(10000) }).catch(() => null);
    return r?.status === 200;
  }
  async manifest(tile) {
    const f = this.file('manifest', `${tile}.json`);
    if (fs.existsSync(f)) return JSON.parse(fs.readFileSync(f, 'utf8'));
    if (!(await this.ready(tile))) return null;
    const m = await get(new URL(`${tile}/manifest.json`, this.source.root));
    if (m) fs.writeFileSync(f, JSON.stringify({ tile, roads: m.roads ?? null, collision: m.collision ?? {}, revision: m.revision ?? null }));
    return m && JSON.parse(fs.readFileSync(f, 'utf8'));
  }
  async roads(tile) {
    const m = await this.manifest(tile);
    if (!m?.roads?.url) return null;
    const f = this.file('roads', `${tile}.${m.roads.sha256.slice(0, 16)}.json`);
    if (!fs.existsSync(f)) { const j = await get(new URL(m.roads.url, this.source.service)); if (!j) return null; fs.writeFileSync(f, JSON.stringify(j)); }
    return { tile, json: JSON.parse(fs.readFileSync(f, 'utf8')), hash: m.roads.sha256 };
  }
  /** Collision cell bytes by cell id (c_i_j), from the owning tile's manifest. */
  async cell(id) {
    const [i, j] = id.slice(2).split('_').map(Number), tile = `t0_${Math.floor(i / 4)}_${Math.floor(j / 4)}`;
    const m = await this.manifest(tile);
    const entry = m?.collision?.[id];
    if (!entry) return null;
    const f = this.file('col', `${id}.${entry.sha256.slice(0, 16)}.col`);
    if (!fs.existsSync(f)) { const b = await get(new URL(entry.url, this.source.service), 'bin'); if (!b) return null; fs.writeFileSync(f, b); }
    const b = fs.readFileSync(f);
    return b.buffer.slice(b.byteOffset, b.byteOffset + b.byteLength);
  }
  /** Every finished tile in the box, with its roads cached. */
  async city([i0, j0, i1, j1] = [-14, -14, 17, 13], parallel = 12) {
    const tiles = [];
    for (let i = i0; i <= i1; i++) for (let j = j0; j <= j1; j++) tiles.push(`t0_${i}_${j}`);
    const listFile = path.join(this.dir, `tiles-${i0}_${j0}_${i1}_${j1}.json`);
    if (fs.existsSync(listFile)) return JSON.parse(fs.readFileSync(listFile, 'utf8'));
    const found = (await pool(tiles, parallel, async t => (await this.roads(t)) ? t : null)).filter(Boolean);
    fs.writeFileSync(listFile, JSON.stringify(found));
    return found;
  }
}

if (import.meta.url === `file://${process.argv[1]}`) {
  const [dir, config] = process.argv.slice(2).filter(a => !a.startsWith('--'));
  const box = process.argv.includes('--tiles') ? process.argv[process.argv.indexOf('--tiles') + 1].split(',').map(Number) : undefined;
  const cache = new TileCache(dir, farm(config));
  const t0 = Date.now(), tiles = await cache.city(box);
  console.log(JSON.stringify({ tiles: tiles.length, seconds: (Date.now() - t0) / 1000, version: cache.source.version }));
}

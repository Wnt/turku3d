// Export what city.lab serves for each finished tile into a static tree that OCI's Caddy serves from disk:
//   <out>/coarse/<tile>/manifest.json, collision.json   the fragments as the tile service answers them now
//   <out>/coarse/_v/<revision>/...                      every payload they reference (vis glb, collision, roads,
//                                                       placement and vehicle-model glbs, the ktx2/bin those glbs
//                                                       reference), plus <file>.br when the service sends brotli
//   <out>/coarse/navigation.json                       the city's street graph
//   <out>/coarse/scenery/...                            distant scenery (--scenery LIST of t0/t2/t4 squares): every
//                                                       catalogued square's descriptor, LOD glb and trees, built on
//                                                       the scenery server first when needed (202 is waited out)
//   <out>/coarse/scenery-catalogue.json                 the catalogue rows whose files are all in place, which
//                                                       citylab-api.mjs answers /coarse/scenery?tiles= from
// Payload paths name their revision, so a file already exported is never fetched again; fragments and descriptors are
// rewritten only when they changed. A tile whose fragment is not finished (pending, stale, queued) is skipped.
// Writes <out>/coarse/export-index.json: tile -> revision, files, bytes. This only seeds files: at runtime the
// published host serves them alone (tools/share/oci/Caddyfile).
//
//   node export-oci.mjs --src http://127.0.0.1:7790 --out DIR --tiles tiles.txt [--scenery squares.txt]
//        [--jobs 32] [--frag-jobs 8] [--scenery-wait-s 2400]
import fs from 'node:fs';
import path from 'node:path';
import http from 'node:http';
import https from 'node:https';
import zlib from 'node:zlib';

const arg = (k, d) => { const i = process.argv.indexOf(`--${k}`); return i > 0 ? process.argv[i + 1] : d; };
const SRC = new URL(arg('src', 'http://127.0.0.1:7781'));
const OUT = path.resolve(arg('out', 'oci-export'));
const TILES = fs.readFileSync(arg('tiles'), 'utf8').split(/\s+/).filter(t => /^t0_-?\d+_-?\d+$/.test(t));
const SQUARES = arg('scenery') ? [...new Set(fs.readFileSync(arg('scenery'), 'utf8').split(/\s+/).filter(t => /^t[024]_-?\d+_-?\d+$/.test(t)))] : [];
const SCENERY_WAIT_MS = Number(arg('scenery-wait-s', 2400)) * 1000;
const sleep = ms => new Promise(r => setTimeout(r, ms));
const JOBS = Number(arg('jobs', 8)), FRAG_JOBS = Number(arg('frag-jobs', 3));
const agent = new (SRC.protocol === 'https:' ? https : http).Agent({ keepAlive: true, maxSockets: JOBS + FRAG_JOBS });

function get(p, encoding) {
  return new Promise(resolve => {
    const u = new URL(p, SRC);
    const req = (u.protocol === 'https:' ? https : http).get(u, { agent, timeout: 120_000, headers: { 'accept-encoding': encoding, 'user-agent': 'turku3d-oci-export/1' } }, res => {
      const chunks = [];
      res.on('data', c => chunks.push(c));
      res.on('end', () => resolve({ status: res.statusCode, enc: res.headers['content-encoding'] ?? '', body: Buffer.concat(chunks) }));
      res.on('error', e => resolve({ status: 0, error: e.message }));
    });
    req.on('timeout', () => req.destroy(new Error('timeout')));
    req.on('error', e => resolve({ status: 0, error: e.message }));
  });
}
const decode = r => r.enc === 'br' ? zlib.brotliDecompressSync(r.body) : r.enc === 'gzip' ? zlib.gunzipSync(r.body) : r.body;
const writeAtomic = (file, data) => { fs.mkdirSync(path.dirname(file), { recursive: true }); const tmp = `${file}.tmp${process.pid}`; fs.writeFileSync(tmp, data); fs.renameSync(tmp, file); };
function glbRefs(buf) {
  if (buf.length < 20 || buf.readUInt32LE(0) !== 0x46546c67) return [];
  const len = buf.readUInt32LE(12), j = JSON.parse(buf.subarray(20, 20 + len).toString());
  return [...(j.images ?? []), ...(j.buffers ?? [])].map(o => o.uri).filter(u => u && !u.startsWith('data:'));
}
function limiter(n) {
  let active = 0; const queue = [];
  const next = () => { if (active >= n || !queue.length) return; active++; const [fn, ok, ko] = queue.shift(); fn().then(ok, ko).finally(() => { active--; next(); }); };
  return fn => new Promise((ok, ko) => { queue.push([fn, ok, ko]); next(); });
}
const payloadSlot = limiter(JOBS), fragSlot = limiter(FRAG_JOBS);
const stats = { tiles: 0, skipped: {}, files: 0, fetched: 0, bytes: 0, brFiles: 0, errors: [] };
const seen = new Map();  // payload path -> Promise<bytes on disk | -1>

// One payload (identity + .br when offered); glbs also pull what they reference. Returns bytes on disk or -1.
function payload(p) {
  if (!p.startsWith('/coarse/_v/') && !/^\/coarse\/scenery\/lod\.[0-9a-f]{64}\.glb$/.test(p)) return Promise.resolve(-1);
  if (seen.has(p)) return seen.get(p);
  const job = (async () => {
    const file = path.join(OUT, decodeURIComponent(p));
    let bytes = 0, body;
    if (fs.existsSync(file)) {
      body = p.endsWith('.glb') ? fs.readFileSync(file) : null;
      bytes = fs.statSync(file).size + (fs.existsSync(`${file}.br`) ? fs.statSync(`${file}.br`).size : 0);
    } else {
      const r = await payloadSlot(() => get(p, 'br'));
      if (r.status !== 200) { stats.errors.push(`${p}: ${r.status || r.error}`); return -1; }
      if (r.enc === 'br') {
        body = zlib.brotliDecompressSync(r.body);
        writeAtomic(`${file}.br`, r.body); stats.brFiles++; bytes += r.body.length;
      } else body = decode(r);
      writeAtomic(file, body); bytes += body.length;
      stats.fetched++; stats.bytes += bytes;
    }
    stats.files++;
    if (body && p.endsWith('.glb')) {
      let refs = [];
      try { refs = glbRefs(body); } catch (e) { stats.errors.push(`${p}: glb ${e.message}`); }
      const sub = await Promise.all(refs.map(ref => payload(new URL(ref, new URL(p, 'http://x')).pathname)));
      if (sub.some(b => b < 0)) return -1;
      bytes += sub.reduce((a, b) => a + b, 0);
    }
    return bytes;
  })();
  seen.set(p, job);
  return job;
}

const index = {};
async function exportTile(id) {
  const frags = {};
  for (const name of ['manifest', 'collision']) {
    const r = await fragSlot(() => get(`/coarse/${id}/${name}.json?d=1000`, 'identity'));
    if (r.status === 200) frags[name] = decode(r);
  }
  let f;
  try { f = JSON.parse(frags.manifest); } catch { stats.skipped['no fragment'] = (stats.skipped['no fragment'] ?? 0) + 1; return; }
  if (!f.coarse || !f.visual?.[id]) { stats.skipped['not built'] = (stats.skipped['not built'] ?? 0) + 1; return; }
  if (f.stale || (f.queue_state && f.queue_state !== 'built') || f.pass_kind !== 'full') {
    const why = f.stale ? 'stale' : f.pass_kind !== 'full' ? `pass ${f.pass_kind}` : `queue ${f.queue_state}`;
    stats.skipped[why] = (stats.skipped[why] ?? 0) + 1; return;
  }
  const base = new URL(`/coarse/${id}/`, 'http://x');
  const urls = [f.visual[id].url, f.roads?.url, f.collision_bundle?.url, ...Object.values(f.collision ?? {}).map(c => c.url),
    ...(f.buildingPlacements?.models ?? []).map(m => m.uri), ...Object.values(f.vehicle_models ?? {}).map(m => m.url)]
    .filter(Boolean).map(u => new URL(u, base).pathname);
  const sizes = await Promise.all([...new Set(urls)].map(payload));
  if (sizes.some(b => b < 0)) { stats.skipped['payload missing'] = (stats.skipped['payload missing'] ?? 0) + 1; return; }
  // Fragments last: a fragment on disk always has its payloads in place.
  for (const [name, body] of Object.entries(frags)) {
    const file = path.join(OUT, 'coarse', id, `${name}.json`);
    if (!fs.existsSync(file) || !fs.readFileSync(file).equals(body)) {
      writeAtomic(file, body);
      writeAtomic(`${file}.br`, zlib.brotliCompressSync(body, { params: { [zlib.constants.BROTLI_PARAM_QUALITY]: 9 } }));
    }
  }
  index[id] = { revision: f.revision, files: urls.length, bytes: sizes.reduce((a, b) => a + b, 0) };
  stats.tiles++;
}

// A small JSON the game polls (no-cache): rewritten (with a .br sidecar) only when it changed.
function writeJson(rel, body) {
  const file = path.join(OUT, rel);
  if (fs.existsSync(file) && fs.readFileSync(file).equals(body)) return;
  writeAtomic(file, body);
  writeAtomic(`${file}.br`, zlib.brotliCompressSync(body, { params: { [zlib.constants.BROTLI_PARAM_QUALITY]: 9 } }));
}

// GET a scenery JSON, waiting out 202 (queued / preparing on the scenery server) until the deadline.
async function settled(p, deadline) {
  for (let attempt = 0; ; attempt++) {
    const r = await payloadSlot(() => get(p, 'identity'));
    if (r.status === 200) return decode(r);
    if (r.status !== 202 && !(r.status >= 500) && r.status !== 0) return null;
    if (Date.now() > deadline) return null;
    await sleep(Math.min(15_000, 1000 * 2 ** Math.min(attempt, 4)));
  }
}

const scenery = { squares: SQUARES.length, rows: 0, published: 0, unbuilt: 0 };
async function exportScenery() {
  const deadline = Date.now() + SCENERY_WAIT_MS, rows = new Map(); let recipe, generation;
  for (let i = 0; i < SQUARES.length; i += 256) {
    const batch = SQUARES.slice(i, i + 256);
    const r = await fragSlot(() => get(`/coarse/scenery?tiles=${batch.join(',')}`, 'identity'));
    if (r.status !== 200) { stats.errors.push(`scenery catalogue ${i}: ${r.status || r.error}`); continue; }
    const c = JSON.parse(decode(r));
    recipe ??= c.recipe; generation ??= c.generation;
    for (const row of c.tiles) rows.set(row.tile, row);
  }
  scenery.rows = rows.size;
  const published = {};
  await Promise.all([...rows.values()].map(async row => {
    const body = await settled(`/coarse/scenery/${row.key}.json`, deadline);
    if (!body) { scenery.unbuilt++; return; }
    let refs = [];
    try { const text = body.toString(); refs = [...text.matchAll(/"([^"]*\/scenery\/lod\.[0-9a-f]{64}\.glb)"/g)].map(m => m[1]); } catch {}
    const lods = await Promise.all(refs.map(ref => payload(new URL(ref, `http://x/coarse/scenery/${row.key}.json`).pathname)));
    if (lods.some(b => b < 0)) { scenery.unbuilt++; return; }
    if (row.trees_url) {
      const rel = path.join('coarse', new URL(row.trees_url, 'http://x/coarse/').pathname.slice('/coarse/'.length));
      if (!fs.existsSync(path.join(OUT, rel))) {
        const trees = await settled(`/coarse/${row.trees_url}`, deadline);
        if (!trees) { scenery.unbuilt++; return; }
        writeJson(rel, trees);
      }
    }
    writeJson(path.join('coarse', 'scenery', `${row.key}.json`), body);  // last: its files are in place
    published[row.tile] = row; scenery.published++;
  }));
  writeJson(path.join('coarse', 'scenery-catalogue.json'), Buffer.from(JSON.stringify({ at: new Date().toISOString(), recipe, generation, tiles: published })));
}

const t0 = Date.now();
const tick = setInterval(() => console.error(`${((Date.now() - t0) / 1000).toFixed(0)} s: ${stats.tiles} tiles, scenery ${JSON.stringify(scenery)}, ${stats.fetched} files fetched ` +
  `(${(stats.bytes / 1e9).toFixed(2)} GB), skipped ${JSON.stringify(stats.skipped)}, errors ${stats.errors.length}`), 30_000);
const nav = await fragSlot(() => get('/coarse/navigation.json', 'identity'));
if (nav.status === 200) writeJson(path.join('coarse', 'navigation.json'), decode(nav));
else stats.errors.push(`navigation.json: ${nav.status || nav.error}`);
await Promise.all([
  ...TILES.map(id => exportTile(id).catch(e => stats.errors.push(`${id}: ${e.message}`))),
  SQUARES.length ? exportScenery().catch(e => stats.errors.push(`scenery: ${e.message}`)) : null,
]);
clearInterval(tick);
writeAtomic(path.join(OUT, 'coarse', 'export-index.json'), JSON.stringify({ at: new Date().toISOString(), src: SRC.origin, tiles: index }));
console.log(JSON.stringify({ ...stats, scenery, errors: stats.errors.slice(0, 20), errorCount: stats.errors.length, seconds: (Date.now() - t0) / 1000 }));
process.exit(0);

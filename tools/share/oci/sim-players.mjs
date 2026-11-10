// Protocol-level load test for city.lab tile serving: N simulated players start the game at the same moment and
// drive to random destinations, streaming coarse tiles roughly the way game/src/world/coarseTiles.ts does.
// No rendering, no physics: only the HTTP traffic and its timing.
//
// Per player (one HTTP/2 connection, like Chrome; download capped at RATE bytes/s):
//   boot    index.html, its entry scripts, the stable boot assets (boot-assets.json: cars, nature, audio, scenery,
//           navigation), then the 3x3 window around the spawn.
//   drive   straight lines between random destination tiles at 8-20 m/s; a 1 s tick picks
//             wanted = 3x3 window around the player's L0 tile,
//             lead   = 3x3 windows along the heading up to clamp(28 s * speed, 450, 700) m,
//             view   = tiles within 400 m inside a +-50 deg wedge, seen from now and from 8 s ahead.
//   a tile  preview = fragment (manifest.json) + collision bundle; full = vis glb, the ktx2/bin it references,
//           roads, placement and vehicle-model glbs (and what those reference). Wanted tiles first, 6 full
//           installs at a time, as the client does. Immutable /_v/ files are fetched once per player (browser cache).
// Metrics: boot time; per wanted tile, how long it waited for full detail after entering the window (the client
// shows "waiting" after 5 s); seconds the tile under the player lacked full detail; bytes, requests, errors, TTFB.
//
//   node sim-players.mjs [--base https://city.lab.madekivi.fi] [--connect 82.70.50.61] [--n 16] [--secs 600]
//        [--rate 12.5e6] [--tiles tiles.txt] [--out result.json] [--stagger 0] [--insecure (OCI's test certificate)]
import http2 from 'node:http2';
import zlib from 'node:zlib';
import fs from 'node:fs';
import path from 'node:path';

const arg = (k, d) => { const i = process.argv.indexOf(`--${k}`); return i > 0 ? process.argv[i + 1] : d; };
const BASE = new URL(arg('base', 'https://city.lab.madekivi.fi'));
const CONNECT = arg('connect', '');
const N = Number(arg('n', 16)), SECS = Number(arg('secs', 600)), RATE = Number(arg('rate', 12.5e6));
const STAGGER = Number(arg('stagger', 0)), OUT = arg('out', ''), INSECURE = process.argv.includes('--insecure');
const HERE = path.dirname(new URL(import.meta.url).pathname);
const BOOT = JSON.parse(fs.readFileSync(path.join(HERE, 'boot-assets.json'), 'utf8'));
// Destination tiles: only tiles known to be built, so the run never asks the generator for new ones.
const TILES = fs.readFileSync(arg('tiles', path.join(HERE, 'tiles-city.txt')), 'utf8').split(/\s+/).filter(Boolean);
const L0 = 250, SPAWN = { x: 23459668 - 23459500, z: -(6704254 - 6704500) };
const DETAIL_SLOTS = 6, PREVIEW_SLOTS = 6, MAX_STREAMS = 64, WAITING_MS = 5000, TIMEOUT_MS = 60_000;
const T0 = Date.now(), now = () => Date.now() - T0;
const sleep = ms => new Promise(r => setTimeout(r, ms));
const tileAt = (x, z) => `t0_${Math.floor(x / L0)}_${Math.floor(z / L0)}`;
const ij = id => id.slice(3).split('_').map(Number);
const centre = id => { const [i, j] = ij(id); return { x: (i + 0.5) * L0, z: (j + 0.5) * L0 }; };
const distTo = (id, x, z) => { const [i, j] = ij(id); const dx = Math.max(i * L0 - x, 0, x - (i + 1) * L0), dz = Math.max(j * L0 - z, 0, z - (j + 1) * L0); return Math.hypot(dx, dz); };
const window3 = (x, z) => { const i = Math.floor(x / L0), j = Math.floor(z / L0), out = []; for (let a = -1; a <= 1; a++) for (let b = -1; b <= 1; b++) out.push(`t0_${i + a}_${j + b}`); return out; };
const pct = (a, p) => { if (!a.length) return null; const s = [...a].sort((x, y) => x - y); return s[Math.min(s.length - 1, Math.floor(p * s.length))]; };

// Glb JSON chunk: external images and buffers it references.
function glbRefs(buf) {
  if (buf.length < 20 || buf.readUInt32LE(0) !== 0x46546c67) return [];
  const len = buf.readUInt32LE(12), j = JSON.parse(buf.subarray(20, 20 + len).toString());
  return [...(j.images ?? []), ...(j.buffers ?? [])].map(o => o.uri).filter(u => u && !u.startsWith('data:'));
}

class Player {
  constructor(i) {
    this.i = i; this.client = `sim${i}-${T0.toString(36)}`;
    this.fetched = new Set(); this.tiles = new Map(); this.queue = []; this.active = 0; this.details = 0; this.previews = 0;
    this.bucket = { t: Date.now(), debt: 0 };
    this.stats = { bytes: 0, requests: 0, errors: 0, errorKinds: {}, ttfb: [], waits: [], late: 0, stallS: 0, bootMs: null, readyMs: null, tilesFull: 0, legs: 0 };
    this.pos = { ...SPAWN }; this.speed = 0; this.dir = { x: 0, z: 1 }; this.dest = null;
  }
  session() {
    if (this.sess && !this.sess.closed && !this.sess.destroyed) return this.sess;
    const lookup = CONNECT ? (h, o, cb) => (o && o.all ? cb(null, [{ address: CONNECT, family: 4 }]) : cb(null, CONNECT, 4)) : undefined;
    // Chrome's HTTP/2 flow-control windows (6 MB per stream, 15 MB per connection); Node's 64 KB default would cap
    // one connection at a few MB/s on a 15 ms path and measure the client instead of the server.
    this.sess = http2.connect(BASE.origin, { servername: BASE.hostname, lookup, rejectUnauthorized: !INSECURE,
      settings: { initialWindowSize: 6 * 1024 * 1024 } });
    this.sess.on('connect', () => { try { this.sess.setLocalWindowSize(15 * 1024 * 1024); } catch {} });
    this.sess.on('error', () => {}); this.sess.on('goaway', () => {});
    return this.sess;
  }
  // Token bucket shared by the player's streams: a stream pauses while the player is ahead of RATE.
  throttle(stream, n) {
    const t = Date.now(); this.bucket.debt = Math.max(0, this.bucket.debt - (t - this.bucket.t) / 1000 * RATE) + n; this.bucket.t = t;
    const wait = this.bucket.debt / RATE * 1000 - 50;
    if (wait > 0) { stream.pause(); setTimeout(() => stream.resume(), wait); }
  }
  get(p, { parse = false } = {}) {
    return new Promise(resolve => {
      const run = () => {
        this.active++;
        const start = Date.now(); let first = 0, size = 0, status = 0, enc = '', chunks = parse ? [] : null, done = false;
        const finish = (err) => {
          if (done) return; done = true; this.active--; this.stats.requests++; this.stats.bytes += size;
          if (err || status >= 400) { this.stats.errors++; const k = err ? String(err.code || err.message).slice(0, 40) : `HTTP ${status}`; this.stats.errorKinds[k] = (this.stats.errorKinds[k] ?? 0) + 1; }
          if (first) this.stats.ttfb.push(first - start);
          const next = this.queue.shift(); if (next) next();
          let body = null;
          if (!err && status < 400 && chunks) { body = Buffer.concat(chunks); try { if (enc === 'br') body = zlib.brotliDecompressSync(body); else if (enc === 'gzip') body = zlib.gunzipSync(body); } catch { body = null; } }
          resolve({ ok: !err && status > 0 && status < 400, status, body, ms: Date.now() - start });
        };
        let s;
        try { s = this.session().request({ ':path': p, 'accept-encoding': 'br, gzip', 'user-agent': 'turku3d-sim/1' }); }
        catch (e) { finish(e); return; }
        s.setTimeout(TIMEOUT_MS, () => { s.close(http2.constants.NGHTTP2_CANCEL); finish(new Error('timeout')); });
        s.on('response', h => { first = Date.now(); status = h[':status']; enc = h['content-encoding'] ?? ''; });
        s.on('data', c => { size += c.length; if (chunks) chunks.push(c); this.throttle(s, c.length); });
        s.on('end', () => finish());
        s.on('error', e => finish(e));
        s.on('close', () => finish(status ? null : new Error('closed')));
        s.end();
      };
      if (this.active < MAX_STREAMS) run(); else this.queue.push(run);
    });
  }
  // An immutable file once per player; a glb also pulls what it references.
  async asset(url) {
    const u = new URL(url, BASE), p = u.pathname + u.search;
    if (this.fetched.has(p)) return true;
    this.fetched.add(p);
    const glb = u.pathname.endsWith('.glb');
    const r = await this.get(p, { parse: glb });
    if (!r.ok) { this.fetched.delete(p); return false; }
    if (glb && r.body) {
      let refs = [];
      try { refs = glbRefs(r.body); } catch {}
      const res = await Promise.all(refs.map(ref => this.asset(new URL(ref, u).href)));
      return res.every(Boolean);
    }
    return true;
  }
  tile(id) { let t = this.tiles.get(id); if (!t) this.tiles.set(id, t = { state: 'none', busy: false }); return t; }
  async preview(id, t) {
    t.busy = true;
    const r = await this.get(`/coarse/${id}/manifest.json?d=${Math.round(distTo(id, this.pos.x, this.pos.z))}&client=${this.client}`, { parse: true });
    let frag = null; try { frag = r.ok && r.body ? JSON.parse(r.body) : null; } catch {}
    // 422 unbuildable (a tile the host does not publish): the game never asks again (world/coarseTiles.ts); others retry.
    if (!frag?.visual?.[id]) { t.state = 'missing'; t.busy = false; t.retryAt = r.status === 422 ? Infinity : now() + 5000; return; }
    t.frag = frag;
    const base = new URL(`/coarse/${id}/`, BASE);
    const col = frag.collision_bundle ? [frag.collision_bundle.url] : Object.values(frag.collision ?? {}).map(c => c.url);
    await Promise.all(col.map(u => this.asset(new URL(u, base).href)));
    t.state = 'preview'; t.busy = false;
  }
  async full(id, t) {
    t.busy = true; this.details++;
    const f = t.frag, base = new URL(`/coarse/${id}/`, BASE);
    const urls = [f.visual[id].url, f.roads?.url, ...(f.buildingPlacements?.models ?? []).map(m => m.uri), ...Object.values(f.vehicle_models ?? {}).map(m => m.url)].filter(Boolean);
    const ok = (await Promise.all(urls.map(u => this.asset(new URL(u, base).href)))).every(Boolean);
    this.details--; t.busy = false;
    if (ok) { t.state = 'full'; t.fullAt = now(); this.stats.tilesFull++; } else t.retryAt = now() + 5000;
  }
  pickDest() {
    const id = TILES[Math.floor(Math.random() * TILES.length)], c = centre(id);
    this.dest = { x: c.x + (Math.random() - 0.5) * 200, z: c.z + (Math.random() - 0.5) * 200 };
    this.speed = 8 + Math.random() * 12; this.stats.legs++;
  }
  move(dt) {
    if (!this.dest) this.pickDest();
    const dx = this.dest.x - this.pos.x, dz = this.dest.z - this.pos.z, d = Math.hypot(dx, dz);
    if (d < 30) { this.pickDest(); return; }
    this.dir = { x: dx / d, z: dz / d };
    const step = Math.min(d, this.speed * dt);
    this.pos.x += this.dir.x * step; this.pos.z += this.dir.z * step;
  }
  sets() {
    const { x, z } = this.pos, wanted = window3(x, z), seen = new Set(wanted), lead = [], view = [];
    if (this.speed >= 3) {
      const reach = Math.min(Math.max(28 * this.speed, 450), 700);
      for (let s = 50; s <= reach; s += 50) for (const id of window3(x + this.dir.x * s, z + this.dir.z * s)) if (!seen.has(id)) { seen.add(id); lead.push(id); }
    }
    const ahead = this.speed >= 3 ? Math.min(8 * this.speed, 200) : 0;
    for (const [ox, oz] of [[x, z], [x + this.dir.x * ahead, z + this.dir.z * ahead]]) {
      for (let i = Math.floor((ox - 400) / L0); i <= Math.floor((ox + 400) / L0); i++) for (let j = Math.floor((oz - 400) / L0); j <= Math.floor((oz + 400) / L0); j++) {
        const id = `t0_${i}_${j}`; if (seen.has(id)) continue;
        const c = centre(id), cx = c.x - ox, cz = c.z - oz, d = Math.hypot(cx, cz);
        if (d <= 400 && (cx * this.dir.x + cz * this.dir.z) / (d || 1) >= Math.cos(50 * Math.PI / 180)) { seen.add(id); view.push(id); }
      }
    }
    return { wanted, lead, view };
  }
  schedule(wanted, others) {
    const t = now();
    for (const id of wanted) { const e = this.tile(id); if (e.state === 'none' && !e.busy) void this.preview(id, e); }
    for (const id of others) {
      if (this.previews >= PREVIEW_SLOTS) break;
      const e = this.tile(id);
      if (e.state === 'none' && !e.busy) { this.previews++; void this.preview(id, e).finally(() => this.previews--); }
    }
    for (const id of [...wanted, ...others]) {
      const e = this.tiles.get(id);
      if (e?.state === 'missing' && !e.busy && t >= e.retryAt) e.state = 'none';
      const slots = wanted.includes(id) ? DETAIL_SLOTS : DETAIL_SLOTS - 1;
      if (e?.state === 'preview' && !e.busy && this.details < slots && t >= (e.retryAt ?? 0)) void this.full(id, e);
    }
  }
  async run(deadline) {
    const t0 = now();
    const index = await this.get('/', { parse: true });
    const html = index.body?.toString() ?? '';
    const entry = [...html.matchAll(/(?:src|href)="(\/assets\/[^"]+\.(?:js|css))"/g)].map(m => m[1]);
    await Promise.all([...entry, ...BOOT].map(p => this.asset(p)));
    this.stats.bootMs = now() - t0;
    // Start window: the 3x3 around the spawn, fully detailed before play starts.
    const start = window3(this.pos.x, this.pos.z);
    while (now() < deadline && !start.every(id => ['full', 'missing'].includes(this.tile(id).state))) { this.schedule(start, []); await sleep(250); }
    this.stats.readyMs = now() - t0;
    const waitingSince = new Map(); let last = now();
    while (now() < deadline) {
      const t = now(), dt = (t - last) / 1000; last = t;
      this.move(dt);
      const { wanted, lead, view } = this.sets();
      for (const id of wanted) {
        const e = this.tile(id);
        if (e.state !== 'full' && e.state !== 'missing' && !waitingSince.has(id)) waitingSince.set(id, t);
      }
      for (const [id, since] of waitingSince) {
        const e = this.tiles.get(id);
        if (e?.state === 'full' || e?.state === 'missing' || !wanted.includes(id)) {
          if (e?.state === 'full') { const w = e.fullAt - since; this.stats.waits.push(w); if (w > WAITING_MS) this.stats.late++; }
          waitingSince.delete(id);
        }
      }
      const here = this.tiles.get(tileAt(this.pos.x, this.pos.z));
      if (here?.state !== 'full' && here?.state !== 'missing') this.stats.stallS += dt;
      this.schedule(wanted, [...lead, ...view]);
      // Forget tiles far behind (their files stay in the browser cache).
      for (const [id, e] of this.tiles) if (!e.busy && distTo(id, this.pos.x, this.pos.z) > 1500) this.tiles.delete(id);
      await sleep(1000);
    }
    this.sess?.close();
  }
}

const players = Array.from({ length: N }, (_, i) => new Player(i));
const deadline = SECS * 1000;
let lastBytes = 0;
const progress = setInterval(() => {
  const bytes = players.reduce((a, p) => a + p.stats.bytes, 0), t = now() / 1000;
  const waits = players.flatMap(p => p.stats.waits), late = players.reduce((a, p) => a + p.stats.late, 0);
  const booted = players.filter(p => p.stats.readyMs !== null).length, errs = players.reduce((a, p) => a + p.stats.errors, 0);
  console.error(`${t.toFixed(0).padStart(4)} s  ${((bytes - lastBytes) / 10 / 1e6).toFixed(1).padStart(6)} MB/s  total ${(bytes / 1e9).toFixed(2)} GB  ready ${booted}/${N}  ` +
    `inflight ${players.reduce((a, p) => a + p.active + p.queue.length, 0)}  tiles ${players.reduce((a, p) => a + p.stats.tilesFull, 0)}  ` +
    `wait p50 ${((pct(waits, 0.5) ?? 0) / 1000).toFixed(1)} p95 ${((pct(waits, 0.95) ?? 0) / 1000).toFixed(1)} s  late ${late}  errors ${errs}`);
  lastBytes = bytes;
}, 10_000);
await Promise.all(players.map((p, i) => sleep(i * STAGGER * 1000).then(() => p.run(deadline))));
clearInterval(progress);
const all = players.map(p => p.stats);
const summary = {
  base: BASE.origin, connect: CONNECT || null, players: N, secs: SECS, rateMBps: RATE / 1e6, at: new Date(T0).toISOString(),
  bytesGB: +(all.reduce((a, s) => a + s.bytes, 0) / 1e9).toFixed(2), requests: all.reduce((a, s) => a + s.requests, 0),
  errors: all.reduce((a, s) => a + s.errors, 0), errorKinds: all.reduce((m, s) => { for (const [k, v] of Object.entries(s.errorKinds)) m[k] = (m[k] ?? 0) + v; return m; }, {}),
  bootS: { p50: pct(all.map(s => s.bootMs), 0.5) / 1000, max: Math.max(...all.map(s => s.bootMs)) / 1000 },
  readyS: { p50: pct(all.map(s => s.readyMs), 0.5) / 1000, max: Math.max(...all.map(s => s.readyMs)) / 1000 },
  tileWaitS: { n: all.flatMap(s => s.waits).length, p50: pct(all.flatMap(s => s.waits), 0.5) / 1000, p95: pct(all.flatMap(s => s.waits), 0.95) / 1000, max: Math.max(0, ...all.flatMap(s => s.waits)) / 1000 },
  lateTiles: all.reduce((a, s) => a + s.late, 0), stallS: +all.reduce((a, s) => a + s.stallS, 0).toFixed(0),
  ttfbMs: { p50: pct(all.flatMap(s => s.ttfb), 0.5), p95: pct(all.flatMap(s => s.ttfb), 0.95) },
  perPlayer: all.map((s, i) => ({ i, GB: +(s.bytes / 1e9).toFixed(2), bootS: s.bootMs / 1000, readyS: s.readyMs / 1000, tiles: s.tilesFull, late: s.late, stallS: +s.stallS.toFixed(0), legs: s.legs, errors: s.errors })),
};
const text = JSON.stringify(summary, null, 1);
if (OUT) fs.writeFileSync(OUT, text);
console.log(text);
process.exit(0);

// Visual patrol capture (tools/patrol/README.md). Run under a host lease, e.g.
//   session.py run win patrol --kind func --load-timeout 180 -- node tools/patrol/capture.mjs --out DIR --pass color
// It drives one QA page (?cam=, qa/README.md §2) through the spots and drives of spots.json:
//   pass=color: per spot the ground height (collision index), stills at 3 headings + a raised view, and a still-camera
//               frame sequence (per-pixel |L_t - L_t-1| and temporal high-pass maps, first/last frame); per drive a
//               fixed-step camera run along the street with a per-frame block-difference trace and a JPEG strip.
//   pass=id:    the same still poses (read from the colour run's poses.json) in the object-id pass, plus its id table.
// Everything lands in DIR; analyze.py turns it into findings.
import fs from 'node:fs';
import path from 'node:path';
import { execFileSync } from 'node:child_process';
import { pathToFileURL } from 'node:url';

const args = Object.fromEntries(process.argv.slice(2).reduce((acc, a, i, all) => {
  if (a.startsWith('--')) acc.push([a.slice(2), all[i + 1]?.startsWith('--') || all[i + 1] === undefined ? '1' : all[i + 1]]);
  return acc;
}, []));
const HERE = path.dirname(new URL(import.meta.url).pathname);
const repo = process.env.PATROL_PLAYWRIGHT_REPO ?? '/Users/wnt/Documents/game-test';
const { chromium } = await import(pathToFileURL(path.join(repo, 'game/node_modules/@playwright/test/index.mjs')).href);
const out = args.out ?? fail('--out DIR is required');
const pass = args.pass ?? 'color';
const base = args.base ?? 'https://city.lab.madekivi.fi/';
const cfg = JSON.parse(fs.readFileSync(args.spots ?? path.join(HERE, 'spots.json'), 'utf8'));
const only = args.only ? new Set(args.only.split(',')) : null;
const [W, H] = (args.size ?? '960x540').split('x').map(Number);
const FRAMES = Number(args.frames ?? 60);
const DRIVE_SPEED = Number(args.speed ?? 12); // m/s at a virtual 30 Hz step, independent of the real frame rate
const E0 = 23459500, N0 = 6704500;
fs.mkdirSync(out, { recursive: true });
const log = (...m) => { const line = `[${new Date().toISOString().slice(11, 19)}] ${m.join(' ')}`; console.error(line); fs.appendFileSync(path.join(out, `capture-${pass}.log`), line + '\n'); };
function fail(msg) { console.error(msg); process.exit(2); }
const touch = () => { if (!process.env.LEASE_NAME) return; try { execFileSync('python3', [`${process.env.HOME}/turku3d/tools/host-lease/lease.py`, 'touch', process.env.LEASE_NAME, process.env.LEASE_HOST ?? 'win'], { stdio: 'ignore' }); } catch (e) { if (e.status === 4) { log('lease lost'); process.exit(125); } } };

const browser = await chromium.connectOverCDP(process.env.BROWSER_CDP ?? fail('BROWSER_CDP unset (run under session.py)'));
const ctx = browser.contexts()[0];
const page = ctx.pages()[0] ?? await ctx.newPage();
await page.setViewportSize({ width: W, height: H });
const errors = [];
page.on('pageerror', e => errors.push(String(e).slice(0, 300)));
page.on('console', m => { if (m.type() === 'error') errors.push(m.text().slice(0, 300)); });

const spots = cfg.spots.filter(s => !only || only.has(s.id));
const drives = pass === 'color' ? (cfg.drives ?? []).filter(d => !only || only.has(d.id)) : [];
const posesFile = path.join(out, 'poses.json');
// The colour pass merges into an existing poses.json, so a run can be resumed with --only.
const poses = fs.existsSync(posesFile) ? JSON.parse(fs.readFileSync(posesFile, 'utf8')) : pass === 'id' ? fail('no poses.json: run the colour pass first') : {};
const first = pass === 'id' ? Object.values(poses).find(p => p.views)?.views[0] : null;
const start = first ?? { E: spots[0]?.cam?.[0] ?? spots[0].E, N: spots[0]?.cam?.[1] ?? spots[0].N, H: 40, yaw: 0, pitch: -10, fov: 60 };
const query = new URLSearchParams({ cam: [start.E, start.N, start.H, start.yaw, start.pitch, start.fov].join(','), time: cfg.time ?? '14:00',
  quality: cfg.quality ?? 'medium', ...(pass === 'id' ? { pass: 'id', traffic: '0', walkers: '0' } : { traffic: '10', walkers: '8' }) });
const url = `${base}?${query}`;
log('open', url);
const t0 = Date.now();
await page.goto(url, { waitUntil: 'commit', timeout: 180000 });

async function waitReady(limitMs = 150000) {
  const t = Date.now();
  for (;;) {
    const st = await page.evaluate(() => ({ ready: !!window.__qaReady, err: window.__qaError ?? null, move: typeof window.__qaMove })).catch(() => ({}));
    if (st.move === 'function' && (st.ready || st.err)) return { ...st, ms: Date.now() - t };
    if (Date.now() - t > limitMs) return { ready: false, err: 'patrol: not ready in time', ms: Date.now() - t };
    await new Promise(r => setTimeout(r, 500));
  }
}
const move = pose => page.evaluate(p => window.__qaMove(p), pose);

// One still: wait `settle` rendered frames, then read the canvas back as PNG.
async function still(file, settle = 12, hidePopulation = false) {
  const b64 = await page.evaluate(async ([settle, hide]) => {
    const pop = window.__game?.population; const shown = pop?.root?.visible;
    if (hide && pop?.root) pop.root.visible = false;
    const src = document.querySelector('canvas');
    await new Promise(resolve => {
      let n = 0; const prev = window.__render.afterFrame;
      window.__render.afterFrame = () => { prev?.(); if (++n >= settle) { window.__render.afterFrame = prev; resolve(); } };
    });
    const c = new OffscreenCanvas(src.width, src.height); c.getContext('2d').drawImage(src, 0, 0);
    if (hide && pop?.root) pop.root.visible = shown;
    const buf = new Uint8Array(await (await c.convertToBlob({ type: 'image/png' })).arrayBuffer());
    let s = ''; for (let i = 0; i < buf.length; i += 0x8000) s += String.fromCharCode(...buf.subarray(i, i + 0x8000));
    return btoa(s);
  }, [settle, hidePopulation]);
  fs.writeFileSync(file, Buffer.from(b64, 'base64'));
}

// Still-camera sequence: FRAMES consecutive rendered frames; maps of mean |L_t - L_t-1| (d1), mean temporal high-pass
// |L_t - (L_t-1 + L_t+1)/2| (d2: shimmer, not smooth sway) and |L_last - L_0| (dn: slow wander), plus frame intervals.
async function sequence(prefix, frames) {
  const res = await page.evaluate(async frames => {
    const pop = window.__game?.population; const shown = pop?.root?.visible;
    if (pop?.root) pop.root.visible = false; // moving cars and walkers are not artefacts
    const src = document.querySelector('canvas'); const w = src.width, h = src.height, n = w * h;
    const c = new OffscreenCanvas(w, h), g = c.getContext('2d', { willReadFrequently: true });
    const d1 = new Float32Array(n), d2 = new Float32Array(n); let L0 = null, a = null, b = null, k = 0, k2 = 0;
    const iv = []; let last = performance.now(); const shots = [];
    await new Promise(r => { let i = 0; const prev = window.__render.afterFrame; window.__render.afterFrame = () => { prev?.(); if (++i >= 6) { window.__render.afterFrame = prev; r(); } }; });
    await new Promise(resolve => {
      const prev = window.__render.afterFrame;
      window.__render.afterFrame = () => {
        prev?.();
        const t = performance.now(); iv.push(t - last); last = t;
        g.drawImage(src, 0, 0);
        const px = g.getImageData(0, 0, w, h).data, L = new Float32Array(n);
        for (let i = 0, j = 0; i < n; i++, j += 4) L[i] = 0.2126 * px[j] + 0.7152 * px[j + 1] + 0.0722 * px[j + 2];
        if (!L0) L0 = L;
        if (b) { for (let i = 0; i < n; i++) d1[i] += Math.abs(L[i] - b[i]); k++; }
        if (a) { for (let i = 0; i < n; i++) d2[i] += Math.abs(b[i] - 0.5 * (a[i] + L[i])); k2++; }
        a = b; b = L;
        if (k === 0 || k === 1 || k === frames) shots.push([k, c.convertToBlob({ type: 'image/png' })]);
        if (k >= frames) { window.__render.afterFrame = prev; resolve(); }
      };
    });
    if (pop?.root) pop.root.visible = shown;
    const dn = new Float32Array(n); for (let i = 0; i < n; i++) { d1[i] /= k; d2[i] /= k2; dn[i] = Math.abs(b[i] - L0[i]); }
    const enc = u => { let s = ''; for (let i = 0; i < u.length; i += 0x8000) s += String.fromCharCode(...u.subarray(i, i + 0x8000)); return btoa(s); };
    const pngs = []; for (const [i, p] of shots) pngs.push([i, enc(new Uint8Array(await (await p).arrayBuffer()))]);
    const q = (x, p) => { const s = [...x].sort((u, v) => u - v); return s.length ? +s[Math.floor((s.length - 1) * p)].toFixed(1) : null; };
    return { w, h, frames: k, int50: q(iv.slice(1), .5), int95: q(iv.slice(1), .95), d1: enc(new Uint8Array(d1.buffer)), d2: enc(new Uint8Array(d2.buffer)), dn: enc(new Uint8Array(dn.buffer)), pngs };
  }, frames);
  for (const k of ['d1', 'd2', 'dn']) fs.writeFileSync(`${prefix}-${k}.f32`, Buffer.from(res[k], 'base64'));
  for (const [i, s] of res.pngs) fs.writeFileSync(`${prefix}-f${String(i).padStart(2, '0')}.png`, Buffer.from(s, 'base64'));
  return { w: res.w, h: res.h, frames: res.frames, int50: res.int50, int95: res.int95 };
}

// Collision height grid round (E, N): (2*half/step+1)^2 heights of the highest surface below `top` (null: none or
// not resident). analyze.py looks for pits, ditches and steps in it.
async function groundGrid(E, N, top, half = 40, step = 2) {
  return page.evaluate(([x0, z0, top, half, step]) => {
    const g = window.__game?.population?.ground; if (!g) return null;
    const rows = [];
    for (let dz = -half; dz <= half + 1e-9; dz += step) { const row = []; for (let dx = -half; dx <= half + 1e-9; dx += step) { const y = g.ground(x0 + dx, top, z0 + dz, 40); row.push(y === null ? null : +y.toFixed(2)); } rows.push(row); }
    return { half, step, top, rows };
  }, [E - E0, -(N - N0), top, half, step]);
}

// Ground under (E, N): the highest collision surface below `top` (the collision index the traffic stands on).
async function groundAt(E, N, top = 80, depth = 120) {
  for (let i = 0; i < 40; i++) {
    const y = await page.evaluate(([x, z, top, depth]) => window.__game?.population?.ground?.ground(x, top, z, depth) ?? null, [E - E0, -(N - N0), top, depth]);
    if (y !== null) return y;
    await new Promise(r => setTimeout(r, 500));
  }
  return null;
}

const ready0 = await waitReady(240000);
log('first ready', JSON.stringify(ready0), `${Date.now() - t0} ms`);
const info = await page.evaluate(() => ({ qaInfo: window.__qaInfo, adapter: window.__game?.backend, build: document.querySelector('meta[name=turku3d-build]')?.content, ua: navigator.userAgent, canvas: [document.querySelector('canvas')?.width, document.querySelector('canvas')?.height] })).catch(e => ({ err: String(e) }));
fs.writeFileSync(path.join(out, `info-${pass}.json`), JSON.stringify({ url, info, ready0, started: new Date(t0).toISOString() }, null, 1));

for (const s of spots) {
  touch();
  const ts = Date.now(); const rec = pass === 'id' ? poses[s.id] : { id: s.id, views: [], ready: [] };
  if (!rec) { log(s.id, 'no colour pose, skipped'); continue; }
  try {
    let views = rec.views;
    if (pass === 'color') {
      let E, N, Hs, heading, pitch = 0, fov = 60;
      if (s.cam) { [E, N, Hs, heading, pitch, fov] = s.cam; }
      else {
        E = s.E; N = s.N; heading = s.heading;
        await move({ E, N, H: (s.top ?? 30) + 20, yaw: heading, pitch: -30, fov });
        const r0 = await waitReady(); rec.ready.push(r0); if (r0.err) rec.unsettled = true;
        const gy = await groundAt(E, N, s.top ?? 80);
        rec.ground = gy;
        Hs = gy === null ? 6 : gy + 1.7;
      }
      views = [0, 120, 240].map((d, i) => ({ view: `h${i}`, E, N, H: Hs, yaw: (heading + d) % 360, pitch, fov, street: true }));
      views.push({ view: 'raised', E, N, H: Hs + 25, yaw: heading, pitch: -22, fov, street: false });
      rec.views = views; rec.x = E - E0; rec.z = -(N - N0);
      const grid = await groundGrid(E, N, Hs - 1.7 + 6);
      if (grid) { fs.writeFileSync(path.join(out, `${s.id}-ground.json`), JSON.stringify(grid)); rec.groundGrid = `${s.id}-ground.json`; }
    }
    for (const v of views) {
      await move({ E: v.E, N: v.N, H: v.H, yaw: v.yaw, pitch: v.pitch, fov: v.fov });
      // A spot that did not settle once (e.g. its tile never gets detailed) is a finding already: later views
      // there wait 25 s instead of the 120 s QA timeout.
      const r = await waitReady(rec.unsettled ? 25000 : 150000); (rec.ready ??= []).push({ view: v.view, ...r });
      if (r.err) rec.unsettled = true;
      await still(path.join(out, `${s.id}-${v.view}-${pass}.png`));
      if (pass === 'color' && v.street) await still(path.join(out, `${s.id}-${v.view}-nopop.png`), 12, true);
    }
    if (pass === 'color') {
      const v = views[0];
      await move({ E: v.E, N: v.N, H: v.H, yaw: v.yaw, pitch: v.pitch, fov: v.fov });
      await waitReady();
      rec.sequence = await sequence(path.join(out, `${s.id}-seq`), FRAMES);
    } else {
      rec.idTable = `${s.id}-idtable.json`;
      fs.writeFileSync(path.join(out, rec.idTable), JSON.stringify(await page.evaluate(() => window.__qaIdTable ?? {})));
    }
    rec[`${pass}Ms`] = Date.now() - ts;
    log(s.id, 'done', `${rec[`${pass}Ms`]} ms`, `ground ${rec.ground ?? '-'}`, JSON.stringify(rec.sequence ?? {}));
  } catch (e) { rec.error = String(e).slice(0, 400); log(s.id, 'ERROR', rec.error); }
  poses[s.id] = rec;
  if (pass === 'color') fs.writeFileSync(posesFile, JSON.stringify(poses, null, 1));
}
if (pass === 'id') fs.writeFileSync(path.join(out, 'poses-id.json'), JSON.stringify(poses, null, 1));

// Drives: the camera follows the street at eye height 1.5 m, one fixed step per rendered frame. Per frame: a 24x14
// block grid of mean |L_t - L_t-1| (pop detection) and the real frame interval (stutter); a 384x216 JPEG strip.
const driveRecs = {};
for (const d of drives) {
  touch();
  const ts = Date.now();
  try {
    const pts = d.path_xz; const p0 = pts[0], p1 = pts[1];
    const h0 = (Math.atan2(p1[0] - p0[0], -(p1[1] - p0[1])) * 180 / Math.PI + 360) % 360;
    await move({ E: p0[0] + E0, N: N0 - p0[1], H: 40, yaw: h0, pitch: -10, fov: 60 });
    await waitReady();
    const g0 = await groundAt(p0[0] + E0, N0 - p0[1], 40);
    await move({ E: p0[0] + E0, N: N0 - p0[1], H: (g0 ?? 4) + 1.5, yaw: h0, pitch: -2, fov: 60 });
    const r0 = await waitReady();
    const res = await page.evaluate(async ({ pts, speed, E0, N0, g0 }) => {
      const seg = []; let total = 0;
      for (let i = 1; i < pts.length; i++) { const l = Math.hypot(pts[i][0] - pts[i - 1][0], pts[i][1] - pts[i - 1][1]); seg.push(l); total += l; }
      const at = s => { s = Math.max(0, Math.min(total - 1e-6, s)); let i = 0; while (s > seg[i]) { s -= seg[i]; i++; } const f = s / seg[i];
        return [pts[i][0] + (pts[i + 1][0] - pts[i][0]) * f, pts[i][1] + (pts[i + 1][1] - pts[i][1]) * f]; };
      const ground = window.__game.population?.ground;
      const src = document.querySelector('canvas');
      const SW = 384, SH = 216, BX = 24, BY = 14;
      const c = new OffscreenCanvas(SW, SH), g = c.getContext('2d', { willReadFrequently: true });
      let prevL = null, s = 0, gy = g0 ?? 4, hd = null; const blocks = [], iv = [], posesOut = [], jpgs = [];
      let last = performance.now();
      await new Promise(resolve => {
        const prev = window.__render.afterFrame;
        window.__render.afterFrame = () => {
          prev?.();
          const t = performance.now(); iv.push(t - last); last = t;
          g.drawImage(src, 0, 0, SW, SH);
          const px = g.getImageData(0, 0, SW, SH).data, L = new Float32Array(SW * SH);
          for (let i = 0, j = 0; i < L.length; i++, j += 4) L[i] = 0.2126 * px[j] + 0.7152 * px[j + 1] + 0.0722 * px[j + 2];
          const blk = new Float32Array(BX * BY);
          if (prevL) for (let y = 0; y < SH; y++) for (let x = 0; x < SW; x++) { const i = y * SW + x; blk[Math.floor(y * BY / SH) * BX + Math.floor(x * BX / SW)] += Math.abs(L[i] - prevL[i]); }
          const per = (SW / BX) * (SH / BY); blocks.push(Array.from(blk, v => +(v / per).toFixed(2))); prevL = L;
          jpgs.push(c.convertToBlob({ type: 'image/jpeg', quality: 0.7 }));
          if (s >= total) { window.__render.afterFrame = prev; resolve(); return; }
          s += speed / 30;
          const [x, z] = at(s), [ax, az] = at(s + 10);
          const want = (Math.atan2(ax - x, -(az - z)) * 180 / Math.PI + 360) % 360;
          hd = hd === null ? want : hd + ((((want - hd) % 360) + 540) % 360 - 180) * 0.15;
          const y = ground?.ground(x, gy + 3, z, 8); if (y !== null && y !== undefined) gy = y;
          const pose = { E: x + E0, N: N0 - z, H: gy + 1.5, yaw: (hd + 360) % 360, pitch: -2, fov: 60 };
          posesOut.push([+x.toFixed(2), +z.toFixed(2), +gy.toFixed(2)]);
          window.__qaMove(pose);
        };
      });
      const enc = u => { let s = ''; for (let i = 0; i < u.length; i += 0x8000) s += String.fromCharCode(...u.subarray(i, i + 0x8000)); return btoa(s); };
      window.__patrolJpgs = jpgs;
      return { blocks, iv: iv.map(v => +v.toFixed(1)), poses: posesOut, frames: jpgs.length, total };
    }, { pts, speed: DRIVE_SPEED, E0, N0, g0 });
    const dir = path.join(out, d.id); fs.mkdirSync(dir, { recursive: true });
    for (let i = 0; i < res.frames; i += 40) {
      const chunk = await page.evaluate(async ([i]) => {
        const enc = u => { let s = ''; for (let k = 0; k < u.length; k += 0x8000) s += String.fromCharCode(...u.subarray(k, k + 0x8000)); return btoa(s); };
        const outp = []; for (const p of window.__patrolJpgs.slice(i, i + 40)) outp.push(enc(new Uint8Array(await (await p).arrayBuffer()))); return outp;
      }, [i]);
      chunk.forEach((s, k) => fs.writeFileSync(path.join(dir, `f${String(i + k).padStart(4, '0')}.jpg`), Buffer.from(s, 'base64')));
    }
    await page.evaluate(() => { window.__patrolJpgs = null; });
    fs.writeFileSync(path.join(dir, 'trace.json'), JSON.stringify({ id: d.id, street: d.street, speed: DRIVE_SPEED, ready: r0, ...res }));
    driveRecs[d.id] = { frames: res.frames, ms: Date.now() - ts };
    log(d.id, 'done', res.frames, 'frames', `${Date.now() - ts} ms`);
  } catch (e) { driveRecs[d.id] = { error: String(e).slice(0, 400) }; log(d.id, 'ERROR', driveRecs[d.id].error); }
}
fs.writeFileSync(path.join(out, `run-${pass}.json`), JSON.stringify({ pass, url, ms: Date.now() - t0, spots: spots.length, drives: driveRecs, errors: errors.slice(0, 50) }, null, 1));
log('finished', `${Date.now() - t0} ms`, `${errors.length} page errors`);
await page.close().catch(() => {});
await browser.close().catch(() => {});

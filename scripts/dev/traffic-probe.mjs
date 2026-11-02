#!/usr/bin/env node
// Real-road deterministic traffic probe. Evidence/raw road inputs stay in the caller's local output directory.
import fs from 'node:fs';
import path from 'node:path';
import { pathToFileURL } from 'node:url';
const args = process.argv.slice(2), opt = (key, fallback) => args.includes(key) ? args[args.indexOf(key) + 1] : fallback;
const out = path.resolve(opt('--out', `${process.env.HOME}/turku3d/loops/traffic/probe`));
const base = opt('--base', 'http://192.168.1.114:7835/coarse/');
const tiles = opt('--tiles', 't0_-1_-1,t0_0_-1,t0_-1_0,t0_0_0').split(',');
const seconds = Number(opt('--seconds', '600')), start = Number(opt('--start', '0'));
fs.mkdirSync(out, { recursive: true });
let modulePath = opt('--module', null);
if (!modulePath) {
  modulePath = path.join(out, 'traffic-sim.mjs');
  const { rolldown } = await import('../../game/node_modules/rolldown/dist/index.mjs');
  const bundle = await rolldown({ input: path.resolve('scripts/dev/traffic-entry.ts'), platform: 'node' });
  await bundle.write({ file: modulePath, format: 'esm' });
  await bundle.close();
}
{
  const { TrafficNetwork, TrafficFlow, sampleFlowPose, trafficRoads, buildTileTraffic } = await import(pathToFileURL(modulePath).href);
  const network = new TrafficNetwork();
  network.density = { hourAt: () => 16, multiplier: Number(opt('--density', '1')) };
  for (const tile of tiles) {
    const file = path.join(out, `${tile}.json`);
    if (!fs.existsSync(file)) {
      const r = await fetch(`${base}${tile}/roads.json`, { signal: AbortSignal.timeout(20000) });
      if (!r.ok) throw new Error(`${tile}: ${r.status}`);
      fs.writeFileSync(file, await r.text());
    }
    const json = JSON.parse(fs.readFileSync(file));
    network.setTile(buildTileTraffic(tile, trafficRoads(json), json.bbox, 'local-probe'));
  }
  const flow = new TrafficFlow(network); flow.reset(start);
  const frames = [], collisions = [], t0 = performance.now();
  let maxCars = 0, stopped = 0, pairs = 0;
  for (let step = 1; step <= seconds * 10; step++) {
    const t = start + step / 10;
    flow.update(t);
    const cars = [...flow.cars.values()].map(c => ({ ...sampleFlowPose(c, 0, () => 0), length: c.length,
      width: c.width, key: c.key, lane: `${c.lane.tile}:${c.lane.id}`, s: c.s, reservation: c.reservation?.id }));
    maxCars = Math.max(maxCars, cars.length); stopped += cars.filter(c => c.speed < 0.2).length;
    for (let i = 0; i < cars.length; i++) for (let j = i + 1; j < cars.length; j++) {
      const a = cars[i], b = cars[j];
      if (Math.abs(a.x - b.x) > 6 || Math.abs(a.z - b.z) > 6) continue;
      pairs++;
      if (overlap(a, b) && collisions.length < 100) collisions.push({ t, a, b });
    }
    if (step % 10 === 0) frames.push({ t, cars });
  }
  const result = { seconds, tiles, stats: flow.stats, wallMs: performance.now() - t0, maxCars,
    stoppedCarSeconds: stopped / 10, pairsChecked: pairs, overlaps: collisions.length, firstOverlaps: collisions.slice(0, 5),
    stalls: [...flow.cars.values()].filter(c => c.waiting > 90).slice(0, 15).map(c => ({ key: c.key,
      route: c.route.id, s: c.s, length: c.lane.length, waiting: c.waiting, blockedBy: c.blockedBy, reservation: c.reservation?.id })) };
  fs.writeFileSync(path.join(out, 'result.json'), JSON.stringify(result, null, 2));
  fs.writeFileSync(path.join(out, 'trace.json'), JSON.stringify({ frames, roads: [...network.tiles.values()].map(tile => ({ tile: tile.tile,
    lanes: tile.lanes.map(l => ({ id: l.id, points: [...l.points], connector: l.connector ? [...l.connector.points] : null,
      signal: l.signal, priority: l.priority, crossings: l.crossings })) })) }));
  console.log(JSON.stringify(result, null, 2));
}

function overlap(a, b) {
  for (const [x, z] of [[a.hx, a.hz], [-a.hz, a.hx], [b.hx, b.hz], [-b.hz, b.hx]]) {
    const ra = Math.abs(a.hx * x + a.hz * z) * a.length / 2 + Math.abs(-a.hz * x + a.hx * z) * a.width / 2;
    const rb = Math.abs(b.hx * x + b.hz * z) * b.length / 2 + Math.abs(-b.hz * x + b.hx * z) * b.width / 2;
    if (Math.abs((b.x - a.x) * x + (b.z - a.z) * z) >= ra + rb - 0.01) return false;
  }
  return true;
}

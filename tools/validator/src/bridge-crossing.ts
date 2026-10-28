/** Browser-free regression check on the same COL colliders and car used by the game. */
import fs from 'node:fs/promises';
import path from 'node:path';
import { createHash } from 'node:crypto';
import { RAPIER as R } from './rapier.ts';
import { MeshoptDecoder } from 'meshoptimizer';
import { readCol, buildTileColliders, SOLID_QUERY_FILTER } from '../../../game/src/shared/col/index.ts';
import { Vehicle } from '../../../game/src/vehicle/vehicle.ts';

export const bridges = [
  { name: 'Auransilta', a: [292.0456, 210.3501], b: [317.9682, 247.5093] },
  { name: 'Teatterisilta', a: [19.6584, 423.5164], b: [-12.2427, 365.3398] },
  { name: 'Myllysilta', a: [-328.2713, 629.7596], b: [-380.904, 551.6499] },
  { name: 'Martinsilta', a: [-511.5758, 632.6626], b: [-452.6923, 719.3394] },
  { name: 'Kirjastosilta', a: [459.9431, 145.4402], b: [410.1537, 112.0818] },
  { name: 'Tuomiokirkkosilta', a: [604.2688, -158.9478], b: [575.9738, -197.0894] },
  { name: 'Hämeensilta', a: [1512.9185, -416.0221], b: [1593.7142, -445.6112] },
];

export function crossing(world: any, a: number[], b: number[]) {
  const length = Math.hypot(b[0] - a[0], b[1] - a[1]);
  const dx = (b[0] - a[0]) / length, dz = (b[1] - a[1]) / length;
  const surface = (x: number, z: number) => {
    const ray = new R.Ray({ x, y: 60, z }, { x: 0, y: -1, z: 0 });
    const hit = world.castRay(ray, 80, true, R.QueryFilterFlags.EXCLUDE_SENSORS, SOLID_QUERY_FILTER);
    return hit ? 60 - hit.timeOfImpact : NaN;
  };
  world.step();
  const profile: { s: number; y: number }[] = [];
  for (let s = -14; s <= length + 14; s += 0.5) profile.push({ s, y: surface(a[0] + dx * s, a[1] + dz * s) });
  const x = a[0] - dx * 12, z = a[1] - dz * 12;
  const car = new Vehicle(R, world, { x, y: surface(x, z) + 0.8, z }, Math.atan2(-dz, dx), { castWheel: () => null });
  const coast = { throttle: 0, brake: 0, steer: 0, handbrake: false };
  let maxProgress = -12, minY = Infinity, unsupported = 0, maxUnsupported = 0;
  const samples = [];
  try {
    for (let f = 0; f < 90; f++) { car.step(1 / 60, coast); world.step(); }
    for (let f = 0; f < 1500; f++) {
      const p = car.body.translation();
      const progress = (p.x - a[0]) * dx + (p.z - a[1]) * dz;
      maxProgress = Math.max(maxProgress, progress); minY = Math.min(minY, p.y);
      const state = car.state();
      unsupported = state.wheels.some(w => w.contact) ? 0 : unsupported + 1;
      maxUnsupported = Math.max(maxUnsupported, unsupported);
      if (f % 30 === 0) samples.push({ progress, x: p.x, z: p.z, y: p.y, speed: Math.hypot(car.body.linvel().x, car.body.linvel().z), wheels: state.wheels.filter(w => w.contact).length });
      if (progress >= length + 10 || p.y < -3) break;
      // Maintain the route heading with the production steering and throttle controls.
      const q = car.body.rotation();
      const yaw = Math.atan2(2 * (q.w * q.y + q.x * q.z), 1 - 2 * (q.y * q.y + q.z * q.z));
      let error = Math.atan2(-dz, dx) - yaw;
      error = Math.atan2(Math.sin(error), Math.cos(error));
      const lateral = (p.x - a[0]) * -dz + (p.z - a[1]) * dx;
      const speed = Math.hypot(car.body.linvel().x, car.body.linvel().z);
      car.step(1 / 60, { ...coast, throttle: speed < 6 ? 0.7 : 0, steer: Math.max(-0.5, Math.min(0.5, -error * 2 - lateral * 0.1)) });
      world.step();
    }
    const jumps = profile.slice(1).map((p, i) => ({ s: p.s, delta: p.y - profile[i].y }));
    return { passed: maxProgress >= length + 10 && minY > 0 && maxUnsupported < 60,
      maxProgress, length, minY, maxUnsupported, maxStep: Math.max(...jumps.map(p => Math.abs(p.delta))), profile, samples };
  } finally { world.removeVehicleController(car.controller); world.removeRigidBody(car.body); }
}

function routeTiles(bridge: typeof bridges[number]) {
  const tiles = new Set<string>();
  const length = Math.hypot(bridge.b[0] - bridge.a[0], bridge.b[1] - bridge.a[1]);
  for (let s = -20; s <= length + 20; s++) {
    const x = bridge.a[0] + (bridge.b[0] - bridge.a[0]) * s / length;
    const z = bridge.a[1] + (bridge.b[1] - bridge.a[1]) * s / length;
    for (const ox of [-4, 4]) for (const oz of [-4, 4]) tiles.add(`t0_${Math.floor((x + ox) / 250)}_${Math.floor((z + oz) / 250)}`);
  }
  return tiles;
}

async function main() {
  const root = process.argv[2];
  if (!root) throw new Error('usage: node tools/validator/src/bridge-crossing.ts CACHE [NAME,...] [--cached]');
  const names = process.argv.slice(3).find(a => !a.startsWith('--'));
  const selected = bridges.filter(b => !names || names.split(',').includes(b.name));
  if (!selected.length) throw new Error(`unknown bridge selection: ${names}`);
  const cached = process.argv.includes('--cached');
  await R.init(); await MeshoptDecoder.ready;
  const results = [];
  // Hold anonymous player-style demand while downloading; a queued tile must
  // not disappear behind the startup warmer between individual manifest polls.
  const wanted = [...new Set(selected.flatMap(b => [...routeTiles(b)]))];
  const client = `bridge-check-${Date.now()}`;
  const demand = async (tiles: string[]) => {
    const query = new URLSearchParams({ client, tiles: tiles.join(','), urgent: tiles.join(',') });
    await fetch(`https://turku.lab.madekivi.fi/coarse/demand?${query}`, { signal: AbortSignal.timeout(5000) });
  };
  if (!cached) await demand(wanted);
  const heartbeat = cached ? undefined : setInterval(() => { void demand(wanted).catch(() => {}); }, 3000).unref();
  for (const bridge of selected) {
    const world = new R.World({ x: 0, y: -9.81, z: 0 });
    try {
      const tiles = routeTiles(bridge);
      const manifests = [];
      for (const tile of tiles) {
        const dir = path.join(root, tile); await fs.mkdir(dir, { recursive: true });
        const base = `https://turku.lab.madekivi.fi/coarse/${tile}/`;
        let manifest;
        if (cached) manifest = JSON.parse(await fs.readFile(path.join(dir, 'manifest.json'), 'utf8'));
        else {
          const deadline = Date.now() + 240000;
          do {
            const reply = await fetch(base + 'manifest.json', { signal: AbortSignal.timeout(10000) });
            manifest = await reply.json();
            if (reply.status === 200 && !manifest.stale && manifest.pass_kind === 'full') break;
            if (Date.now() > deadline) throw new Error(`${tile} did not become current: ${JSON.stringify(manifest.refresh)}`);
            await new Promise(resolve => setTimeout(resolve, 2000));
          } while (true);
          await fs.writeFile(path.join(dir, 'manifest.json'), JSON.stringify(manifest));
        }
        if (manifest.stale) throw new Error(`${tile} is stale`);
        manifests.push({ tile, state: 'built', stale: false, pass: manifest.pass_kind, visual: manifest.visual[tile].sha256 });
        for (const entry of Object.values(manifest.collision) as any[]) {
          const file = path.join(dir, entry.url); await fs.mkdir(path.dirname(file), { recursive: true });
          let bytes;
          try { bytes = await fs.readFile(file); } catch {
            if (cached) throw new Error(`missing cached payload: ${file}`);
            const reply = await fetch(base + entry.url, { signal: AbortSignal.timeout(10000) });
            if (!reply.ok) throw new Error(`${reply.status}: ${entry.url}`);
            bytes = Buffer.from(await reply.arrayBuffer()); await fs.writeFile(file, bytes);
          }
          if (createHash('sha256').update(bytes).digest('hex') !== entry.sha256) throw new Error(`hash mismatch: ${file}`);
          buildTileColliders(R, world, readCol(bytes, { decoder: MeshoptDecoder }));
        }
      }
      for (const reverse of [false, true]) {
        const result = { name: bridge.name, reverse, manifests, ...crossing(world, reverse ? bridge.b : bridge.a, reverse ? bridge.a : bridge.b) };
        results.push(result);
        await fs.writeFile(path.join(root, 'crossings.json'), JSON.stringify(results, null, 2));
        console.log(JSON.stringify({ ...result, profile: undefined, samples: undefined }));
      }
    } finally { world.free(); }
  }
  if (heartbeat) clearInterval(heartbeat);
  if (!cached) await demand([]);
  await fs.writeFile(path.join(root, 'crossings.json'), JSON.stringify(results, null, 2));
  if (results.some(r => !r.passed)) process.exitCode = 1;
}

if (import.meta.url === `file://${process.argv[1]}`) await main();

// Replays the collision streaming of capture 20261103-201621-v8hjwo (route.json: the car's pose, the gamepad
// inputs and the col.load/col.unload sequence of 90.9-98.6 s) on the real cells around the route, with the
// physics worker's tick: queued installs under the running budget, then one 60 Hz step. The old path (each cell
// built whole inside its message) is measured on the same sequence for comparison.
//   PHYSSTALL_DIR=~/turku3d/loops/physstall npx vitest run -c vitest.perf.config.ts tests/perf/physstall.test.ts
import { expect, test } from 'vitest';
import { PerformanceObserver } from 'node:perf_hooks';
import fs from 'node:fs';
import path from 'node:path';
import * as R from '@dimforge/rapier3d';
import { PhysicsWorld, DT } from '../../src/physics/world';
import { distToCollisionTile, parseCollisionId } from '../../src/core/origin';

const dir = process.env.PHYSSTALL_DIR;
/** This thread's CPU time (ms): the Mac runs other agents' builds (load 20+), so wall time measures the OS
 *  scheduler as much as the worker's own work. */
const cpu = () => { const u = process.threadCpuUsage(); return (u.user + u.system) / 1000; };
interface Route { car: number[]; inputs: number[][]; col: [number, 'load' | 'unload', string][] }

async function replay(queued: boolean) {
  const route = JSON.parse(fs.readFileSync(path.join(dir!, 'route.json'), 'utf8')) as Route;
  const bytes = (id: string) => { const p = path.join(dir!, 'cells', `${id}.col`); if (!fs.existsSync(p)) return null; const b = fs.readFileSync(p); return b.buffer.slice(b.byteOffset, b.byteOffset + b.byteLength); };
  const pw = new PhysicsWorld(R);
  const [x, y, z, qx, qy, qz, qw, vx, vy, vz] = route.car;
  const streamed = new Set(route.col.map(e => e[2]));
  for (const id of fs.readdirSync(path.join(dir!, 'cells')).filter(f => f.endsWith('.col')).map(f => f.slice(0, -4))) {
    const ij = parseCollisionId(id);
    if (ij && !streamed.has(id) && distToCollisionTile(x, z, ij.i, ij.j) < 200) await pw.loadTile(id, bytes(id)!);
  }
  pw.world.step();
  pw.spawnVehicle({ x, y, z }, 0, { rot: { x: qx, y: qy, z: qz, w: qw }, linvel: { x: vx, y: vy, z: vz } });
  pw.setCharacterEnabled(false);
  const ticks: number[] = [], pieces: number[] = [], slow: string[] = [];
  let e = 0, input = 0, loaded = 0;
  const idle = { vx: 0, vz: 0, jump: false };
  for (let tick = 0; tick * DT < 7.7; tick++) {
    const t = tick * DT, t0 = cpu();
    while (e < route.col.length && route.col[e][0] <= t) {
      const [, kind, id] = route.col[e++];
      if (kind === 'unload') { pw.unloadTile(id); continue; }
      const b = bytes(id);
      if (!b) continue;
      if (queued) pw.requestTile(id, b, r => { if (!(r instanceof Error)) loaded++; });
      else { await pw.loadTile(id, b); loaded++; }
    }
    if (queued) {
      const c0 = cpu();
      pw.pumpInstalls(2);
      const s = pw.installState.slowest, ms = cpu() - c0;
      pieces.push(ms);
      if (ms > 4) slow.push(`${s.key} ${s.phase} ${ms.toFixed(1)}`);
    }
    while (input + 1 < route.inputs.length && route.inputs[input + 1][0] <= t) input++;
    const [, throttle, brake, steer, handbrake] = route.inputs[input];
    pw.step(DT, idle, { throttle, brake, steer, handbrake: !!handbrake });
    ticks.push(cpu() - t0);
  }
  for (let k = 0; k < 400 && pw.installState.queued; k++) pw.pumpInstalls(2);
  const sorted = [...ticks].sort((a, b) => a - b);
  return { p50: sorted[sorted.length >> 1], p99: sorted[Math.floor(sorted.length * 0.99)], max: sorted[sorted.length - 1],
    over8: ticks.filter(v => v > 8).length, slow, resident: pw.tileStats().map(t => t.id).sort().join(), piece: Math.max(...pieces, 0), loaded, end: pw.vehicleState()!.pos };
}

test.skipIf(!dir)('the capture route\'s cell loads never hold a physics tick for more than a few ms', async () => {
  const gcs: number[] = [];
  const obs = new PerformanceObserver(list => { for (const e of list.getEntries()) if (e.duration > 2) gcs.push(Math.round(e.duration)); });
  obs.observe({ entryTypes: ['gc'] });
  await replay(true); // JIT warm-up
  // Best of three alternating runs: other work on the machine only ever adds time.
  const runs = { old: [] as Awaited<ReturnType<typeof replay>>[], now: [] as Awaited<ReturnType<typeof replay>>[] };
  for (let k = 0; k < 3; k++) { runs.old.push(await replay(false)); runs.now.push(await replay(true)); }
  const best = (list: typeof runs.old) => list.reduce((a, b) => (b.max < a.max ? b : a));
  const old = best(runs.old), now = best(runs.now);
  const f = (r: Awaited<ReturnType<typeof replay>>) => `CPU per tick p50 ${r.p50.toFixed(2)} p99 ${r.p99.toFixed(2)} max ${r.max.toFixed(1)} ms, ${r.over8} ticks > 8 ms, slowest pump ${r.piece.toFixed(1)} ms, ${r.loaded} loads`;
  obs.disconnect();
  console.log(`gc pauses > 2 ms: ${gcs.join(' ')}`);
  console.log(`whole-cell builds: ${f(old)}\nbudgeted pieces:   ${f(now)}\nslow pieces: ${now.slow.join(', ')}`);
  // The same cells end up resident (a load the capture unloaded before it finished is cancelled, not built).
  expect(now.resident).toBe(old.resident);
  expect(now.max).toBeLessThan(old.max / 3);
  expect(now.p99).toBeLessThan(Math.min(12, old.p99));
}, 120_000);

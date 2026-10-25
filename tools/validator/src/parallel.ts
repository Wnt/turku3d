import { Worker } from 'node:worker_threads';
import { availableParallelism } from 'node:os';
import { readFileSync } from 'node:fs';
import { MeshoptDecoder } from 'meshoptimizer';
import { readCol } from '../../../game/src/shared/col/index.ts';
import { CHECKS, validateSerial } from './validate.ts';
import type { Options } from './validate.ts';
import { TILE } from './tileChecks.ts';
import { loadFootprints } from './geo.ts';

export async function validateParallel(opts: Options, files: string[]) {
  const start = performance.now();
  const report = await validateSerial({ ...opts, workers: 1, checks: ['format', 'seams'] });
  // Format failures must not disappear when the physics jobs are partitioned.
  if (report.checks.format.status === 'fail') return report;
  await MeshoptDecoder.ready;
  const tiles = files.map(file => {
    const t = readCol(readFileSync(file), { decoder: MeshoptDecoder });
    return { file, i: t.header.tileI, j: t.header.tileJ, id: t.id };
  });
  const owned = opts.aoi ? tiles.filter(t => (t.i + 1) * TILE > opts.aoi![0] && t.i * TILE < opts.aoi![2] &&
    (t.j + 1) * TILE > opts.aoi![1] && t.j * TILE < opts.aoi![3]) : tiles;
  if (!owned.length) throw new Error('AOI does not overlap any collision tile');
  const fps = loadFootprints(opts.metadata!.footprints);
  const counts = owned.map(t => fps.filter(f => Math.floor((f.bbox[0] + f.bbox[2]) / 2 / TILE) === t.i && Math.floor((f.bbox[1] + f.bbox[3]) / 2 / TILE) === t.j).length);
  const total = counts.reduce((a, b) => a + b, 0);
  let assigned = 0, cumulative = 0;
  const jobs: Options[] = owned.map((tile, i) => {
    cumulative += counts[i];
    const end = total ? Math.round(opts.walkers * cumulative / total) : 0;
    const walkers = Math.max(counts[i], end - assigned);
    assigned = end;
    return { ...opts, workers: 1, coreTile: [tile.i, tile.j], walkers,
      seed: opts.seed + i,
      // A full one-tile halo preserves approach routes, walls and KCC continuity at the work boundary.
      inputs: tiles.filter(t => Math.abs(t.i - tile.i) <= 1 && Math.abs(t.j - tile.j) <= 1).map(t => t.file),
      checks: (opts.checks ?? CHECKS).filter(c => c !== 'format' && c !== 'seams') };
  });
  const count = Math.min(opts.workers ?? 4, availableParallelism(), jobs.length);
  const results = (await runJobs(jobs, count)).map((r, i) => ({ tile: owned[i].id, ...r }));
  const wanted = opts.checks ?? CHECKS;
  for (const c of ['format', 'seams']) if (!wanted.includes(c as any)) delete report.checks[c];
  for (const check of wanted.filter(c => c !== 'format' && c !== 'seams')) {
    const perTile = results.map(r => ({ tile: r.tile, ...r.checks[check] }));
    report.checks[check] = { status: perTile.some(r => r.status === 'fail') ? 'fail' : perTile.some(r => r.status === 'pass') ? 'pass' : 'skipped', perTile };
  }
  report.pass = Object.values(report.checks).every((c: any) => c.status !== 'fail');
  report.summary = Object.fromEntries(Object.entries(report.checks).map(([k, v]: [string, any]) => [k, v.status]));
  report.elapsedMs = Math.round(performance.now() - start);
  report.cpuMs += results.reduce((s, r) => s + r.cpuMs, 0);
  report.parallel = { workers: count, tiles: jobs.length, physicsMs: results.reduce((s, r) => s + r.elapsedMs, 0) };
  report.options = { ...report.options, workers: count, checks: opts.checks };
  return report;
}

/** Bounded persistent pool, also used to run independent regression scenes concurrently. */
export async function runJobs(jobs: Options[], limit: number): Promise<any[]> {
  const count = Math.min(limit, availableParallelism(), jobs.length);
  const results: any[] = new Array(jobs.length);
  let next = 0;
  const workers: Worker[] = [];
  try {
    await Promise.all(Array.from({ length: count }, () => new Promise<void>((resolve, reject) => {
      const w = new Worker(new URL('./worker.ts', import.meta.url));
      workers.push(w);
      let current = -1;
      const dispatch = () => {
        if (next === jobs.length) { resolve(); return; }
        current = next++;
        w.postMessage(jobs[current]);
      };
      w.on('error', reject);
      w.on('exit', code => { if (code !== 0) reject(new Error(`validator worker exited ${code}`)); });
      w.on('message', message => {
        if (message.error) { reject(new Error(message.error)); return; }
        results[current] = message;
        dispatch();
      });
      dispatch();
    })));
  } finally { await Promise.all(workers.map(w => w.terminate())); }
  return results;
}

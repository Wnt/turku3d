// Rapier validator for .col collision tiles (ARCHITECTURE Task 1.11).
// Checks: format, seams, holes, building walkers, curb climbs, stair flights,
// the shared game car, and below-ground samples from all walker trajectories.

import { readFileSync } from 'node:fs';
import { collectColFiles, tileConsistency, TILE } from './tileChecks.ts';
import { RAPIER } from './rapier.ts';
import { MeshoptDecoder } from 'meshoptimizer';
import {
  buildTileColliders, checkShippable,
  readCol,
} from '../../../game/src/shared/col/index.ts';
import type { BuiltTile, ColTile } from '../../../game/src/shared/col/index.ts';
import { Ctx, type Loaded } from './context.ts';
import { CHECKS, type CheckName, type Options } from './options.ts';
export { CHECKS, DEFAULT_OPTIONS, type CheckName, type Options } from './options.ts';
import { loadFootprints } from './geo.ts';
import { checkSeams } from './seams.ts';
import { checkStairs } from './stairs.ts';
import { resolveInputs } from './inputs.ts';
import { checkParked, withParked } from './parked.ts';
import { CurbQueries } from '../../../game/src/vehicle/curbQueries.ts';
import { addCurbMesh, addCurbPrimitive } from '../../../game/src/vehicle/tileCurbs.ts';

type Status = 'pass' | 'fail' | 'skipped';
const r3 = (v: number) => Math.round(v * 1000) / 1000;

export async function validate(opts: Options): Promise<any> {
  opts = resolveInputs(opts);
  const files = collectColFiles(opts.inputs);
  if ((opts.workers ?? 4) > 1 && files.length > 2) {
    const { validateParallel } = await import('./parallel.ts');
    return validateParallel(opts, files);
  }
  return validateSerial(opts);
}

export async function validateSerial(opts: Options): Promise<any> {
  opts = resolveInputs(opts);
  const t0 = performance.now();
  const cpuStart = process.threadCpuUsage();
  await RAPIER.init();
  await MeshoptDecoder.ready;
  const want = new Set<CheckName>(opts.checks ?? CHECKS);
  const physics = [...want].some(c => c !== 'format' && c !== 'seams');
  const world = new RAPIER.World({ x: 0, y: -9.81, z: 0 });
  const files = collectColFiles(opts.inputs);
  const report: any = {
    validator: 'turku3d-col-validator',
    reportVersion: 2,
    rapier: RAPIER.version(),
    started: new Date().toISOString(),
    options: { ...opts, metadata: undefined, kcc: { ...opts.kcc } },
    sources: opts.metadata!.sources,
    tiles: [] as any[],
    checks: {} as Record<string, any>,
  };
  const surfaces = new CurbQueries(RAPIER, world);
  const loaded: Loaded[] = [];
  const formatErrors: string[] = [];
  for (const f of files) {
    const entry: any = { file: f };
    report.tiles.push(entry);
    let tile: ColTile;
    const buf = readFileSync(f);
    try {
      tile = readCol(buf, { decoder: MeshoptDecoder, verifyCrc: true, onWarning: (m) => formatErrors.push(`${f}: ${m}`) });
    } catch (e: any) {
      entry.error = String(e.message ?? e);
      formatErrors.push(`${f}: ${entry.error}`);
      continue;
    }
    const errs = [...checkShippable(tile), ...tileConsistency(tile)];
    const built: BuiltTile = physics ? buildTileColliders(RAPIER, world, tile) : { tileId: tile.id, colliders: [], buildMs: 0 };
    if (want.has('cars')) for (const c of built.colliders) {
      if (c.cls !== 'solid') continue;
      if (c.kind === 'trimesh') {
        const selected = new Uint32Array(c.triangleIds!.length * 3);
        c.triangleIds!.forEach((t, i) => selected.set(tile.indices.subarray(t * 3, t * 3 + 3), i * 3));
        addCurbMesh(RAPIER, surfaces, c.collider, tile.positions, selected, c.materials, tile.header.origin);
      } else addCurbPrimitive(RAPIER, surfaces, c.collider, c.materials[0]);
    }
    Object.assign(entry, {
      id: tile.id,
      bounds: [tile.header.origin[0], tile.header.origin[2], tile.header.origin[0] + TILE, tile.header.origin[2] + TILE],
      bytes: buf.byteLength,
      vertices: tile.header.vertexCount,
      triangles: tile.header.triangleCount,
      primitives: tile.primitives.length,
      flags: tile.header.flags,
      encodings: Object.fromEntries(tile.sections.map((s) => [s.tag, s.encoding])),
      buildMs: r3(built.buildMs),
      colliders: built.colliders.map((c) => `${c.kind}:${c.cls}`),
      errors: errs,
    });
    for (const e of errs) formatErrors.push(`${tile.id}: ${e}`);
    loaded.push({ file: f, bytes: buf.byteLength, tile, built });
  }
  if (!loaded.length) throw new Error(`no loadable .col tiles in ${opts.inputs.join(', ')}`);
  const ids = loaded.map((l) => l.tile.id);
  const dup = ids.filter((v, i) => ids.indexOf(v) !== i);
  if (dup.length) formatErrors.push(`duplicate tiles ${[...new Set(dup)].join(',')}`);
  world.step(); // update the broad phase so scene queries see every collider

  const ctx = new Ctx(RAPIER, world, loaded, opts);
  report.aoi = ctx.aoiDesc();

  if (want.has('format')) report.checks.format = { status: formatErrors.length ? 'fail' : 'pass', errors: formatErrors };
  if (want.has('seams')) report.checks.seams = checkSeams(loaded.map(l => l.tile), TILE);
  if (want.has('holes')) report.checks.holes = ctx.checkHoles();
  if (want.has('buildings')) report.checks.buildings = opts.metadata!.footprints.features.length ? ctx.checkBuildings(loadFootprints(opts.metadata!.footprints)) : { status: 'skipped', reason: 'no building footprints in layer outputs or --footprints' };
  if (want.has('curbs')) report.checks.curbs = ctx.checkCurbs(opts.metadata!.curbs.features.length ? opts.metadata!.curbs : undefined);
  if (want.has('stairs')) report.checks.stairs = checkStairs(ctx);
  if (want.has('cars')) report.checks.cars = ctx.checkCars(surfaces);
  if (want.has('belowGround')) report.checks.belowGround = ctx.checkBelowGround();
  if (want.has('cars')) report.checks.cars = withParked(report.checks.cars, checkParked(loaded.map(l => l.tile), opts.metadata!.parked ?? [], (x, z) => ctx.owns(x, z) && ctx.inside(x, z)));

  ctx.walker.free();
  world.free();
  const statuses = Object.values(report.checks).map((c: any) => c.status as Status);
  report.pass = !statuses.includes('fail');
  report.summary = Object.fromEntries(Object.entries(report.checks).map(([k, v]: [string, any]) => [k, v.status]));
  report.elapsedMs = Math.round(performance.now() - t0);
  const cpu = process.threadCpuUsage(cpuStart);
  report.cpuMs = (cpu.user + cpu.system) / 1000;
  return report;
}

import { test } from 'node:test';
import assert from 'node:assert/strict';
import { mkdtempSync, readFileSync, rmSync, writeFileSync, mkdirSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join, resolve } from 'node:path';
import { RAPIER as R } from '../src/rapier.ts';
import { MeshoptDecoder } from 'meshoptimizer';
import { readCol } from '../../../game/src/shared/col/index.ts';
import type { ColTile } from '../../../game/src/shared/col/index.ts';
import { checkSeams } from '../src/seams.ts';
import { CurbQueries } from '../../../game/src/vehicle/curbQueries.ts';
import { addCurbPrimitive } from '../../../game/src/vehicle/tileCurbs.ts';
import { MAT } from '../../../game/src/shared/col/materials.ts';
import { checkCars } from '../src/car.ts';
import { DEFAULT_OPTIONS, validate } from '../src/validate.ts';
import { htmlReport } from '../src/report.ts';
import { resolveInputs } from '../src/inputs.ts';

const fixture = resolve(import.meta.dirname, '../fixtures/good');
await R.init();
await MeshoptDecoder.ready;
function tile(i: number, midpoint = false, unsplit = false): ColTile {
  const x = i === 0 ? 62.5 : 0;
  return { id: `c_${i}_0`, header: { tileI: i, tileJ: 0, origin: [i * 62.5, 0, 0] },
    positions: new Float32Array([x, 0, 0, x, 0, 62.5, x + (i ? 1 : -1), 0, 30, x, 0, 30]),
    indices: new Uint32Array(midpoint ? unsplit ? [0, 1, 2, 0, 3, 2] : [0, 3, 2, 3, 1, 2] : [0, 1, 2]),
    materials: new Uint8Array([1, 1]) } as ColTile;
}

test('seams catch a height gap, a missing seam, and T junctions with identical vertex sets', () => {
  assert.equal(checkSeams([tile(0), tile(1)], 62.5).status, 'pass');
  const gap = tile(1); gap.positions[1] = 0.001;
  const r = checkSeams([tile(0), gap], 62.5);
  assert.equal(r.status, 'fail'); assert.equal(r.failures[0].coordinate, 62.5);
  assert.equal(checkSeams([tile(0), tile(1, true)], 62.5).status, 'fail');
  const t = checkSeams([tile(0, true), tile(1, true, true)], 62.5);
  assert.equal(t.status, 'fail'); assert.equal(t.failures[0].onlyA.length + t.failures[0].onlyB.length, 0);
  const missing = tile(1); missing.positions[0] += 0.01; missing.positions[3] += 0.01;
  assert.equal(checkSeams([tile(0), missing], 62.5).status, 'fail');
});

test('game car stops at a 0.1 m wall at 30 m/s; a planted non-colliding wall is caught', () => {
  for (const defect of [false, true]) {
    const world = new R.World({ x: 0, y: -9.81, z: 0 });
    world.createCollider(R.ColliderDesc.cuboid(40, 0.1, 10).setTranslation(0, -0.1, 0));
    world.createCollider(R.ColliderDesc.cuboid(0.05, 2, 5).setTranslation(0, 2, 0).setSensor(defect));
    world.step();
    const barrier = { source: 'thin-wall', x: 0, z: 0, nx: -1, nz: 0, low: 0, high: 4, step: 4 };
    const result = checkCars(R, world, [], [barrier], () => 0, new CurbQueries(R, world));
    assert.equal(result.status, defect ? 'fail' : 'pass', JSON.stringify(result));
    assert.equal(result.runs[0].speed, 30);
    world.free();
  }
});

test('W1-07 meets every ARCHITECTURE 1.14 curb response threshold through the validator', () => {
  for (const height of [0.07, 0.12, 0.15, 0.26, 0.3, 0.4]) {
    const world = new R.World({ x: 0, y: -9.81, z: 0 });
    world.createCollider(R.ColliderDesc.cuboid(40, 0.1, 10).setTranslation(0, -0.1, 0));
    const curb = world.createCollider(R.ColliderDesc.cuboid(15, height / 2, 10).setTranslation(15, height / 2, 0));
    const surfaces = new CurbQueries(R, world);
    addCurbPrimitive(R, surfaces, curb, MAT.curb);
    world.step();
    const result = checkCars(R, world, [{ source: 'curb', x: 0, z: 0, nx: -1, nz: 0, low: 0, high: height, step: height }], [], () => 0, surfaces);
    assert.equal(result.status, 'pass', JSON.stringify(result));
    assert.deepEqual(result.runs.map(r => r.speed), height > 0.25 ? [3, 6, 10, 2] : [3, 6, 10]);
    if (height <= 0.15) for (const run of result.runs) {
      assert.ok(run.responseCriteriaApplicable && run.pitchSpikeDeg >= 2 && run.speedLoss >= 0.05 && run.speedLoss <= 0.2);
      console.log(`validator height=${height} speed=${run.speed} pitch=${run.pitchSpikeDeg.toFixed(2)} loss=${(run.speedLoss * 100).toFixed(2)}%`);
    }
    world.free();
  }
});

test('a missing curb-strike registration fails both response and low-speed blocking', () => {
  for (const height of [0.12, 0.3]) {
    const world = new R.World({ x: 0, y: -9.81, z: 0 });
    world.createCollider(R.ColliderDesc.cuboid(30, 0.1, 10).setTranslation(0, -0.1, 0));
    world.createCollider(R.ColliderDesc.cuboid(10, height / 2, 10).setTranslation(10, height / 2, 0));
    world.step();
    const r = checkCars(R, world, [{ source: 'unregistered-curb', x: 0, z: 0, nx: -1, nz: 0, low: 0, high: height, step: height }], [], () => 0, new CurbQueries(R, world));
    assert.equal(r.status, 'fail', JSON.stringify(r));
    world.free();
  }
});

test('planted 30 cm collision curb fails its declared 25 cm layer contract', async () => {
  const root = resolve(fixture, '../acceptance');
  const r = await validate({ ...DEFAULT_OPTIONS, inputs: [join(root, 'curb30.col')], curbs: join(root, 'curbs.geojson'), checks: ['curbs'], workers: 1 });
  assert.equal(r.checks.curbs.status, 'fail');
  assert.ok(r.checks.curbs.failures.some((f: any) => Math.abs(f.step - 0.30) < 0.001));
});

test('layer manifests resolve footprints, curbs and stair flights; malformed lists fail', () => {
  const dir = mkdtempSync(join(tmpdir(), 'validator-layers-'));
  try {
    mkdirSync(join(dir, 'structures'));
    writeFileSync(join(dir, 'structures/MANIFEST.json'), JSON.stringify({ validation: { stairs: 'flights.json' } }));
    writeFileSync(join(dir, 'structures/flights.json'), JSON.stringify([{ id: 'steps', bottom: [0, 0, 0], top: [3, 1, 0], width: 2 }]));
    assert.equal(resolveInputs({ ...DEFAULT_OPTIONS, inputs: [], layers: dir }).metadata!.stairs.length, 1);
    writeFileSync(join(dir, 'structures/flights.json'), '{}');
    assert.throws(() => resolveInputs({ ...DEFAULT_OPTIONS, inputs: [], layers: dir }), /stair flight array/);
  } finally { rmSync(dir, { recursive: true, force: true }); }
});

test('HTML escapes metadata and puts failure coordinates on an embedded ortho', () => {
  const dir = mkdtempSync(join(tmpdir(), 'validator-map-'));
  try {
    const png = join(dir, 'ortho.png'); writeFileSync(png, Buffer.from('iVBORw0KGgo=', 'base64'));
    const html = htmlReport({ pass: false, tiles: [], elapsedMs: 1, summary: {}, aoi: { bbox: [0, 0, 10, 10] }, checks: { holes: { status: 'fail', missSamples: [[3, 4]], reason: '</pre><script>alert(1)</script>' } } }, png, [0, 0, 10, 10]);
    assert.match(html, /cx="3" cy="4"/); assert.match(html, /data:image\/png;base64/); assert.ok(!html.includes('<script>'));
  } finally { rmSync(dir, { recursive: true, force: true }); }
});

test('shipped fixture seams are checked without rounded coordinates', () => {
  const tiles = ['c_1_2', 'c_2_2'].map(id => readCol(readFileSync(join(fixture, id + '.col')), { decoder: MeshoptDecoder }));
  assert.equal(checkSeams(tiles, 62.5).status, 'pass');
});


test('parallel jobs cover each collision tile exactly once and preserve seam and hole checks', async () => {
  const inputs = [resolve(fixture, '../acceptance/flat')];
  const options = { ...DEFAULT_OPTIONS, inputs, rayDensity: 0.02, checks: ['format', 'seams', 'holes'] as const };
  const serial = await validate({ ...options, checks: [...options.checks], workers: 1 });
  const parallel = await validate({ ...options, checks: [...options.checks], workers: 2 });
  assert.equal(serial.pass, true); assert.equal(parallel.pass, true);
  assert.equal(parallel.parallel.workers, 2);
  assert.equal(parallel.checks.seams.sharedEdges, 4);
  assert.equal(new Set(parallel.checks.holes.perTile.map((r: any) => r.tile)).size, 4);
  assert.ok(parallel.checks.holes.perTile.every((r: any) => r.rays > 0 && r.misses === 0));
});


test('layer stair routes fail when their collider is absent, and roofless buildings cannot pass', async () => {
  const metadata = { sources: [], curbs: { features: [] },
    stairs: [{ id: 'missing-ramp', bottom: [20, 2, 20], top: [20, 3, 24], width: 2 }],
    footprints: { features: [{ id: 'missing-shell', geometry: { type: 'Polygon', coordinates: [[[35, 35], [45, 35], [45, 45], [35, 45], [35, 35]]] } }] } };
  const r = await validate({ ...DEFAULT_OPTIONS, metadata, walkers: 12,
    inputs: [resolve(fixture, '../acceptance/flat/c_0_0.col')], checks: ['stairs', 'buildings'] });
  assert.equal(r.checks.stairs.status, 'fail');
  assert.equal(r.checks.stairs.flights[0].id, 'missing-ramp');
  assert.equal(r.checks.buildings.status, 'fail');
  assert.ok(r.checks.buildings.insideAnyFrame > 0);
});

test('a failing worker makes the aggregate report fail with owned-tile coordinates', async () => {
  const root = resolve(fixture, '../acceptance');
  const inputs = [join(root, 'hole/c_0_0.col'), ...['c_0_1', 'c_1_0', 'c_1_1'].map(id => join(root, 'flat', id + '.col'))];
  const r = await validate({ ...DEFAULT_OPTIONS, inputs, workers: 2, checks: ['holes'], rayDensity: 0.1 });
  assert.equal(r.pass, false);
  const bad = r.checks.holes.perTile.filter((t: any) => t.status === 'fail');
  assert.deepEqual(bad.map((t: any) => t.tile), ['c_0_0']);
  assert.ok(bad[0].missSamples.length);
});


test('a declared curb entirely absent from collision fails, even on otherwise valid flat ground', async () => {
  const root = resolve(fixture, '../acceptance');
  const r = await validate({ ...DEFAULT_OPTIONS, inputs: [join(root, 'flat/c_0_0.col')],
    curbs: join(root, 'curbs.geojson'), checks: ['curbs'] });
  assert.equal(r.checks.curbs.status, 'fail');
  assert.ok(r.checks.curbs.failures.length);
});


test('decoded .col curb faces use the same query adapter as PhysicsWorld', async () => {
  const r = await validate({ ...DEFAULT_OPTIONS, inputs: [resolve(fixture, '../acceptance/curb12.col')], checks: ['cars'] });
  assert.equal(r.checks.cars.status, 'pass', JSON.stringify(r.checks.cars));
  assert.equal(r.checks.cars.runs.length, 12);
  assert.ok(r.checks.cars.runs.every((run: any) => run.responseCriteriaApplicable && run.strikes > 0));
});

// Synthetic fixtures (python -m turku3d.col.fixtures) must pass/fail exactly as fixtures/expected.json says:
// 12 cm and 25 cm steps are climbed, 40 cm blocks, buildings are impenetrable, and each negative
// fixture fails the check it was built to break.
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { spawnSync } from 'node:child_process';
import { Worker } from 'node:worker_threads';
import { readFileSync } from 'node:fs';
import { join, resolve } from 'node:path';
import { DEFAULT_OPTIONS, validate } from '../src/validate.ts';

const HERE = resolve(import.meta.dirname, '..');
const FIX = join(HERE, 'fixtures');
const expected = JSON.parse(readFileSync(join(FIX, 'expected.json'), 'utf8'));
const WALKERS = Number(process.env.FIXTURE_WALKERS ?? 150);

// Small deterministic gate samples; the standalone suite defaults to 150 and tile-validate
// retains its 2,000 walkers. Every fixture expectation and detailed assertion runs in both.
const reports: Record<string, Promise<any>> = {};
const names = Object.keys(expected);
let next = 0;
async function fill() {
  while (next < names.length) {
    const name = names[next++];
    const result = new Promise<any>((resolve, reject) => {
      const worker = new Worker(new URL('./fixture-worker.ts', import.meta.url), {
        workerData: { name, directory: FIX, walkers: WALKERS, checks: Object.keys(expected[name].checks) },
      });
      worker.once('message', resolve);
      worker.once('error', reject);
      worker.once('exit', code => { if (code) reject(new Error(`fixture ${name}: worker exit ${code}`)); });
    });
    reports[name] = result;
    await result;
  }
}
await Promise.all([fill(), fill()]);
async function run(name: string) {
  return reports[name];
}

for (const [name, exp] of Object.entries(expected) as [string, any][]) {
  test(`fixture ${name}: ${JSON.stringify(exp.checks)}`, async () => {
    const r = await run(name);
    for (const [check, status] of Object.entries(exp.checks)) assert.equal(r.checks[check].status, status, `${name}.${check}: ${JSON.stringify(r.checks[check]).slice(0, 600)}`);
    assert.equal(r.pass, exp.pass);
  });
}

test('good fixture: per-step outcomes (12 cm climb, 25 cm climb, 40 cm block) at every speed', async () => {
  const r = await run('good');
  const bs = r.checks.curbs.byStep;
  for (const [step, want] of [['0.12', 'climbed'], ['0.25', 'climbed'], ['0.40', 'blocked']]) {
    assert.ok(bs[step], `no stations with step ${step}: ${Object.keys(bs)}`);
    assert.deepEqual(Object.keys(bs[step].outcomes), [want], `step ${step}: ${JSON.stringify(bs[step])}`);
    assert.ok(bs[step].stations >= 10);
  }
  assert.equal(r.checks.buildings.insideAnyFrame, 0);
  assert.ok(r.checks.buildings.walkers >= WALKERS * 0.9);
  assert.equal(r.checks.stairs.count, 1);
  assert.equal(r.checks.stairs.flights[0].up.result, 'ok');
  assert.equal(r.checks.stairs.flights[0].down.result, 'ok');
  assert.ok(r.checks.holes.rays > 15000);
  // the query-only stair treads and the water sensor must have been built as non-solid colliders
  assert.ok(r.tiles[0].colliders.includes('trimesh:query'));
  assert.ok(r.tiles[0].colliders.includes('trimesh:sensor'));
});

test('good fixture via --curbs lines gives the same step outcomes', async () => {
  const r = await validate({ ...DEFAULT_OPTIONS, inputs: [join(FIX, 'good')], curbs: join(FIX, 'good', 'curbs.geojson'), checks: ['curbs'] });
  assert.equal(r.checks.curbs.status, 'pass');
  assert.equal(r.checks.curbs.mode, 'lines');
  assert.deepEqual(Object.keys(r.checks.curbs.byStep).sort(), ['0.12', '0.25', '0.40']);
});

test('negative fixtures fail for the intended reason', async () => {
  assert.ok((await run('neg_hole')).checks.holes.misses > 10);
  assert.ok((await run('neg_open_building')).checks.buildings.perFootprint.A > 0);
  const st = (await run('neg_steep_stair')).checks.stairs.flights[0];
  assert.ok(st.slopeDeg > 50 && st.slopeFlag && st.up.result === 'stuck');
  assert.match((await run('neg_material0')).checks.format.errors.join(' '), /unshippable material ids 0/);
});

test('CLI exit codes: 0 pass, 1 fail, 2 usage', () => {
  const cli = join(HERE, 'src/cli.ts');
  const ok = spawnSync('node', [cli, '--quiet', '--checks', 'format,holes', join(FIX, 'good')]);
  assert.equal(ok.status, 0, String(ok.stderr));
  const bad = spawnSync('node', [cli, '--quiet', '--checks', 'format', join(FIX, 'neg_material0')]);
  assert.equal(bad.status, 1);
  const usage = spawnSync('node', [cli]);
  assert.equal(usage.status, 2);
  const req = spawnSync('node', [cli, '--quiet', '--checks', 'format,buildings', '--require', 'buildings', join(FIX, 'good')]);
  assert.equal(req.status, 1, 'required check skipped must fail');
});

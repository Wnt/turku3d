// Draft (coarse) tiles on the synthetic dev tiles (x 0..250, z 0..250, ground H 2), against a routed fake of the
// tools/dev/coarse-tiles service: t0_1_0 east of the dev tiles is a good draft (16 flat .col cells at H 2), t0_0_-1
// north of them lacks one collision cell, every other cell answers "no coverage". The character walks onto the good
// draft and stands on it; the rejected draft is never drawn and the edge wall stops the walk north into it at z 0.
// Screenshots: $TURKU3D_WORK/reports/fix-coarse-collision-and-map/.
import { expect, installPad, isolatedTest as test, type Page } from './fixtures';
import { frames, untilSim } from './waits';
import fs from 'node:fs';
import path from 'node:path';
import { Document, NodeIO } from '@gltf-transform/core';
import { writeCol } from '../../scripts/colWriter.mjs';
import { reportsRoot } from '../../../tools/dev/environment.mjs';

const SHOTS = path.join(reportsRoot, 'fix-coarse-collision-and-map');
const G = 2, COL = 62.5, L0 = 250, N = 4;
const shot = async (page: Page, name: string) => page.screenshot({ path: path.join(SHOTS, `${test.info().project.name}-${name}.png`) });

/** One flat collision cell at H 2: an N x N grid of road triangles. */
function flatCol(i: number, j: number) {
  const pos: number[] = [], idx: number[] = [];
  const s = COL / N;
  for (let b = 0; b <= N; b++) for (let a = 0; a <= N; a++) pos.push(a * s, G, b * s);
  for (let b = 0; b < N; b++) for (let a = 0; a < N; a++) {
    const v = b * (N + 1) + a;
    idx.push(v, v + N + 1, v + 1, v + 1, v + N + 1, v + N + 2);
  }
  return writeCol({ i, j, positions: new Float32Array(pos), indices: new Uint32Array(idx), materials: new Uint8Array(idx.length / 3).fill(1),
    prims: [], flags: 0, meta: { tile: `c_${i}_${j}`, code: 'e2e-draft' } });
}

/** The draft's visual: one green 250 m quad at H 2 under a node translated to the tile origin. */
async function flatGlb(ti: number, tj: number) {
  const doc = new Document();
  const buffer = doc.createBuffer();
  const position = doc.createAccessor().setType('VEC3').setBuffer(buffer)
    .setArray(new Float32Array([0, G, 0, L0, G, 0, L0, G, L0, 0, G, L0]));
  const indices = doc.createAccessor().setType('SCALAR').setBuffer(buffer).setArray(new Uint32Array([0, 3, 1, 1, 3, 2]));
  const material = doc.createMaterial('draft').setBaseColorFactor([0.25, 0.6, 0.3, 1]).setRoughnessFactor(1);
  const mesh = doc.createMesh('ground').addPrimitive(doc.createPrimitive().setAttribute('POSITION', position).setIndices(indices).setMaterial(material));
  const node = doc.createNode('tile').setMesh(mesh).setTranslation([ti * L0, 0, tj * L0]);
  doc.createScene().addChild(node);
  return Buffer.from(await new NodeIO().writeBinary(doc));
}

const cells = (ti: number, tj: number) => Array.from({ length: 16 }, (_, k) => [ti * 4 + (k % 4), tj * 4 + Math.floor(k / 4)] as const);

async function routeService(page: Page) {
  const drafts: Record<string, { cells: (readonly [number, number])[]; glb: Buffer }> = {
    t0_1_0: { cells: cells(1, 0), glb: await flatGlb(1, 0) },
    t0_0_m1: { cells: cells(0, -1).slice(1), glb: await flatGlb(0, -1) },
  };
  const files = new Map<string, Buffer>();
  const fragments = new Map<string, unknown>();
  for (const [key, d] of Object.entries(drafts)) {
    const id = key.replace('_m', '_-');
    files.set(`/coarse/${id}/vis.glb`, d.glb);
    const collision: Record<string, unknown> = {};
    for (const [i, j] of d.cells) {
      const buf = flatCol(i, j);
      files.set(`/coarse/${id}/col/c_${i}_${j}.col`, buf);
      collision[`c_${i}_${j}`] = { url: `col/c_${i}_${j}.col`, sha256: '', bytes: buf.length, tris: 2 * N * N, prims: 0, h: [G, G], flags: 0 };
    }
    fragments.set(id, { format: 'turku3d-coarse-tile', version: 1, coarse: true, tile: id,
      visual: { [id]: { url: 'vis.glb', sha256: '', bytes: d.glb.length, level: 0, tris: 2, h: [G, G], coarse: true } }, collision });
  }
  await page.route(/\/coarse\//, route => {
    const url = new URL(route.request().url());
    const json = (status: number, body: unknown) => route.fulfill({ status, contentType: 'application/json', body: JSON.stringify(body) });
    if (url.pathname === '/coarse/status') return json(200, { state: 'idle' });
    const m = /^\/coarse\/(t0_-?\d+_-?\d+)\/manifest\.json$/.exec(url.pathname);
    if (m) return fragments.has(m[1]) ? json(200, fragments.get(m[1])) : json(404, { state: 'failed', error: 'no coverage' });
    const file = files.get(url.pathname);
    return file ? route.fulfill({ status: 200, contentType: 'application/octet-stream', body: file }) : json(404, { error: 'not found' });
  });
}

test('a draft tile with collision is drawn and stood on; one without full collision is never drawn and walled off', { tag: ['@walk', '@streaming', '@gameplay'] }, async ({ page }, info) => {
  const errors: string[] = [];
  page.on('pageerror', e => errors.push(e.message));
  // The fake service answers 404 for cells without coverage, as the real one does; the browser logs each.
  page.on('console', m => { if (m.type() === 'error' && !/status of 404/.test(m.text())) errors.push(m.text()); });
  await routeService(page);
  await installPad(page);
  await page.goto(`/?backend=${info.project.name}&spawn=23459740,6704470`); // x 240, z 30: 10 m from the east edge
  await page.getByRole('option', { name: 'New game' }).click();
  await page.waitForFunction(() => { const g = (window as any).__game; return g?.ready && g.state().grounded && g.state().tilesSettled; });
  fs.mkdirSync(SHOTS, { recursive: true });

  const drafts = () => page.evaluate(() => {
    const coarse = (window as any).__game.coarse;
    const e = (id: string) => { const t = coarse.entries.get(id); return t && { state: t.state, error: t.error ?? null, drawn: !!t.model?.parent }; };
    return { good: e('t0_1_0'), broken: e('t0_0_-1'), status: coarse.status as string };
  });
  await untilSim(page, () => { const c = (window as any).__game.coarse; return c.entries.get('t0_1_0')?.state === 'draft' && c.entries.get('t0_0_-1')?.state === 'failed'; }, null, 20);
  let d = await drafts();
  expect(d.good).toEqual({ state: 'draft', error: null, drawn: true });
  expect(d.broken).toEqual({ state: 'failed', error: 'no collision for 1 of 16 cells (c_0_-4)', drawn: false });

  const walk = async (yaw: number, until: (s: any) => boolean, seconds: number) => {
    await page.evaluate(y => { (window as any).__game.orbit.yaw = y; (window as any).__virtualPad.axes = [0, -1, 0, 0]; }, yaw);
    await untilSim(page, until as never, null, seconds).catch(() => {});
    await page.evaluate(() => { (window as any).__virtualPad.axes = [0, 0, 0, 0]; });
    await frames(page, 30);
    return page.evaluate(() => (window as any).__game.state());
  };
  // North: t0_0_-1's draft was rejected, so the edge wall at z 0 stops the walk on solid ground.
  let s = await walk(0, () => (window as any).__game.state().feet[2] < -5, 10);
  expect(s.feet[2]).toBeGreaterThan(-1.5);
  expect(s.feet[2]).toBeLessThan(2);
  expect(s.feet[1]).toBeCloseTo(G, 1);
  expect(s.grounded).toBe(true);
  expect(s.recovery.lost).toBeNull();
  await shot(page, '01-wall-at-rejected-draft');
  expect((await drafts()).broken.drawn).toBe(false);

  // East onto the good draft: the character stands on its collision at H 2.
  s = await walk(-Math.PI / 2, () => (window as any).__game.state().feet[0] > 270, 15);
  expect(s.feet[0]).toBeGreaterThan(270);
  expect(s.feet[1]).toBeCloseTo(G, 1);
  expect(s.grounded).toBe(true);
  expect(s.recovery.lost).toBeNull();
  d = await drafts();
  expect(d.status).toMatch(/^draft tile t0_1_0/);
  await page.evaluate(() => { (window as any).__game.orbit.yaw = Math.PI / 2; });
  await frames(page, 30);
  await shot(page, '02-standing-on-draft');
  expect(errors).toEqual([]);
});

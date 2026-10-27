// Draft (coarse) tiles on the synthetic dev tiles (x 0..250, z 0..250, ground H 2), against a routed fake of the
// tools/dev/coarse-tiles service: t0_1_0 east of the dev tiles is a good draft (16 flat .col cells at H 2), t0_0_-1
// north of them lacks one collision cell, every other cell answers "no coverage". The character walks onto the good
// draft and stands on it; the rejected draft is never drawn and the edge wall stops the walk north into it at z 0.
// The good draft's street graph joins the minimap and the router.
// Screenshots: $TURKU3D_WORK/reports/fix-coarse-collision-and-map/.
import { expect, installPad, isolatedTest as test, type Page } from './fixtures';
import { frames, untilSim } from './waits';
import { G, cells, routeCoarse } from './coarseFake';
import fs from 'node:fs';
import path from 'node:path';
import { reportsRoot } from '../../../tools/dev/environment.mjs';

const SHOTS = path.join(reportsRoot, 'fix-coarse-collision-and-map');
const shot = async (page: Page, name: string) => page.screenshot({ path: path.join(SHOTS, `${test.info().project.name}-${name}.png`) });

async function routeService(page: Page) {
  // t0_1_0 carries a street graph: an east-west street at z 30 with a side street south at x 400.
  await routeCoarse(page, {
    't0_1_0': { cells: cells(1, 0), roads: { nodes: 4, edges: 3, json: { format: 'turku3d-roadgraph', version: 1, frame: 'engine-xz',
      area: 'coarse-t0_1_0', bbox: [250, 0, 500, 250], nodes: [250, 30, 400, 30, 500, 30, 400, 200],
      edges: [[0, 1, 19, 150], [1, 2, 19, 100], [1, 3, 19, 170]] } } },
    't0_0_-1': { cells: cells(0, -1).slice(1) },
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
  await page.getByRole('button', { name: 'Skip' }).click();
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

  // The draft's streets joined the map (the dev tiles publish none) and the router routes on them.
  await untilSim(page, () => (window as any).__game.minimap.snapshot().streets === 3, null, 5);
  const route = await page.evaluate(() => (window as any).__game.router.route({ x: 260, z: 35 }, { x: 405, z: 190 }));
  expect(route.map((p: { x: number; z: number }) => [Math.round(p.x), Math.round(p.z)])).toEqual([[260, 30], [400, 30], [400, 190]]);
  await page.evaluate(() => (window as any).__game.minimap.toggleExpanded());
  await frames(page, 10);
  await shot(page, '03-map-with-draft-streets');
  expect(errors).toEqual([]);
});

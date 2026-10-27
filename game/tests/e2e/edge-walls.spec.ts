// The edge of the world at full speed, on the synthetic dev tiles (x 0..250, z 0..250, ground H 2) with a routed fake
// coarse service: t0_1_0 east of them is a good draft, every other tile has no coverage. With a 60 m collision radius
// the car drives east at full throttle from x 30: the collision ahead streams in along the heading, no wall stands at
// the edge of the loaded collision or at the dev tiles' edge (x 250), and the car stops only at the true edge, x 500
// (no collision in any source beyond), where the striped edge marker stands. Far LOD outside the built tiles would be
// grey; the dev tiles have none. Screenshots: $TURKU3D_WORK/reports/fix-edge-walls-streaming/.
import { expect, installPad, isolatedTest as test, padButton, type Page } from './fixtures';
import { frames, untilSim } from './waits';
import { G, cells, routeCoarse } from './coarseFake';
import fs from 'node:fs';
import path from 'node:path';
import { reportsRoot } from '../../../tools/dev/environment.mjs';

const SHOTS = path.join(reportsRoot, 'fix-edge-walls-streaming');
const shot = async (page: Page, name: string) => page.screenshot({ path: path.join(SHOTS, `${test.info().project.name}-${name}.png`) });

test('a car at full throttle meets no wall until the true edge of the collision, which is marked', { tag: ['@drive', '@streaming', '@gameplay'] }, async ({ page }, info) => {
  const errors: string[] = [];
  page.on('pageerror', e => errors.push(e.message));
  page.on('console', m => { if (m.type() === 'error' && !/status of 404/.test(m.text())) errors.push(m.text()); });
  await routeCoarse(page, { t0_1_0: { cells: cells(1, 0) } });
  await installPad(page);
  // x 30, z 30 facing east; the debug spawn parks the car 6 m south of the player, facing east.
  await page.goto(`/?backend=${info.project.name}&spawn=23459530,6704470,90&colRadius=60`);
  await page.getByRole('option', { name: 'New game' }).click();
  await page.locator('#intro-dialog').getByRole('button', { name: 'Skip' }).click();
  await page.waitForFunction(() => { const g = (window as any).__game; return g?.ready && g.state().grounded && g.state().tilesSettled; });
  await untilSim(page, () => (window as any).__game.coarse.entries.get('t0_1_0')?.state === 'draft', null, 20);
  fs.mkdirSync(SHOTS, { recursive: true });

  // Into the car: stand next to it and press Y.
  await page.evaluate(() => {
    const g = (window as any).__game, car = g.state().vehicle.pos;
    g.physics.setCharacterEnabled(true, { x: car[0], y: car[1] + 0.5, z: car[2] - 3 });
  });
  await frames(page, 20);
  await padButton(page, 3, 1);
  await untilSim(page, () => (window as any).__game.state().mode === 'car', null, 5);
  await padButton(page, 3, 0);

  // Full throttle east; the page records the drive every frame.
  await page.evaluate(() => {
    const w = window as any, rows: number[][] = w.__edgeDrive = [];
    const sample = () => {
      const s = w.__game.state();
      rows.push([s.vehicle.pos[0], s.vehicle.pos[1], s.vehicle.speed]);
      if (rows.length < 20_000) requestAnimationFrame(sample);
    };
    requestAnimationFrame(sample);
  });
  await padButton(page, 7, 1);
  // Until it stops at the true edge (or 60 simulated s pass).
  await untilSim(page, () => {
    const rows = (window as any).__edgeDrive as number[][];
    const last = rows.at(-1);
    return !!last && last[0] > 400 && rows.slice(-30).every(r => Math.abs(r[2]) < 0.5);
  }, null, 60);
  await padButton(page, 7, 0);
  const drive = await page.evaluate(() => (window as any).__edgeDrive as number[][]);
  const top = Math.max(...drive.map(r => r[2]));
  const end = drive.at(-1)!;
  // It drove at speed (the start car tops out at 27 m/s and reaches about 24 m/s in 470 m), and nothing slowed it between x 150 and the
  // last 30 m before the edge.
  console.log(`[edge-walls] top ${top.toFixed(1)} m/s, stopped at x ${end[0].toFixed(2)}`);
  expect(top).toBeGreaterThan(20);
  const cruise = drive.filter(r => r[0] > 150 && r[0] < 470);
  expect(cruise.length).toBeGreaterThan(10);
  expect(Math.min(...cruise.map(r => r[2]))).toBeGreaterThan(12);
  // It stopped at the true edge, on the ground.
  expect(end[0]).toBeGreaterThan(490);
  expect(end[0]).toBeLessThan(500);
  expect(Math.min(...drive.map(r => r[1]))).toBeGreaterThan(G);
  const s = await page.evaluate(() => (window as any).__game.state());
  expect(s.recovery.lost).toBeNull();

  // The edge is marked where the wall stands: the east sides of the draft's last column (i 7), and only true edges.
  const marker = await page.evaluate(() => {
    const g = (window as any).__game;
    return { edges: g.streamer.trueEdges() as number[][], count: g.edgeMarker.count as number };
  });
  expect(marker.edges).toContainEqual([7, 0, 0]);
  expect(marker.edges.every(([i, j, side]) => {
    const [di, dj] = [[1, 0], [-1, 0], [0, 1], [0, -1]][side];
    return !(i + di >= 0 && i + di < 8 && j + dj >= 0 && j + dj < 4);
  })).toBe(true);
  await untilSim(page, () => (window as any).__game.edgeMarker.count >= (window as any).__game.streamer.trueEdges().length, null, 5);
  await frames(page, 30);
  await shot(page, 'edge-marker');
  expect(errors).toEqual([]);
});

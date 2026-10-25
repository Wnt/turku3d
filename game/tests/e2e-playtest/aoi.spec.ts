import type { Page } from '@playwright/test';
import { test, expect, installPad, padAxes, padButton, gameState } from '../e2e/fixtures';
import { OFFICE_EN, engineToEN, VISUAL_L0 } from '../../src/core/origin';
import type { RouteSample } from '../e2e/fixtures';

async function start(page: Page, backend: string, E = OFFICE_EN.E as number, N = OFFICE_EN.N as number, heading = 0) {
  await installPad(page);
  await page.goto(`/?backend=${backend}&tiles=/tiles/tileset.json&spawn=${E},${N},${heading}`);
  await expect(page.getByRole('button', { name: 'Start playing' })).toBeEnabled();
  await expect(page.locator('#gamepad-status')).toHaveText('Xbox Wireless Controller connected');
  expect(await page.evaluate(() => window.__game!.backend)).toBe(backend === 'webgpu' ? 'WebGPU' : 'WebGL2');
  await page.getByRole('button', { name: 'Start playing' }).click();
  await page.waitForFunction(() => { const s = (window.__game!.state as () => any)(); return s.grounded && s.tilesSettled; });
}
async function safety(page: Page, maxSpeed: number) {
  const samples = await page.evaluate(() => window.__routeSamples);
  expect(samples.length).toBeGreaterThan(30);
  expect(samples.filter(s => !s.feet.every(Number.isFinite) || !(s.speed < maxSpeed)), 'Finite positions and speed bounds on every frame').toEqual([]);
  const checked = samples.filter(s => s.groundChecked);
  expect(checked.every(s => s.ground !== null), 'Every sampled ground ray must hit terrain').toBe(true);
  const hits = samples.filter((s): s is RouteSample & { ground: number } => s.ground !== null);
  expect(hits.length).toBeGreaterThan(10);
  expect(hits.filter(s => !(s.feet[1] - s.ground > -0.15 && s.feet[1] - s.ground < 1.2)),
    'Every sampled position must remain above and near the terrain').toEqual([]);
}

test('AOI terrain: virtual Xbox walks a loop across the four visual tile seams', { tag: ['@walk', '@ground', '@streaming', '@character', '@aoi'] }, async ({ page }, info) => {
  // The four-tile junction is an explicitly limited terrain route, not a surveyed street/block route.
  const en = engineToEN(VISUAL_L0 + 6, 0, VISUAL_L0 - 6);
  await start(page, info.project.name, en.E, en.N);
  const visited = new Set<string>();
  const origin = (await gameState(page)).feet;
  for (const [x, y, axis, direction] of [[0, 1, 2, 1], [-1, 0, 0, -1], [0, -1, 2, -1], [1, 0, 0, 1]]) {
    const from = (await gameState(page)).feet[axis];
    await padAxes(page, x, y);
    await page.waitForFunction(({ axis, direction, from }) => {
      if (direction * ((window.__game!.state as () => any)().feet[axis] - from) < 12) return false;
      // Release on this frame; a second protocol round trip can overshoot under host load.
      window.__virtualPad.axes = [0, 0, 0, 0];
      return true;
    }, { axis, direction, from });
    await expect.poll(async () => Math.hypot(...(await gameState(page)).vel)).toBeLessThan(0.15);
    const s = await gameState(page);
    visited.add(`t0_${Math.floor(s.feet[0] / VISUAL_L0)}_${Math.floor(s.feet[2] / VISUAL_L0)}`);
    expect(s.grounded).toBe(true);
  }
  expect([...visited].sort()).toEqual(['t0_0_0', 't0_0_1', 't0_1_0', 't0_1_1']);
  const end = await gameState(page);
  expect(Math.hypot(end.feet[0] - origin[0], end.feet[2] - origin[2])).toBeLessThan(1);
  expect(end.collision.tiles).toBeGreaterThanOrEqual(16);
  await safety(page, 4);
});

// Surveyed facade normals, with each start six metres outside a distinct real footprint.
const approaches = [
  { id: '103295893K', x: 174.9, z: 256.78, heading: 330.9 },
  { id: '103295888D', x: 199.4, z: 243.0, heading: 330.5 },
  { id: '103295899S', x: 146.13, z: 246.94, heading: 235.1 },
  { id: '103295889E', x: 216.58, z: 233.72, heading: 328.2 },
  { id: '1032958849', x: 233.81, z: 224.47, heading: 331.4 },
];
for (const approach of approaches) {
  test(`AOI building ${approach.id}: Xbox walks into the facade and is blocked`, { tag: ['@walk', '@character', '@buildings', '@aoi'] }, async ({ page }, info) => {
    const manifest = await (await page.request.get('/tiles/manifest.json')).json();
    test.skip(!manifest.spawn?.building_id, 'Assembled building layer is not available in the stub AOI');
    const en = engineToEN(approach.x, 0, approach.z);
    await start(page, info.project.name, en.E, en.N, approach.heading);
    const from = (await gameState(page)).feet;
    await padAxes(page, 0, -1);
    await page.waitForTimeout(4_000);
    const blocked = await gameState(page);
    await padAxes(page, 0, 0);
    const travel = Math.hypot(blocked.feet[0] - from[0], blocked.feet[2] - from[2]);
    expect(travel, `${approach.id}: approach must reach its facade`).toBeGreaterThan(0.7);
    expect(travel, `${approach.id}: building wall must stop the character`).toBeLessThan(6.2);
    expect(blocked.grounded, `${approach.id}: character must stay grounded`).toBe(true);
    await safety(page, 4);
  });
}

test('AOI office: Xbox enters the parked car, drives, brakes and exits', { tag: ['@drive', '@vehicle', '@ground', '@input', '@aoi'] }, async ({ page }, info) => {
  // Static parked cars occupy the northbound lane; drive south through the clear parking strip.
  const en = engineToEN(224, 0, 181);
  await start(page, info.project.name, en.E, en.N, 180);
  await padAxes(page, 1, 0);
  await page.waitForFunction(() => {
    const s = (window.__game!.state as () => any)();
    return Math.hypot(s.feet[0] - s.vehicle.pos[0], s.feet[2] - s.vehicle.pos[2]) < 3.2;
  }, null, { timeout: 4_000 });
  await padAxes(page, 0, 0); await padButton(page, 3, 1); await padButton(page, 3, 0);
  await expect.poll(async () => (await gameState(page)).mode).toBe('car');
  const origin = (await gameState(page)).vehicle!.pos;
  await padButton(page, 7, 0.7);
  await page.waitForFunction(origin => (window.__game!.state as () => any)().vehicle.pos[2] - origin[2] > 15, origin, { timeout: 8_000 });
  const driving = await gameState(page);
  expect(driving.vehicle!.speed).toBeGreaterThan(3);
  expect(Math.abs(driving.vehicle!.pos[0] - origin[0])).toBeLessThan(3);
  await padButton(page, 7, 0); await padButton(page, 5, 1);
  await expect.poll(async () => (await gameState(page)).vehicle!.speed).toBeLessThan(0.2);
  await padButton(page, 5, 0); await padButton(page, 3, 1); await padButton(page, 3, 0);
  await expect.poll(async () => (await gameState(page)).mode).toBe('foot');
  await page.waitForFunction(() => (window.__game!.state as () => any)().grounded);
  await safety(page, 20);
});

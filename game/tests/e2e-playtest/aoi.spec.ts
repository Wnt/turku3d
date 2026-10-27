import type { Page, TestInfo } from '@playwright/test';
import { test, expect, installPad, padAxes, padButton, gameState } from '../e2e/fixtures';
import { OFFICE_EN, engineToEN, VISUAL_L0 } from '../../src/core/origin';
import type { PhysicsClient } from '../../src/physics/client';
import type { RouteSample } from '../e2e/fixtures';

async function start(page: Page, backend: string, E = OFFICE_EN.E as number, N = OFFICE_EN.N as number, heading = 0, quality = 'medium') {
  await installPad(page);
  await page.goto(`/?backend=${backend}&tiles=/tiles/tileset.json&spawn=${E},${N},${heading}&quality=${quality}`);
  await page.waitForFunction(() => window.__game?.ready);
  await expect(page.getByRole('option', { name: 'New game' })).toBeEnabled();
  await expect(page.locator('#gamepad-status')).toHaveText('Xbox Wireless Controller connected');
  expect(await page.evaluate(() => window.__game!.backend)).toBe(backend === 'webgpu' ? 'WebGPU' : 'WebGL2');
  await page.getByRole('option', { name: 'New game' }).click();
  await page.getByRole('button', { name: 'Skip' }).click();
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

const curb = { x: 80.53186, z: 244.71251, nx: 0.82218, nz: -0.56923, heading: 55.3 };
async function surveyedCurb(page: Page) {
  const hits = await page.evaluate(async ({ x, z, nx, nz }) => {
    const physics = window.__game!.physics as PhysicsClient;
    const ray = (px: number, pz: number) => physics.raycast({ x: px, y: 20, z: pz }, { x: 0, y: -1, z: 0 }, 40);
    return { road: await ray(x - 1.5 * nx, z - 1.5 * nz), sidewalk: await ray(x + 1.5 * nx, z + 1.5 * nz) };
  }, curb);
  expect(hits.road).not.toBeNull();
  expect(hits.sidewalk).not.toBeNull();
  const rise = hits.sidewalk!.point.y - hits.road!.point.y;
  test.skip(rise < 0.09, 'Published AOI does not yet contain the measured curb ground layer');
  expect(hits.road!.material).toBe(1);
  expect(hits.sidewalk!.material).toBe(2);
  expect(rise).toBeGreaterThan(0.11);
  expect(rise).toBeLessThan(0.17);
  return rise;
}

test('AOI curb: Xbox walks from road to sidewalk over measured step', { tag: ['@walk', '@curb', '@ground', '@character', '@aoi'] }, async ({ page }, info) => {
  const en = engineToEN(79.29860, 0, 245.56636);
  await start(page, info.project.name, en.E, en.N, curb.heading, 'low');
  const rise = await surveyedCurb(page);
  const from = (await gameState(page)).feet;
  await padAxes(page, 0, -1);
  await page.waitForFunction(({ from, nx, nz }) => {
    const p = (window.__game!.state as () => any)().feet;
    if ((p[0] - from[0]) * nx + (p[2] - from[2]) * nz < 3.3) return false;
    window.__virtualPad.axes = [0, 0, 0, 0];
    return true;
  }, { from, nx: curb.nx, nz: curb.nz });
  await page.waitForFunction(() => Math.hypot(...(window.__game!.state as () => any)().vel) < 0.15);
  const end = await gameState(page);
  expect(end.feet[1] - from[1]).toBeGreaterThan(rise - 0.03);
  expect(end.feet[1] - from[1]).toBeLessThan(rise + 0.08);
  expect(end.groundMaterial).toBe(2);
  expect(end.grounded).toBe(true);
  await safety(page, 4);
});

test('AOI curb: Xbox car strikes measured road-to-sidewalk step', { tag: ['@drive', '@curb', '@vehicle', '@ground', '@aoi'] }, async ({ page }, info) => {
  const carX = curb.x - 9 * curb.nx, carZ = curb.z - 9 * curb.nz;
  const yaw = -curb.heading * Math.PI / 180;
  const en = engineToEN(carX - 6 * Math.cos(yaw), 0, carZ + 6 * Math.sin(yaw));
  await start(page, info.project.name, en.E, en.N, curb.heading, 'low');
  await surveyedCurb(page);
  await padAxes(page, 1, 0);
  await page.waitForFunction(() => {
    const s = (window.__game!.state as () => any)();
    return Math.hypot(s.feet[0] - s.vehicle.pos[0], s.feet[2] - s.vehicle.pos[2]) < 3.2;
  });
  await padAxes(page, 0, 0); await padButton(page, 3, 1); await padButton(page, 3, 0);
  await expect.poll(async () => (await gameState(page)).mode).toBe('car');
  await padButton(page, 7, 0.7);
  await page.waitForFunction(() => !!(window.__game!.state as () => any)().vehicle.curbStrike, null, { timeout: 12_000 });
  await padButton(page, 7, 0);
  const driven = await page.evaluate(() => (window.__game!.state as () => any)());
  expect(driven.vehicle.curbStrike.height).toBeGreaterThan(0.1);
  expect(driven.vehicle.curbStrike.height).toBeLessThan(0.18);
  expect(driven.vehicle.curbStrike.speed).toBeGreaterThan(3);
  expect(driven.vehicle.curbStrike.blocked).toBe(false);
  await safety(page, 20);
});

type HapticRecord = { cause: string; strong: number; weak: number; duration: number };
const hapticLog = (page: Page) => page.evaluate(() => (window.__game!.haptics as { log: HapticRecord[] }).log);
const padRumbles = (page: Page) => page.evaluate(() => window.__virtualPad.rumbles);
async function hapticEvidence(page: Page, info: TestInfo, log: HapticRecord[], rumbles: unknown[]) {
  const counts = await page.evaluate(() => (window.__game!.haptics as { counts: Record<string, number> }).counts);
  console.log(`[haptics] ${info.title}: ${rumbles.length} playEffect calls, ${JSON.stringify(counts)}`);
  await info.attach('haptics', { body: JSON.stringify({ counts, log, rumbles }, null, 1), contentType: 'application/json' });
}

test('AOI haptics: Xbox car rumbles at the surveyed kerb and at the building behind it', { tag: ['@drive', '@curb', '@vehicle', '@haptics', '@aoi'] }, async ({ page }, info) => {
  test.skip(info.project.name !== 'webgpu', 'Haptics are backend independent; the WebGPU route covers them');
  const carX = curb.x - 9 * curb.nx, carZ = curb.z - 9 * curb.nz;
  const yaw = -curb.heading * Math.PI / 180;
  const en = engineToEN(carX - 6 * Math.cos(yaw), 0, carZ + 6 * Math.sin(yaw));
  await start(page, info.project.name, en.E, en.N, curb.heading, 'low');
  await surveyedCurb(page);
  await padAxes(page, 1, 0);
  await page.waitForFunction(() => {
    const s = (window.__game!.state as () => any)();
    return Math.hypot(s.feet[0] - s.vehicle.pos[0], s.feet[2] - s.vehicle.pos[2]) < 3.2;
  });
  await padAxes(page, 0, 0); await padButton(page, 3, 1); await padButton(page, 3, 0);
  await expect.poll(async () => (await gameState(page)).mode).toBe('car');
  const before = (await padRumbles(page)).length;
  await padButton(page, 7, 0.7);
  // The real kerb is a lidar slope with no curb material: the wheel's step is what rumbles, then the facade 3.5 m on.
  await page.waitForFunction(() => {
    const log = (window.__game!.haptics as { log: { cause: string }[] }).log;
    return log.some(r => r.cause === 'bump') && log.some(r => r.cause === 'impact');
  }, null, { timeout: 15_000 });
  await padButton(page, 7, 0); await padButton(page, 6, 1);
  await expect.poll(async () => (await gameState(page)).vehicle!.speed).toBeLessThan(0.5);
  await padButton(page, 6, 0);
  const log = await hapticLog(page), rumbles = (await padRumbles(page)).slice(before);
  const driven = await page.evaluate(() => (window.__game!.state as () => any)());
  expect(driven.vehicle.curbStrike, 'The surveyed kerb carries no curb material: haptics must not depend on it').toBeNull();
  const kerb = log.find(r => r.cause === 'bump')!, crash = log.find(r => r.cause === 'impact')!;
  expect(kerb.strong).toBeGreaterThan(0.2);
  expect(crash.strong).toBeGreaterThan(kerb.strong);
  // Every effect reached the pad as a dual-rumble; the engine hums on the weak motor while RT is held.
  expect(rumbles.every(r => r.type === 'dual-rumble' && r.strongMagnitude >= 0 && r.strongMagnitude <= 1 && r.weakMagnitude <= 1)).toBe(true);
  expect(rumbles.filter(r => r.strongMagnitude === 0 && r.weakMagnitude > 0).length, 'engine pulses').toBeGreaterThan(5);
  expect(rumbles.some(r => r.strongMagnitude === kerb.strong && r.duration === kerb.duration)).toBe(true);
  expect(rumbles.some(r => r.strongMagnitude === crash.strong && r.duration === crash.duration)).toBe(true);
  await hapticEvidence(page, info, log, rumbles);
  await safety(page, 20);
});

// A stair flight from Läntinen Rantakatu down to the quay, east of the office (stair_slope from z 259.3 to 261.2).
const stairs = { x: 188.75, top: 257, bottom: 262.4 };
test('AOI haptics: Xbox walk down and up a real stair flight ticks on every stair footstep', { tag: ['@walk', '@character', '@structures', '@haptics', '@aoi'] }, async ({ page }, info) => {
  test.skip(info.project.name !== 'webgpu', 'Haptics are backend independent; the WebGPU route covers them');
  const en = engineToEN(stairs.x, 0, stairs.top);
  await start(page, info.project.name, en.E, en.N, 180, 'low');
  const from = await gameState(page);
  for (const [y, target] of [[-1, stairs.bottom], [1, stairs.top + 0.5]] as const) {
    await padAxes(page, 0, y);
    await page.waitForFunction(({ y, target }) => {
      const z = (window.__game!.state as () => any)().feet[2];
      if (y < 0 ? z < target : z > target) return false;
      window.__virtualPad.axes = [0, 0, 0, 0];
      return true;
    }, { y, target }, { timeout: 10_000 });
    await expect.poll(async () => Math.hypot(...(await gameState(page)).vel)).toBeLessThan(0.15);
  }
  const end = await gameState(page);
  expect(end.feet[1]).toBeGreaterThan(from.feet[1] - 0.3);
  const log = await hapticLog(page), rumbles = await padRumbles(page);
  const steps = log.filter(r => r.cause === 'stairs');
  expect(steps.length, 'stair footsteps down and up').toBeGreaterThanOrEqual(3);
  expect(log.filter(r => r.cause === 'landing'), 'walking a stair flight is no fall').toEqual([]);
  expect(rumbles.filter(r => r.duration === steps[0].duration && r.strongMagnitude === steps[0].strong).length).toBe(steps.length);
  await hapticEvidence(page, info, log, rumbles);
  await safety(page, 7);
});

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

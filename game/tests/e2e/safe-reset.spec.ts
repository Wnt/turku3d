// Safe reset (gameplay/recovery.ts) on the synthetic dev tiles: x 0..250, z 0..250, ground at H 2, a river pond in
// c_1_2 (bed 2 m down, water 0.4 m below the ground). Screenshots: $TURKU3D_WORK/reports/gameplay-safe-reset/.
import { expect, installPad, isolatedTest as test, type Page } from './fixtures';
import { frames, untilSim } from './waits';
import fs from 'node:fs';
import path from 'node:path';
import { reportsRoot } from '../../../tools/dev/environment.mjs';

const SHOTS = path.join(reportsRoot, 'gameplay-safe-reset');
const EAST_EDGE = 250;
const state = (page: Page) => page.evaluate(() => (window as any).__game.state());
const missionState = (s: any) => ({ phase: s.missions.phase, money: s.missions.money, permit: s.missions.permit,
  target: s.missions.hud.activeTargetId });

async function start(page: Page, backend: string, spawn: string) {
  await installPad(page);
  await page.goto(`/?scene=dev&backend=${backend}&spawn=${spawn}`);
  await page.getByRole('option', { name: 'New game' }).click();
  await page.getByRole('button', { name: 'Skip' }).click();
  await page.waitForFunction(() => { const g = (window as any).__game; return g?.ready && g.state().grounded && g.state().tilesSettled; });
  fs.mkdirSync(SHOTS, { recursive: true });
}
const shot = async (page: Page, name: string) => page.screenshot({ path: path.join(SHOTS, `${test.info().project.name}-${name}.png`) });

test('a car driven at the east edge stops at it; a car driven into the river is reset, automatically and with R', { tag: ['@drive', '@vehicle', '@gameplay'] }, async ({ page }, info) => {
  const errors: string[] = [];
  page.on('pageerror', e => errors.push(e.message));
  page.on('console', m => { if (m.type() === 'error') errors.push(m.text()); });
  await start(page, info.project.name, '23459700,6704353'); // x 200, z 147
  // Park the player's car facing east (yaw 0: chassis +X), the player beside it, and get in.
  await page.evaluate(() => {
    const game = (window as any).__game;
    game.physics.resetVehicle({ x: 200, y: 2.75, z: 150 }, 0, 'player');
  });
  await untilSim(page, () => { const s = (window as any).__game.state(); return Math.hypot(s.vehicle.pos[0] - 200, s.vehicle.pos[2] - 150) < 0.5 && s.vehicle.speed < 0.2; }, null, 20);
  const before = missionState(await state(page));
  await page.keyboard.press('KeyF');
  await untilSim(page, () => (window as any).__game.state().mode === 'car', null, 10);

  // The edge of the built area is an invisible wall (physics/edgeWalls.ts): full throttle into it, the car stays
  // on the ground inside the edge and is never lost.
  await page.keyboard.down('KeyW');
  await untilSim(page, edge => (window as any).__game.state().vehicle.pos[0] > edge - 8, EAST_EDGE, 20);
  await frames(page, 120);
  let s = await state(page);
  await page.keyboard.up('KeyW');
  expect(s.vehicle.pos[0]).toBeLessThan(EAST_EDGE);
  expect(s.vehicle.pos[1]).toBeGreaterThan(2.2);
  expect(s.recovery.lost).toBeNull();
  expect(s.recovery.hud.edge).toBe(true);
  await shot(page, '01-car-at-edge');

  // West into the river pond (c_1_2, x 78..109, bed 2 m down): brake on its bed and wait for the reset.
  await page.evaluate(() => { (window as any).__game.physics.resetVehicle({ x: 200, y: 2.75, z: 156 }, Math.PI, 'player'); });
  await untilSim(page, () => { const s = (window as any).__game.state(); return s.vehicle.speed < 0.3 && s.vehicle.wheels.every((w: any) => w.contact); }, null, 10);
  await frames(page, 40); // the placement's grace and a first safe sample
  const driveIn = async () => {
    await page.keyboard.down('KeyW');
    await untilSim(page, () => (window as any).__game.state().vehicle.pos[0] < 104, null, 30);
    await page.keyboard.up('KeyW');
    await page.keyboard.down('KeyS');
    await untilSim(page, () => (window as any).__game.state().recovery.lost !== null, null, 20);
    await page.keyboard.up('KeyS');
  };
  await driveIn();
  s = await state(page);
  expect(s.recovery.lost.reason).toBe('water');
  expect(s.recovery.hud.line).toContain('Press R to return to solid ground');
  await shot(page, '02-car-in-river');

  // The 5 s countdown resets the car: upright, still, on the ground east of the pond, still driving.
  await untilSim(page, () => (window as any).__game.state().recovery.resets === 1, null, 10);
  await untilSim(page, () => { const s = (window as any).__game.state(); return s.vehicle.speed < 0.3 && s.vehicle.wheels.every((w: any) => w.contact); }, null, 10);
  s = await state(page);
  expect(s.mode).toBe('car');
  expect(s.vehicle.pos[0]).toBeGreaterThan(109);
  expect(s.vehicle.pos[1]).toBeGreaterThan(2.2);
  expect(s.vehicle.pos[1]).toBeLessThan(3.2);
  expect(s.recovery.lost).toBeNull();
  expect(s.recovery.hud.line).toBe('Back on solid ground');
  expect(missionState(s)).toEqual(before);
  await shot(page, '03-car-reset');
  await untilSim(page, () => (window as any).__game.state().recovery.hud.line === null, null, 6);

  // Again (the reset keeps the westward heading of its sample), and R (vehicleReset) resets at once.
  await driveIn();
  const countdown = (await state(page)).recovery.countdown;
  await page.keyboard.press('KeyR');
  await untilSim(page, () => (window as any).__game.state().recovery.resets === 2, null, 2);
  s = await state(page);
  expect(countdown).toBeGreaterThan(4);
  expect(s.vehicle.pos[0]).toBeGreaterThan(109);
  expect(missionState(s)).toEqual(before);
  expect(errors).toEqual([]);
});

test('walking into the river shows the prompt, Y returns to the bank, the countdown also does', { tag: ['@walk', '@gameplay', '@input'] }, async ({ page }, info) => {
  const errors: string[] = [];
  page.on('pageerror', e => errors.push(e.message));
  page.on('console', m => { if (m.type() === 'error') errors.push(m.text()); });
  await start(page, info.project.name, '23459594,6704378'); // x 94, z 122, 11 m north of the pond's bank
  const before = missionState(await state(page));
  const walkIn = async () => {
    await page.evaluate(() => { (window as any).__game.orbit.yaw = Math.PI; }); // forward = +z, south into the pond
    await page.evaluate(() => { (window as any).__virtualPad.axes = [0, -1, 0, 0]; });
    await untilSim(page, () => (window as any).__game.state().recovery.lost !== null, null, 20);
    await page.evaluate(() => { (window as any).__virtualPad.axes = [0, 0, 0, 0]; });
  };
  await walkIn();
  let s = await state(page);
  expect(s.recovery.lost.reason).toBe('water');
  expect(s.feet[1]).toBeLessThan(1.2);
  expect(s.recovery.hud.line).toContain('You fell into the river. Press Ⓨ to return to solid ground');
  await shot(page, '03-river-prompt');
  // Y (interact outside the lost state) resets at once and does not enter a car.
  await page.evaluate(() => { (window as any).__virtualPad.buttons[3] = 1; });
  await untilSim(page, () => (window as any).__game.state().recovery.resets === 1, null, 2);
  await page.evaluate(() => { (window as any).__virtualPad.buttons[3] = 0; });
  await untilSim(page, () => { const s = (window as any).__game.state(); return s.grounded && s.feet[1] > 1.5; }, null, 5);
  s = await state(page);
  expect(s.mode).toBe('foot');
  expect(s.feet[1]).toBeCloseTo(2, 1);
  expect(s.feet[2]).toBeLessThan(133); // north of the bank
  expect(s.recovery.waterDepth ?? -1).toBeLessThanOrEqual(0);
  expect(missionState(s)).toEqual(before);
  await shot(page, '04-river-reset');

  // Into the river again and wait: the countdown resets automatically.
  await frames(page, 30);
  await walkIn();
  await untilSim(page, () => (window as any).__game.state().recovery.resets === 2, null, 8);
  await untilSim(page, () => { const s = (window as any).__game.state(); return s.grounded && s.feet[1] > 1.5; }, null, 5);
  s = await state(page);
  expect(s.feet[1]).toBeCloseTo(2, 1);
  expect(missionState(s)).toEqual(before);
  expect(errors).toEqual([]);
});

test('the edge of the built area shows on the HUD within 20 m', { tag: ['@walk', '@gameplay'] }, async ({ page }, info) => {
  await start(page, info.project.name, '23459735,6704380'); // x 235, z 120: 15 m from the east edge
  await untilSim(page, () => (window as any).__game.state().recovery.hud.edge === true, null, 5);
  expect((await state(page)).recovery.edgeDistance).toBeCloseTo(15, 0);
  await shot(page, '05-edge-notice');
});

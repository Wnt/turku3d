import { isolatedTest as test, expect, installPad, padAxes, padButton } from './fixtures';
import { open, untilSim } from './waits';
import fs from 'node:fs';
import path from 'node:path';
import { reportsRoot } from '../../../tools/dev/environment.mjs';
import { DEALER, DEALER_VEHICLE_SPOT } from '../../src/gameplay/content/locations';
import { SCOOTER_TARGET_SPEED } from '../../src/physics/constants';

const SHOTS = path.join(reportsRoot, 'gameplay-two-wheeler');
// A continued (pre-slot) game that may shop at the dealer: a rented permit and enough money for the scooter.
const SAVE = { version: 2, phase: 'at_pizzeria', money: 100, permit: 10, plates: false, kidney: false, warning: false,
  vehicle: 'none', delivery: 1, elapsed: 0, par: 0 };

test('buys the scooter, rides it over the kerb to 45 km/h and dismounts', { tag: ['@drive', '@scooter', '@curb'] }, async ({ page }, info) => {
  const errors: string[] = [];
  page.on('pageerror', e => errors.push(e.message));
  page.on('console', m => { if (m.type() === 'error') errors.push(m.text()); });
  await installPad(page);
  await page.addInitScript(save => localStorage.setItem('turku3d.missions', save), JSON.stringify(SAVE));
  await open(page, `/?scene=dev&backend=${info.project.name}`);
  // The pre-slot mission save migrates to slot 1, which Continue loads.
  await page.getByRole('option', { name: /^Continue/ }).click();
  await page.waitForFunction(() => { const g = (window as any).__game, s = g?.ready && g.state(); return s && !s.loading && s.grounded && s.tilesSettled; });

  // The development tiles end before the dealer's street, so the purchase drives the mission system at the dealer.
  const bought = await page.evaluate(dealer => {
    const missions = (window as any).__game.missions;
    const frame = { position: dealer, input: 'gamepad', vehicleId: null };
    missions.update(0, { ...frame, interact: true });
    const view = missions.shop;
    const result = missions.update(0, { ...frame, interact: false, choose: 'buy_scooter' });
    return { shop: view?.id, option: view?.options.find((o: any) => o.id === 'buy_scooter'), result, vehicle: missions.vehicle, money: missions.money };
  }, DEALER.point);
  expect(bought).toMatchObject({ shop: 'dealer', option: { label: 'Scooter', price: 60, enabled: true },
    result: { bought: 'scooter' }, vehicle: 'scooter', money: 40 });
  // The scooter appears at the dealer's kerb where the car would be parked.
  await untilSim(page, spot => (window as any).__game.state().vehicles.some((v: any) => v.id === 'scooter'
    && Math.hypot(v.pos[0] - spot.x, v.pos[2] - spot.z) < 0.5), DEALER_VEHICLE_SPOT, 10);
  // Bring it beside the player, facing north towards the 12 cm kerb at z 238.
  await page.evaluate(() => {
    const g = (window as any).__game, [x, y, z] = g.state().feet;
    g.physics.resetVehicle({ x: x - 2, y: y + 0.6, z: z - 1 }, Math.PI / 2, 'scooter');
  });
  await untilSim(page, () => {
    const s = (window as any).__game.state(), v = s.vehicles.find((c: any) => c.id === 'scooter');
    return v && Math.abs(v.speed) < 0.05 && s.scooter.visible;
  }, undefined, 10);

  await padButton(page, 3, 1);
  await page.waitForFunction(() => (window as any).__game.state().drivenId === 'scooter');
  await padButton(page, 3, 0);
  const mounted = await page.evaluate(() => (window as any).__game.state());
  expect(mounted.mode).toBe('car');
  expect(mounted.vehicle).toMatchObject({ id: 'scooter', kind: 'scooter', modelLoaded: true });
  expect(mounted.scooter.rider).toBe(true);
  // The rider's hips sit on the seat anchor.
  expect(Math.hypot(...mounted.scooter.riderPos.map((v: number, i: number) => v - mounted.scooter.seatPos[i]))).toBeLessThan(1.2);
  const start = mounted.vehicle.pos as number[];

  await page.evaluate(() => {
    const w = window as any;
    w.scooterTop = 0;
    const sample = () => { const v = w.__game.state().vehicle; if (v?.id === 'scooter') w.scooterTop = Math.max(w.scooterTop, v.forwardSpeed); requestAnimationFrame(sample); };
    requestAnimationFrame(sample);
  });
  await padButton(page, 7, 1);
  await untilSim(page, () => !!(window as any).__game.state().vehicle.curbStrike, null, 30);
  const kerb = await page.evaluate(() => (window as any).__game.state().vehicle.curbStrike);
  expect(kerb.height).toBeCloseTo(0.12, 2);
  expect(kerb.blocked).toBe(false);
  await untilSim(page, z => (window as any).__game.state().vehicle.pos[2] < z - 20, start[2], 30);
  fs.mkdirSync(SHOTS, { recursive: true });
  await page.screenshot({ path: path.join(SHOTS, `${info.project.name}-scooter-ride.png`) });
  // Full throttle on the straight settles at the 45 km/h target.
  await untilSim(page, () => (window as any).__game.state().vehicle.pos[2] < 60, null, 60);
  const top = await page.evaluate(() => (window as any).scooterTop);
  expect(Math.abs(top - SCOOTER_TARGET_SPEED) / SCOOTER_TARGET_SPEED).toBeLessThan(0.1);
  // Steering at speed leans the body into the turn, at most 15 degrees.
  await padAxes(page, -0.6, 0);
  await untilSim(page, () => (window as any).__game.state().scooter.lean < -0.05, null, 10);
  await page.screenshot({ path: path.join(SHOTS, `${info.project.name}-scooter-lean.png`) });
  const lean = await page.evaluate(() => (window as any).__game.state().scooter.lean);
  expect(Math.abs(lean)).toBeLessThanOrEqual(15 * Math.PI / 180 + 1e-6);
  await padAxes(page, 0, 0);
  await padButton(page, 7, 0);
  await padButton(page, 6, 1);
  await untilSim(page, () => Math.abs((window as any).__game.state().vehicle.forwardSpeed) < 0.3, null, 20);
  await padButton(page, 6, 0);

  await padButton(page, 3, 1);
  await page.waitForFunction(() => (window as any).__game.state().mode === 'foot');
  await padButton(page, 3, 0);
  await untilSim(page, () => (window as any).__game.state().grounded, null, 10);
  const off = await page.evaluate(() => (window as any).__game.state());
  const parked = off.vehicles.find((v: any) => v.id === 'scooter');
  expect(Math.hypot(off.feet[0] - parked.pos[0], off.feet[2] - parked.pos[2])).toBeLessThan(2);
  expect(off.scooter.rider).toBe(false);
  await page.screenshot({ path: path.join(SHOTS, `${info.project.name}-scooter-dismount.png`) });
  expect(errors).toEqual([]);
});

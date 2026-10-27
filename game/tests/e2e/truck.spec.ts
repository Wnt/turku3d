import { isolatedTest as test, expect, installPad, padAxes, padButton } from './fixtures';
import { open, untilSim } from './waits';
import fs from 'node:fs';
import path from 'node:path';
import { reportsRoot } from '../../../tools/dev/environment.mjs';
import { DEALER, DEALER_VEHICLE_SPOT } from '../../src/gameplay/content/locations';
import { TRUCK_TARGET_SPEED } from '../../src/physics/constants';

const SHOTS = path.join(reportsRoot, 'gameplay-trophy-truck');
// A continued (pre-slot) game that may shop at the dealer: a rented permit and enough money for the truck.
const SAVE = { version: 2, phase: 'at_pizzeria', money: 1100, permit: 10, plates: false, kidney: false, warning: false,
  vehicle: 'none', delivery: 1, elapsed: 0, par: 0 };
const KERB_SPEED = 40 / 3.6;

/** Puts the truck at engine (x, z) on the development tile's ground (H 2.0 m), heading `yaw` (0 east, π/2 north). */
async function place(page: Parameters<typeof open>[0], x: number, z: number, yaw: number) {
  await page.evaluate(([x, z, yaw]) => (window as any).__game.physics.resetVehicle({ x, y: 3.1, z }, yaw, 'truck'), [x, z, yaw]);
  await untilSim(page, () => {
    const v = (window as any).__game.state().vehicles.find((c: any) => c.id === 'truck');
    // The soft springs hold it with a small residual vertical velocity, so only horizontal rest counts.
    return v && Math.hypot(v.linvel.x, v.linvel.z) < 0.05 && v.wheels.every((w: any) => w.contact);
  }, undefined, 10).catch(async e => {
    const v = await page.evaluate(() => (window as any).__game.state().vehicles.find((c: any) => c.id === 'truck'));
    throw new Error(`${e.message}\ntruck ${JSON.stringify(v)}`);
  });
}

/** A page-side driver: holds RT so the truck keeps `target` m/s (full throttle when `target` is null), straight ahead. */
async function drive(page: Parameters<typeof open>[0], target: number | null) {
  await page.evaluate(target => {
    const w = window as any;
    w.truckTarget = target;
    if (w.truckDriver) return;
    w.truckDriver = true;
    const loop = () => {
      const v = w.__game.state().vehicle;
      if (v?.id === 'truck') {
        if (w.truckTarget !== undefined) {
          const throttle = w.truckTarget === null ? 1 : Math.max(0, Math.min(1, 0.3 + (w.truckTarget - v.forwardSpeed) * 0.6));
          w.__virtualPad.buttons[7] = w.truckTarget === 0 ? 0 : throttle;
          w.__virtualPad.buttons[6] = w.truckTarget === 0 ? 1 : 0;
        }
        const up = 1 - 2 * v.rot.x ** 2 - 2 * v.rot.z ** 2;
        w.truckLog.push({ t: w.__game.clock.simSeconds, x: v.pos[0], y: v.pos[1], z: v.pos[2], speed: v.forwardSpeed,
          contact: v.wheels.filter((wh: any) => wh.contact).length, tilt: Math.acos(Math.max(-1, Math.min(1, up))) * 180 / Math.PI,
          strike: v.curbStrike?.id ?? 0, impact: v.impact?.id ?? 0 });
      }
      requestAnimationFrame(loop);
    };
    w.truckLog = [];
    requestAnimationFrame(loop);
  }, target);
}

type Sample = { t: number; x: number; y: number; z: number; speed: number; contact: number; tilt: number; strike: number; impact: number };
const log = (page: Parameters<typeof open>[0]) => page.evaluate(() => (window as any).truckLog as Sample[]);
const clearLog = (page: Parameters<typeof open>[0]) => page.evaluate(() => { (window as any).truckLog = []; });

test('buys the trophy truck, drives it over a kerb at 40 km/h, to 120 km/h and over a jump', { tag: ['@drive', '@truck', '@curb'] }, async ({ page }, info) => {
  test.skip(info.project.name !== 'webgpu', 'the truck route runs on WebGPU');
  const errors: string[] = [];
  page.on('pageerror', e => errors.push(e.message));
  page.on('console', m => { if (m.type() === 'error') errors.push(m.text()); });
  await installPad(page);
  await page.addInitScript(save => localStorage.setItem('turku3d.missions', save), JSON.stringify(SAVE));
  await open(page, `/?backend=${info.project.name}`);
  await page.getByRole('option', { name: /^Continue/ }).click();
  await page.waitForFunction(() => { const g = (window as any).__game, s = g?.ready && g.state(); return s && !s.loading && s.grounded && s.tilesSettled; });
  fs.mkdirSync(SHOTS, { recursive: true });

  // The development tiles end before the dealer's street, so the purchase drives the mission system at the dealer.
  const bought = await page.evaluate(dealer => {
    const missions = (window as any).__game.missions;
    const frame = { position: dealer, input: 'gamepad', vehicleId: null };
    missions.update(0, { ...frame, interact: true });
    const view = missions.shop;
    const result = missions.update(0, { ...frame, interact: false, choose: 'buy_truck' });
    return { shop: view?.id, option: view?.options.find((o: any) => o.id === 'buy_truck'), result, vehicle: missions.vehicle, money: missions.money };
  }, DEALER.point);
  expect(bought).toMatchObject({ shop: 'dealer', option: { label: 'Trophy truck', price: 1000, enabled: true },
    result: { bought: 'truck' }, vehicle: 'truck', money: 100 });
  // The truck appears at the dealer's kerb where the car would be parked.
  await untilSim(page, spot => (window as any).__game.state().vehicles.some((v: any) => v.id === 'truck' && v.kind === 'truck'
    && Math.hypot(v.pos[0] - spot.x, v.pos[2] - spot.z) < 0.5), DEALER_VEHICLE_SPOT, 10);

  // 61 m west-south-west of where the 12 cm kerb (z 238) runs, aimed to cross it at x 160.
  const kerbAim = Math.atan2(247 - 238, 160 - 100);
  await place(page, 100, 247, kerbAim);
  await page.evaluate(([x, z]) => (window as any).__game.physics.setCharacterEnabled(true, { x, y: 2.05, z }), [100, 244.6]);
  await untilSim(page, () => { const s = (window as any).__game.state(); return s.grounded && Math.hypot(s.feet[0] - 100, s.feet[2] - 244.6) < 0.5; }, undefined, 10);
  await padButton(page, 3, 1);
  await untilSim(page, () => (window as any).__game.state().drivenId === 'truck', null, 3);
  await padButton(page, 3, 0);
  const seated = await page.evaluate(() => (window as any).__game.state());
  expect(seated.vehicle).toMatchObject({ id: 'truck', kind: 'truck', modelLoaded: true });
  expect(seated.truck).toMatchObject({ visible: true, driver: true });
  await untilSim(page, () => (window as any).__game.state().truck.driver, null, 2);
  const driver = await page.evaluate(() => (window as any).__game.state());
  // The driver's hips sit on the seat anchor inside the open cab.
  expect(Math.hypot(...driver.scooter.riderPos.map((v: number, i: number) => v - driver.truck.seatPos[i]))).toBeLessThan(1.2);
  // The chase camera frames the truck from higher up than the car's 0.9 m look-at.
  expect(driver.camera.pos[1] - driver.vehicle.pos[1]).toBeGreaterThan(1.2);
  await page.screenshot({ path: path.join(SHOTS, `${info.project.name}-truck-seated.png`) });

  // 1. Drive the 50 m to the kerb, holding 40 km/h for the last part.
  await drive(page, KERB_SPEED);
  await untilSim(page, () => !!(window as any).__game.state().vehicle.curbStrike, null, 30);
  await untilSim(page, () => (window as any).__game.state().vehicle.pos[0] > 172, null, 10);
  await page.screenshot({ path: path.join(SHOTS, `${info.project.name}-truck-kerb.png`) });
  const kerb = await page.evaluate(() => (window as any).__game.state().vehicle.curbStrike);
  expect(kerb.height).toBeCloseTo(0.12, 2);
  expect(kerb.blocked).toBe(false);
  const kerbRun = await log(page);
  const first = kerbRun.find(s => s.strike > 0)!;
  const last = kerbRun.findLast((s, i) => i > 0 && s.strike !== kerbRun[i - 1].strike) ?? first;
  const before = kerbRun.filter(s => s.t < first.t - 0.2 && s.t > first.t - 1);
  const after = kerbRun.filter(s => s.t > last.t + 0.3);
  const travelled = Math.hypot(first.x - 100, first.z - 247);
  const speedBefore = before.reduce((a, s) => a + s.speed, 0) / before.length;
  const speedAfter = Math.min(...after.slice(0, 20).map(s => s.speed));
  expect(travelled).toBeGreaterThan(50);
  expect(speedBefore * 3.6).toBeGreaterThan(36);
  expect(speedBefore * 3.6).toBeLessThan(44);
  expect(speedAfter).toBeGreaterThan(speedBefore * 0.95);
  expect(Math.max(...kerbRun.map(s => s.tilt))).toBeLessThan(6);

  // 2. Full throttle down the tile's 290 m diagonal: within 10 % of 120 km/h.
  await drive(page, 0);
  await place(page, 40, 10, -Math.PI / 4);
  await clearLog(page);
  await drive(page, null);
  await untilSim(page, () => { const v = (window as any).__game.state().vehicle; return v.pos[0] > 238 || v.pos[2] > 238; }, null, 30);
  await page.screenshot({ path: path.join(SHOTS, `${info.project.name}-truck-top-speed.png`) });
  const run = await log(page);
  const top = Math.max(...run.map(s => s.speed));
  expect(Math.abs(top - TRUCK_TARGET_SPEED) / TRUCK_TARGET_SPEED).toBeLessThan(0.1);

  // 3. The 0.96 m stair ramp at x 128-134 (rising north at z 220) and off the platform's north edge at z 208.
  await drive(page, 0);
  await place(page, 131, 246, Math.PI / 2);
  await clearLog(page);
  await drive(page, 9);
  await untilSim(page, () => (window as any).__game.state().vehicle.pos[2] < 205, null, 20);
  await untilSim(page, () => (window as any).truckLog.at(-1).contact === 4, null, 5);
  await page.screenshot({ path: path.join(SHOTS, `${info.project.name}-truck-landed.png`) });
  await untilSim(page, () => (window as any).__game.state().vehicle.pos[2] < 190, null, 10);
  const jump = await log(page);
  let air = 0, longest = 0;
  for (let i = 1; i < jump.length; i++) {
    air = jump[i].contact === 0 ? air + (jump[i].t - jump[i - 1].t) : 0;
    longest = Math.max(longest, air);
  }
  expect(longest).toBeGreaterThan(0.25);
  const landed = jump.filter(s => s.z < 200);
  expect(Math.max(...landed.map(s => s.tilt))).toBeLessThan(15);
  expect(Math.min(...landed.map(s => s.speed))).toBeGreaterThan(4);
  expect(Math.abs(landed.at(-1)!.x - 131)).toBeLessThan(2);
  // Stopped, the driver climbs out beside the outboard tyres.
  await drive(page, 0);
  await untilSim(page, () => Math.abs((window as any).__game.state().vehicle.forwardSpeed) < 0.3, null, 20);
  await page.evaluate(() => { (window as any).truckTarget = undefined; (window as any).__virtualPad.buttons[6] = 0; });
  await padButton(page, 3, 1);
  await untilSim(page, () => (window as any).__game.state().mode === 'foot', null, 3);
  await padButton(page, 3, 0);
  await untilSim(page, () => (window as any).__game.state().grounded, null, 10);
  const off = await page.evaluate(() => (window as any).__game.state());
  const parked = off.vehicles.find((v: any) => v.id === 'truck');
  expect(Math.hypot(off.feet[0] - parked.pos[0], off.feet[2] - parked.pos[2])).toBeGreaterThan(1.5);
  expect(Math.hypot(off.feet[0] - parked.pos[0], off.feet[2] - parked.pos[2])).toBeLessThan(3);
  expect(off.truck.driver).toBe(false);
  fs.writeFileSync(path.join(SHOTS, `${info.project.name}-metrics.json`), JSON.stringify({
    kerb: { travelled, height: kerb.height, speedBeforeKmh: speedBefore * 3.6, speedAfterKmh: speedAfter * 3.6 },
    topSpeedKmh: top * 3.6, jump: { airSeconds: longest, landedMaxTilt: Math.max(...landed.map(s => s.tilt)) } }, null, 2));
  expect(errors).toEqual([]);
});

test('the Developer page spawns the trophy truck in front of the player, unbought and drivable', { tag: ['@truck', '@synthetic'] }, async ({ page }, info) => {
  test.skip(info.project.name !== 'webgpu', 'the truck route runs on WebGPU');
  await installPad(page);
  await open(page, `/?backend=${info.project.name}`);
  await page.getByRole('option', { name: 'New game' }).click();
  await page.getByRole('button', { name: 'Skip' }).click();
  await untilSim(page, () => { const s = (window as any).__game.state(); return !s.loading && !s.startScreen && s.grounded; }, null, 20);
  await page.keyboard.press('Escape');
  const option = (name: string) => page.locator('#pause-menu .menu-option', { hasText: name });
  await option('Developer').click();
  await option('Spawn trophy truck here').click();
  await expect(page.locator('#pause-menu .menu-text')).toHaveText('Trophy truck spawned in front of you.');
  await option('Back').click();
  await option('Resume').click();
  await untilSim(page, () => {
    const s = (window as any).__game.state(), v = s.vehicles.find((c: any) => c.id === 'truck');
    return !s.pauseMenu.open && v && Math.hypot(v.linvel.x, v.linvel.z) < 0.05 && v.wheels.every((w: any) => w.contact);
  }, null, 10);
  const s = await page.evaluate(() => (window as any).__game.state());
  const truck = s.vehicles.find((v: any) => v.id === 'truck');
  expect(Math.hypot(truck.pos[0] - s.feet[0], truck.pos[2] - s.feet[2])).toBeGreaterThan(5);
  expect(Math.hypot(truck.pos[0] - s.feet[0], truck.pos[2] - s.feet[2])).toBeLessThan(9);
  expect(await page.evaluate(() => (window as any).__game.missions.vehicle)).toBe('none');
  await page.screenshot({ path: path.join(SHOTS, `${info.project.name}-truck-dev-spawn.png`) });
  // Walk towards it (it lies along the camera's view) and climb in: free roam lets the player drive any vehicle.
  await padAxes(page, 0, -0.5);
  await untilSim(page, () => {
    const s = (window as any).__game.state(), v = s.vehicles.find((c: any) => c.id === 'truck');
    return Math.hypot(v.pos[0] - s.feet[0], v.pos[2] - s.feet[2]) < 3.6;
  }, null, 10);
  await padAxes(page, 0, 0);
  await padButton(page, 3, 1);
  await untilSim(page, () => (window as any).__game.state().drivenId === 'truck', null, 3);
  await padButton(page, 3, 0);
});

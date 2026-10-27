import { isolatedTest as test, expect } from './fixtures';
import { open, untilSim } from './waits';
import fs from 'node:fs';
import path from 'node:path';
import { reportsRoot } from '../../../tools/dev/environment.mjs';

const SHOTS = path.join(reportsRoot, 'screenshots');

test('virtual gamepad drives over a curb, rumbles, orbits, brakes and exits', { tag: ['@drive', '@input', '@curb'] }, async ({ page }, info) => {
  const errors: string[] = [];
  page.on('pageerror', e => errors.push(e.message));
  page.on('console', m => { if (m.type() === 'error') errors.push(m.text()); });
  await page.addInitScript(() => {
    const w = window as any;
    w.pad = { id: 'Xbox Wireless Controller (virtual)', index: 0, connected: true, mapping: 'standard',
      axes: [0, 0, 0, 0], buttons: Array.from({ length: 17 }, () => ({ pressed: false, touched: false, value: 0 })), timestamp: 0,
      vibrationActuator: { playEffect: async (_type: string, parameters: any) => { w.rumbles.push(parameters); return 'complete'; }, reset: async () => 'complete' } };
    w.rumbles = [];
    Object.defineProperty(navigator, 'getGamepads', { value: () => [w.pad] });
  });
  const axis = (i: number, value: number) => page.evaluate(([i, value]) => { (window as any).pad.axes[i] = value; }, [i, value]);
  const button = (i: number, value: number) => page.evaluate(([i, value]) => {
    (window as any).pad.buttons[i] = { value, pressed: value > 0.5, touched: value > 0 };
  }, [i, value]);
  await open(page, `/?backend=${info.project.name}`);
  await page.getByRole('button', { name: 'Start playing' }).click();
  await page.waitForFunction(() => { const g = (window as any).__game; return g?.ready && g.state().grounded && g.state().tilesSettled; });
  await axis(0, 1);
  await page.waitForFunction(() => { const s = (window as any).__game.state(); return Math.hypot(s.vehicle.pos[0] - s.feet[0], s.vehicle.pos[2] - s.feet[2]) < 3; });
  await axis(0, 0);
  await button(3, 1);
  await page.waitForFunction(() => (window as any).__game.state().mode === 'car');
  await button(3, 0);
  const start = await page.evaluate(() => (window as any).__game.state());
  expect(start.vehicle.modelLoaded).toBe(true);
  expect(start.inputSource).toBe('gamepad');
  await button(7, 0.65);
  await untilSim(page, () => !!(window as any).__game.state().vehicle.curbStrike, null, 60);
  await button(7, 0);
  await untilSim(page, () => (window as any).__game.state().vehicle.pos[2] < 236, null, 60);
  const driven = await page.evaluate(() => (window as any).__game.state());
  expect(driven.vehicle.curbStrike.height).toBeCloseTo(0.12, 2);
  expect(driven.vehicle.curbStrike.blocked).toBe(false);
  expect(driven.vehicle.curbStrike.speed).toBeGreaterThan(3);
  expect(driven.vehicle.curbStrike.speed).toBeLessThan(10);
  expect(driven.camera.fov).toBeGreaterThan(start.camera.fov);
  expect(driven.vehicle.wheels.some((w: any) => Math.abs(w.rotation) > 1)).toBe(true);
  expect(await page.evaluate(() => (window as any).rumbles.some((r: any) => r.strongMagnitude > 0))).toBe(true);
  // The right stick can orbit through the rear view without changing the car's heading.
  await axis(2, 1);
  await untilSim(page, (yaw) => Math.abs((window as any).__game.state().camera.yaw - yaw) > 2.6, driven.camera.yaw, 60);
  await axis(2, 0);
  const beforeBrake = await page.evaluate(() => (window as any).__game.state().vehicle.speed);
  await button(6, 1);
  await untilSim(page, (speed) => (window as any).__game.state().vehicle.speed < speed * 0.5, beforeBrake, 60);
  await button(6, 0);
  await button(5, 1);
  fs.mkdirSync(SHOTS, { recursive: true });
  await page.screenshot({ path: path.join(SHOTS, `${info.project.name}-vehicle-curb.png`) });
  await button(5, 0);
  await button(3, 1);
  await page.waitForFunction(() => (window as any).__game.state().mode === 'foot');
  await button(3, 0);
  expect(errors).toEqual([]);
});

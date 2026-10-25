import { expect, test, type Page } from '@playwright/test';
import path from 'node:path';
import { reportsRoot } from '../../../tools/dev/environment.mjs';

// Isolate input focus from other parallel Chrome windows on the shared Mac.
test.beforeEach(async ({ page }) => {
  const session = await page.context().newCDPSession(page);
  await session.send('Emulation.setFocusEmulationEnabled', { enabled: true });
});

// Use the shared functional timeout. Frame/throughput budgets live in performance.spec.ts.

async function start(page: Page, backend: string, spawn: string) {
  await page.addInitScript(() => {
    const virtual = { connected: true, axes: [0, 0, 0, 0], buttons: Array(17).fill(0) };
    (window as any).__virtualPad = virtual;
    Object.defineProperty(navigator, 'getGamepads', { value: () => virtual.connected ? [{
      id: 'Xbox Wireless Controller', index: 0, mapping: 'standard', connected: true, timestamp: performance.now(),
      axes: virtual.axes, buttons: virtual.buttons.map(value => ({ value, pressed: value > 0.5, touched: value > 0 })),
    }] : [] });
  });
  await page.goto(`/?backend=${backend}&spawn=${spawn}`);
  await page.waitForFunction(() => (window as any).__game?.ready && (window as any).__game.state().grounded);
}
async function axes(page: Page, x: number, y: number, rx = 0, ry = 0) {
  await page.evaluate(a => { (window as any).__virtualPad.axes = a; }, [x, y, rx, ry]);
}
async function button(page: Page, index: number, value: number) {
  await page.evaluate(([i, v]) => { (window as any).__virtualPad.buttons[i] = v; }, [index, value]);
  await page.waitForTimeout(80);
}
const state = (page: Page) => page.evaluate(() => (window as any).__game.state());

test('virtual Xbox: analog walk, sprint, curb, camera, jump, hot-plug and pause', { tag: ["@input", "@walk"] }, async ({ page }, info) => {
  await start(page, info.project.name, '23459668,6704260'); // x168,z240, 2m south of curb
  const initial = await state(page);
  // Capture transient analog speed on a game frame; slow protocol polling under
  // parallel GPU load can miss movement samples between physics updates.
  const walking = await page.evaluate(() => new Promise<any>(resolve => {
    (window as any).__virtualPad.axes = [0, -0.5, 0, 0];
    const deadline = performance.now() + 5_000;
    const sample = () => {
      const s = (window as any).__game.state();
      if (Math.hypot(s.vel[0], s.vel[2]) > 0.6 || performance.now() >= deadline) resolve(s);
      else requestAnimationFrame(sample);
    };
    requestAnimationFrame(sample);
  }));
  expect(Math.hypot(walking.vel[0], walking.vel[2])).toBeGreaterThan(0.6);
  expect(Math.hypot(walking.vel[0], walking.vel[2])).toBeLessThan(1.6);
  await axes(page, 0, -1); await button(page, 0, 1);
  await page.waitForFunction(() => (window as any).__game.state().feet[2] < 236.8);
  expect(Math.hypot((await state(page)).vel[0], (await state(page)).vel[2])).toBeGreaterThan(4);
  await axes(page, 0, 0); await button(page, 0, 0);
  await page.waitForTimeout(300);
  const curb = await state(page);
  expect(curb.feet[1]).toBeCloseTo(2.12, 1); expect(curb.feet[2]).toBeLessThan(initial.feet[2] - 3);
  expect(curb.visualFeet[1]).toBeCloseTo(curb.feet[1], 1);
  expect(curb.inputSource).toBe('gamepad');
  expect(curb.contacts).toHaveLength(2);
  for (const c of curb.contacts) expect(Math.abs(c.ankle[1] - c.target[1])).toBeLessThan(0.045);
  await expect(page.locator('#help')).toContainText('ⓧ');
  const yaw = curb.cameraYaw;
  await axes(page, 0, 0, 0.8); await page.waitForTimeout(250); await axes(page, 0, 0);
  expect(Math.abs((await state(page)).cameraYaw - yaw)).toBeGreaterThan(0.15);
  await button(page, 11, 1); await button(page, 11, 0);
  expect(Math.abs(Math.sin((await state(page)).cameraYaw))).toBeLessThan(0.05);
  expect(Math.cos((await state(page)).cameraYaw)).toBeGreaterThan(0.95);
  await button(page, 2, 1);
  await expect.poll(async () => (await state(page)).feet[1]).toBeGreaterThan(2.6);
  await button(page, 2, 0);
  await page.waitForFunction(() => (window as any).__game.state().grounded);
  await button(page, 8, 1); await button(page, 8, 0);
  const paused = await state(page); expect(paused.paused).toBe(true);
  await axes(page, 0, -1); await page.waitForTimeout(200);
  expect((await state(page)).feet).toEqual(paused.feet);
  await axes(page, 0, 0); await button(page, 8, 1); await button(page, 8, 0);
  await page.evaluate(() => { (window as any).__virtualPad.connected = false; });
  await expect.poll(async () => (await state(page)).inputSource).toBe('keyboard');
  await page.evaluate(() => { (window as any).__virtualPad.connected = true; });
  await axes(page, 0.5, 0); await page.waitForTimeout(150); await axes(page, 0, 0);
  expect((await state(page)).inputSource).toBe('gamepad');
  expect((await state(page)).errors).toEqual([]);
});

test('virtual Xbox: ascend and descend slope stairs with feet on query treads', { tag: ["@input", "@walk"] }, async ({ page }, info) => {
  await start(page, info.project.name, '23459631,6704279'); // x131,z221
  await page.evaluate(() => {
    (window as any).__footSamples = [];
    const sample = () => {
      const s = (window as any).__game.state();
      if (s.groundMaterial === 16 && (s.locomotion === 'stairUp' || s.locomotion === 'stairDown')) {
        for (const c of s.contacts) if (c.material === 17 && c.lift < 0.05) {
          (window as any).__footSamples.push({ state: s.locomotion, error: Math.hypot(...c.ankle.map((v: number, i: number) => v - c.target[i])) });
        }
      }
      requestAnimationFrame(sample);
    };
    sample();
  });
  await axes(page, 0, -0.5);
  await page.waitForFunction(() => { const s = (window as any).__game.state(); return s.feet[2] < 219.25 && s.locomotion === 'stairUp'; });
  await axes(page, 0, 0); await page.waitForTimeout(500);
  const middle = await state(page);
  expect(middle.groundMaterial).toBe(16);
  expect(middle.feet[1]).toBeGreaterThan(2.3);
  expect(middle.contacts).toHaveLength(2);
  for (const c of middle.contacts) {
    expect(c.material).toBe(17);
    expect(Math.abs(c.ankle[1] - c.target[1])).toBeLessThan(0.045);
    expect(c.lift).toBeLessThan(0.06);
  }
  await page.screenshot({ path: path.join(reportsRoot, 'screenshots', `${info.project.name}-stair-ik.png`) });
  await axes(page, 0, -0.65);
  await page.waitForFunction(() => (window as any).__game.state().feet[2] < 217.5);
  await axes(page, 0, 0); await page.waitForTimeout(200);
  expect((await state(page)).feet[1]).toBeCloseTo(2.96, 1);
  await axes(page, 0, 0.6);
  await page.waitForFunction(() => (window as any).__game.state().locomotion === 'stairDown');
  await page.waitForFunction(() => (window as any).__game.state().feet[2] > 220.8);
  await axes(page, 0, 0); await page.waitForTimeout(200);
  expect((await state(page)).feet[1]).toBeCloseTo(2, 1);
  const samples = await page.evaluate(() => (window as any).__footSamples as { state: string; error: number }[]);
  expect(samples.filter(s => s.state === 'stairUp').length).toBeGreaterThan(5);
  expect(samples.filter(s => s.state === 'stairDown').length).toBeGreaterThan(5);
  expect(Math.max(...samples.map(s => s.error))).toBeLessThan(0.06);
  expect((await state(page)).errors).toEqual([]);
});

test('virtual Xbox: Y enters/exits, RT drives, LT reverses and RB brakes', { tag: ["@input", "@drive"] }, async ({ page }, info) => {
  await start(page, info.project.name, '23459668,6704254');
  await axes(page, 1, 0);
  await page.waitForFunction(() => (window as any).__game.state().feet[0] > 170.8);
  await axes(page, 0, 0); await button(page, 3, 1); await button(page, 3, 0);
  expect((await state(page)).mode).toBe('car');
  await expect(page.locator('#help')).toContainText('RT');
  const z = (await state(page)).vehicle.pos[2];
  await button(page, 7, 0.8);
  await page.waitForFunction(z => (window as any).__game.state().vehicle.pos[2] < z - 2, z);
  await button(page, 7, 0); await button(page, 5, 1);
  await page.waitForTimeout(300); await button(page, 5, 0);
  await button(page, 6, 1);
  await page.waitForFunction(() => (window as any).__game.physics.latest.vehicle.forwardSpeed < -0.5);
  await button(page, 6, 0); await button(page, 3, 1); await button(page, 3, 0);
  expect((await state(page)).mode).toBe('foot');
  expect((await state(page)).errors).toEqual([]);
});

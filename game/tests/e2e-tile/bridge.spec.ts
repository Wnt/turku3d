import { expect, test } from '@playwright/test';
import fs from 'node:fs';
import path from 'node:path';
import { reportsRoot, tileDist } from '../../../tools/dev/environment.mjs';

const shots = path.join(reportsRoot, 'bridges');
fs.mkdirSync(shots, { recursive: true });
const tiles = '/tiles/tileset.json';
const manifest = JSON.parse(fs.readFileSync(path.join(tileDist, 'manifest.json'), 'utf8'));

test.beforeEach(async ({ page, context }) => {
  await context.addInitScript(() => Object.defineProperty(navigator, 'getGamepads', { value: () => [] }));
  const session = await context.newCDPSession(page);
  await session.send('Emulation.setFocusEmulationEnabled', { enabled: true });
});

test('Auransilta from the west river bank', { tag: ['@pipeline', '@render'] }, async ({ page }, info) => {
  test.skip(!manifest.visual.t0_1_0 || manifest.visual.t0_1_0.tris < 1000, 'Requires assembled bridge-area tiles');
  test.setTimeout(150000);
  const backend = info.project.name;
  await page.goto(`/?backend=${backend}&tiles=${tiles}&cam=23459755,6704270,5.5,90,-5,60`);
  await page.waitForFunction(() => (window as any).__qaReady || (window as any).__qaError, null, { timeout: 125000 });
  expect(await page.evaluate(() => (window as any).__qaError)).toBeFalsy();
  await page.screenshot({ path: path.join(shots, `auransilta-river-bank-${backend}.png`) });
});

test('Auransilta walk and drive traversal', { tag: ['@pipeline', '@walk', '@drive'] }, async ({ page }, info) => {
  test.skip(!manifest.visual.t0_1_0 || manifest.visual.t0_1_0.tris < 1000, 'Requires assembled bridge-area tiles');
  test.setTimeout(180000);
  const backend = info.project.name;
  const browserErrors: string[] = [];
  page.on('pageerror', error => browserErrors.push(error.message));
  page.on('console', message => { if (message.type() === 'error') browserErrors.push(message.text()); });
  await page.goto(`/?backend=${backend}&tiles=${tiles}&spawn=23459785.5,6704302.7,145`);
  await page.waitForFunction(() => {
    const s = (window as any).__game?.state?.();
    return s?.grounded && s.collision.tiles >= 9;
  }, null, { timeout: 60000 });
  const start = await page.evaluate(() => (window as any).__game.state());
  expect(start.H).toBeGreaterThan(4.9);
  await page.screenshot({ path: path.join(shots, `auransilta-near-bank-${backend}.png`) });
  await page.keyboard.down('KeyW');
  await page.keyboard.down('ShiftLeft');
  try {
    await page.waitForFunction(() => {
      const s = (window as any).__game.state();
      return s.feet[0] > 305 && s.feet[2] > 228;
    }, null, { timeout: 60000 });
  } catch (error) {
    console.log(`[${backend}] stalled walker ${JSON.stringify(await page.evaluate(() => (window as any).__game.state()))}`);
    await page.screenshot({ path: path.join(shots, `auransilta-walk-stall-${backend}.png`) });
    throw error;
  }
  const middle = await page.evaluate(() => (window as any).__game.state());
  expect(middle.groundMaterial).toBe(24);
  await page.waitForFunction(() => {
    const s = (window as any).__game.state();
    return s.feet[0] > 325 && s.feet[2] > 254;
  }, null, { timeout: 60000 });
  await page.keyboard.up('KeyW');
  await page.keyboard.up('ShiftLeft');
  const walked = await page.evaluate(() => (window as any).__game.state());
  expect(walked.grounded).toBe(true);
  expect(walked.H).toBeGreaterThan(5.5);
  await page.screenshot({ path: path.join(shots, `auransilta-far-bank-${backend}.png`) });

  await page.evaluate(() => {
    const g = (window as any).__game;
    g.physics.resetVehicle({ x: 285.5, y: 6, z: 197.3 }, -0.95);
    g.physics.setCharacterEnabled(true, { x: 285.5, y: 6.3, z: 198.3 });
  });
  await page.waitForFunction(() => {
    const s = (window as any).__game.state();
    return Math.hypot(s.vehicle.pos[0] - s.feet[0], s.vehicle.pos[2] - s.feet[2]) < 4;
  });
  await page.keyboard.press('KeyF');
  await page.waitForFunction(() => (window as any).__game.state().mode === 'car');
  await page.keyboard.down('KeyW');
  try {
    await page.waitForFunction(() => (window as any).__game.state().vehicle.pos[2] > 254, null, { timeout: 60000 });
  } catch (error) {
    console.log(`[${backend}] stalled vehicle ${JSON.stringify((await page.evaluate(() => (window as any).__game.state())).vehicle)}`);
    await page.screenshot({ path: path.join(shots, `auransilta-drive-stall-${backend}.png`) });
    throw error;
  }
  await page.keyboard.up('KeyW');
  const driven = await page.evaluate(() => (window as any).__game.state());
  expect(driven.vehicle.pos[0]).toBeGreaterThan(325);
  expect(driven.vehicle.pos[1]).toBeGreaterThan(5.5);
  expect(driven.errors).toEqual([]);
  expect(browserErrors).toEqual([]);
  console.log(`[${backend}] Auransilta walk ${JSON.stringify({ from: start.feet, middle: middle.feet, to: walked.feet, material: middle.groundMaterial })}; drive ${JSON.stringify(driven.vehicle.pos)}`);
});

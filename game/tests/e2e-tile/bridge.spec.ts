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

test('Auransilta walk and drive traversal', { tag: ['@pipeline', '@walk', '@drive'] }, async ({ page }, info) => {
  test.skip(!manifest.visual.t0_1_0 || manifest.visual.t0_1_0.tris < 1000, 'Requires assembled bridge-area tiles');
  test.setTimeout(180000);
  const backend = info.project.name;
  const browserErrors: string[] = [];
  page.on('pageerror', error => browserErrors.push(error.message));
  page.on('console', message => { if (message.type() === 'error') browserErrors.push(message.text()); });
  await page.goto(`/?backend=${backend}&tiles=${tiles}&spawn=23459794,6704289,145`);
  await page.waitForFunction(() => {
    const s = (window as any).__game?.state?.();
    return s?.grounded && s.collision.tiles >= 9;
  }, null, { timeout: 60000 });
  const start = await page.evaluate(() => (window as any).__game.state());
  expect(start.H).toBeGreaterThan(5.5);
  await page.screenshot({ path: path.join(shots, `auransilta-near-bank-${backend}.png`) });
  await page.keyboard.down('KeyW');
  await page.keyboard.down('ShiftLeft');
  await page.waitForFunction(() => {
    const s = (window as any).__game.state();
    return s.feet[0] > 317 && s.feet[2] > 245;
  }, null, { timeout: 60000 });
  await page.keyboard.up('KeyW');
  await page.keyboard.up('ShiftLeft');
  const walked = await page.evaluate(() => (window as any).__game.state());
  expect(walked.grounded).toBe(true);
  expect(walked.H).toBeGreaterThan(5.5);
  expect(walked.groundMaterial).toBe(24);

  await page.evaluate(() => {
    const g = (window as any).__game;
    g.physics.resetVehicle({ x: 294, y: 7, z: 211 }, -0.95);
    g.physics.setCharacterEnabled(true, { x: 294, y: 6.3, z: 212 });
  });
  await page.waitForFunction(() => {
    const s = (window as any).__game.state();
    return Math.hypot(s.vehicle.pos[0] - s.feet[0], s.vehicle.pos[2] - s.feet[2]) < 4;
  });
  await page.keyboard.press('KeyF');
  await page.waitForFunction(() => (window as any).__game.state().mode === 'car');
  await page.keyboard.down('KeyW');
  try {
    await page.waitForFunction(() => (window as any).__game.state().vehicle.pos[2] > 245, null, { timeout: 60000 });
  } catch (error) {
    console.log(`[${backend}] stalled vehicle ${JSON.stringify((await page.evaluate(() => (window as any).__game.state())).vehicle)}`);
    throw error;
  }
  await page.keyboard.up('KeyW');
  const driven = await page.evaluate(() => (window as any).__game.state());
  expect(driven.vehicle.pos[0]).toBeGreaterThan(317);
  expect(driven.vehicle.pos[1]).toBeGreaterThan(5.5);
  expect(driven.errors).toEqual([]);
  expect(browserErrors).toEqual([]);
  console.log(`[${backend}] Auransilta walk ${JSON.stringify({ from: start.feet, to: walked.feet, material: walked.groundMaterial })}; drive ${JSON.stringify(driven.vehicle.pos)}`);
});

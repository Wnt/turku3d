import { expect, cachedTest as test } from '../e2e/fixtures';
import fs from 'node:fs';
import path from 'node:path';
import { reportsRoot, tileDist } from '../../../tools/dev/environment.mjs';
import { open, untilSim } from '../e2e/waits';

const shots = path.join(reportsRoot, 'content-tile-t0_1_-1-1027-1913');
const manifest = JSON.parse(fs.readFileSync(path.join(tileDist, 'manifest.json'), 'utf8'));

test('north tile streams when driven across the office area boundary', { tag: ['@pipeline', '@drive', '@streaming'] }, async ({ page }, info) => {
  const tile = manifest.visual['t0_1_-1'];
  expect(tile).toBeDefined();
  expect(tile.wire_bytes).toBeLessThan(8_000_000);
  test.setTimeout(240_000);
  const backend = info.project.name;
  fs.mkdirSync(shots, { recursive: true });
  const errors: string[] = [];
  page.on('pageerror', error => errors.push(error.message));
  await open(page, `/?backend=${backend}&quality=low&tiles=/tiles/tileset.json&spawn=23459838.3,6704479.6,123`);
  await page.getByRole('option', { name: 'New game' }).click();
  await untilSim(page, () => (window as any).__game?.state?.().grounded, null, 90);
  await page.evaluate(() => {
    const game = (window as any).__game;
    game.physics.resetVehicle({ x: 338.3, y: 5.8, z: 20.4 }, 2.17);
    game.physics.setCharacterEnabled(true, { x: 338.3, y: 6.1, z: 19.4 });
  });
  await untilSim(page, () => {
    const s = (window as any).__game.state();
    return Math.hypot(s.vehicle.pos[0] - s.feet[0], s.vehicle.pos[2] - s.feet[2]) < 4;
  }, null, 30);
  await page.keyboard.press('KeyF');
  await untilSim(page, () => (window as any).__game.state().mode === 'car', null, 30);
  const start = await page.evaluate(() => (window as any).__game.state().vehicle.pos);
  await page.screenshot({ path: path.join(shots, `${backend}-south-boundary.png`) });
  await page.keyboard.down('KeyW');
  try {
    await untilSim(page, () => (window as any).__game.state().vehicle.pos[2] < -4, null, 45);
  } finally {
    await page.keyboard.up('KeyW');
  }
  await untilSim(page, () => [...(window as any).__game.tiles.tiles.visibleTiles]
    .some((tile: any) => tile.extras?.id === 't0_1_-1'), null, 30);
  const end = await page.evaluate(() => (window as any).__game.state());
  expect(start[2]).toBeGreaterThan(0);
  expect(end.vehicle.pos[2]).toBeLessThan(0);
  expect(end.vehicle.pos[0]).toBeGreaterThan(250);
  expect(end.vehicle.pos[0]).toBeLessThan(500);
  expect(end.errors).toEqual([]);
  expect(errors).toEqual([]);
  await page.screenshot({ path: path.join(shots, `${backend}-north-tile.png`) });
  fs.writeFileSync(path.join(shots, `${backend}-drive.json`), JSON.stringify({ start, end: end.vehicle.pos,
    visible: [...await page.evaluate(() => [...(window as any).__game.tiles.tiles.visibleTiles].map((tile: any) => tile.extras?.id))] }, null, 2));
});

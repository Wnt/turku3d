import { test, expect } from '@playwright/test';
import fs from 'node:fs';
import path from 'node:path';
import { tileDist } from '../../../tools/dev/environment.mjs';

const manifest = JSON.parse(fs.readFileSync(path.join(tileDist, 'manifest.json'), 'utf8'));

test('published parked car enters with F and drives on both backends', { tag: ['@drive', '@pipeline'] }, async ({ page }, info) => {
  test.setTimeout(240_000);
  test.skip(!manifest.vehicles?.length, 'No published parked vehicles');
  const car = manifest.vehicles.find((v: any) => v.model === 'van' && Math.abs(v.x - 140) < 2) ?? manifest.vehicles[0];
  await page.goto(`/?backend=${info.project.name}&tiles=/tiles/tileset.json`);
  await page.waitForFunction(count => (window as any).__game?.state?.().vehicles?.length === count + 1, manifest.vehicles.length);
  await page.evaluate(c => (window as any).__game.physics.setCharacterEnabled(true,
    { x: c.x - 2.5, y: c.y, z: c.z }), car);
  await page.keyboard.down('KeyW');
  await page.waitForTimeout(250);
  await page.keyboard.up('KeyW');
  await page.waitForFunction(id => {
    const s = (window as any).__game.state();
    const car = s.vehicles.find((v: any) => v.id === id);
    return Math.hypot(s.feet[0] - car.pos[0], s.feet[2] - car.pos[2]) < 4;
  }, car.object_id);
  await page.keyboard.press('KeyF');
  await page.waitForFunction(id => (window as any).__game.state().drivenId === id, car.object_id);
  await page.evaluate(() => {
    const w = window as any;
    w.drivenMeters = 0;
    let previous = w.__game.state().vehicle.pos;
    w.distanceTimer = setInterval(() => {
      const current = w.__game.state().vehicle.pos;
      w.drivenMeters += Math.hypot(current[0] - previous[0], current[2] - previous[2]);
      previous = current;
    }, 50);
  });
  const start = await page.evaluate(() => (window as any).__game.state().vehicle.pos);
  await page.keyboard.down('KeyW');
  await page.waitForTimeout(4500);
  await page.keyboard.up('KeyW');
  await page.keyboard.down('KeyS');
  await page.waitForFunction(() => (window as any).drivenMeters > 20, null, { timeout: 60_000 });
  await page.keyboard.up('KeyS');
  await page.keyboard.down('KeyW');
  await page.waitForFunction(() => (window as any).__game.state().vehicle.speed < 0.1);
  await page.keyboard.up('KeyW');
  await page.keyboard.press('KeyF');
  await page.waitForFunction(() => (window as any).__game.state().mode === 'foot');
  const final = await page.evaluate(id => {
    const w = window as any;
    clearInterval(w.distanceTimer);
    return w.__game.state().vehicles.find((v: any) => v.id === id).pos;
  }, car.object_id);
  expect(final).toBeTruthy();
  expect(await page.evaluate(() => (window as any).drivenMeters)).toBeGreaterThan(20);
  expect(Math.hypot(final[0] - start[0], final[2] - start[2])).toBeGreaterThan(1);
});

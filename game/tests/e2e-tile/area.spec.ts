import { test, expect } from '@playwright/test';
import fs from 'node:fs';
import path from 'node:path';
import { reportsRoot, tileDist } from '../../../tools/dev/environment.mjs';

const manifest = JSON.parse(fs.readFileSync(path.join(tileDist, 'manifest.json'), 'utf8'));
const ready = () => (window as any).__qaReady || (window as any).__qaError;
type Pose = { name: string; tile: string; x: number; z: number; h: number; heading: number };
const poses: Pose[] = JSON.parse(fs.readFileSync(path.join(import.meta.dirname, 'viewpoints.json'), 'utf8'));

async function move(page: import('@playwright/test').Page, pose: Pose, pitch = 0) {
  await page.evaluate(({ x, z, h, heading, pitch }) => {
    (window as any).__qaMove({ E: 23459500 + x, N: 6704500 - z, H: h,
      yaw: heading, pitch, fov: 60 });
  }, { ...pose, pitch });
  await page.waitForFunction(ready, null, { timeout: 45_000 });
  expect(await page.evaluate(() => (window as any).__qaError), pose.name).toBeFalsy();
}

test('four-tile street draw budget', { tag: ['@pipeline', '@render'] }, async ({ page }, info) => {
  test.skip(!manifest.visual.t0_1_1 || manifest.visual.t0_1_1.tris < 1000, 'Requires assembled AOI');
  test.setTimeout(300_000);
  expect(new Set(poses.map(p => p.tile))).toEqual(new Set(['t0_0_0', 't0_0_1', 't0_1_0', 't0_1_1']));
  const bad: string[] = [];
  page.on('pageerror', e => bad.push(e.message));
  await page.setViewportSize({ width: 2560, height: 1440 });
  const backend = info.project.name;
  const first = poses[0];
  await page.goto(`/?backend=${backend}&tiles=/tiles/tileset.json&qa=1&cam=${23459500 + first.x},${6704500 - first.z},${first.h},${first.heading},0,60`);
  await page.waitForFunction(ready, null, { timeout: 90_000 });
  const rows = [];
  for (const pose of poses) {
    await move(page, pose);
    const sample = await page.evaluate(() => ({
      draws: (window as any).__perf.drawCalls,
      triangles: (window as any).__perf.triangles,
      residentTriangles: (window as any).__game.tiles.residentTris,
    }));
    rows.push({ ...pose, ...sample });
  }
  const maximum = rows.reduce((a, b) => a.draws > b.draws ? a : b);
  await move(page, maximum);
  const breakdown = await page.evaluate(() => {
    const rows: Record<string, number> = {};
    (window as any).__game.tiles.tiles.group.traverse((mesh: any) => {
      if (!mesh.isMesh || !mesh.visible) return;
      let owner = mesh;
      while (owner && !owner.userData?.layer) owner = owner.parent;
      const key = `${owner?.userData?.layer ?? 'unknown'}/${mesh.material?.userData?.material_key ?? 'unknown'}`;
      rows[key] = (rows[key] ?? 0) + 1;
    });
    return rows;
  });
  const aerial = { name: 'aerial', tile: 'all', x: 250, z: 250, h: 120, heading: 330 };
  await move(page, aerial, -55);
  const aerialSample = await page.evaluate(() => ({ draws: (window as any).__perf.drawCalls,
    triangles: (window as any).__perf.triangles }));
  const report = { backend, maximum, breakdown, street: rows, aerial: { ...aerial, ...aerialSample } };
  fs.mkdirSync(reportsRoot, { recursive: true });
  fs.writeFileSync(path.join(reportsRoot, `tile-area-${backend}.json`), JSON.stringify(report, null, 2));
  console.log(`[${backend}] four-tile draws max ${maximum.draws} at ${maximum.name} E ${23459500 + maximum.x} N ${6704500 - maximum.z}; aerial ${aerialSample.draws}; street samples ${rows.length}`);
  await info.attach('area-performance', { body: JSON.stringify(report), contentType: 'application/json' });
  expect(bad).toEqual([]);
  expect(maximum.draws, `${maximum.name} at E ${23459500 + maximum.x} N ${6704500 - maximum.z}`).toBeLessThan(500);
});

test('facade ID pixels across the playable area', { tag: ['@pipeline', '@qa', '@render'] }, async ({ page }, info) => {
  test.skip(!manifest.visual.t0_1_1 || manifest.visual.t0_1_1.tris < 1000, 'Requires assembled AOI');
  test.setTimeout(180_000);
  await page.setViewportSize({ width: 1280, height: 720 });
  const first = poses[0];
  await page.goto(`/?backend=${info.project.name}&tiles=/tiles/tileset.json&qa=1&pass=id&cam=${23459500 + first.x},${6704500 - first.z},${first.h},${first.heading},0,60`);
  await page.waitForFunction(ready, null, { timeout: 90_000 });
  for (const name of ['northwest-2', 'southwest-3', 'northeast-2', 'southeast-3']) {
    const pose = poses.find(p => p.name === name)!;
    await move(page, pose);
    const png = await page.screenshot();
    const count = await page.evaluate(async b64 => {
      const image = new Image(); image.src = `data:image/png;base64,${b64}`; await image.decode();
      const canvas = document.createElement('canvas'); canvas.width = image.width; canvas.height = image.height;
      const context = canvas.getContext('2d')!; context.drawImage(image, 0, 0);
      const pixels = context.getImageData(0, 0, image.width, image.height).data;
      const table = (window as any).__qaIdTable;
      let walls = 0;
      for (let k = 0; k < pixels.length; k += 16) {
        const id = pixels[k] * 65536 + pixels[k + 1] * 256 + pixels[k + 2];
        if (table[id]?.kind === 'building' && table[id]?.facade !== undefined) walls++;
      }
      return walls;
    }, png.toString('base64'));
    console.log(`[${info.project.name}] ${name} facade ID sample pixels ${count}`);
    expect(count, name).toBeGreaterThan(250);
  }
});

test('bridge gameplay draw budget', { tag: ['@pipeline', '@render', '@drive'] }, async ({ page, context }, info) => {
  test.skip(!manifest.visual.t0_1_0 || manifest.visual.t0_1_0.tris < 1000, 'Requires assembled bridge tile');
  test.setTimeout(120_000);
  await context.addInitScript(() => Object.defineProperty(navigator, 'getGamepads', { value: () => [] }));
  await page.setViewportSize({ width: 2560, height: 1440 });
  await page.goto(`/?backend=${info.project.name}&tiles=/tiles/tileset.json&spawn=23459783,6704299,300`);
  await page.getByRole('option', { name: 'New game' }).click();
  await page.waitForFunction(() => {
    const state = (window as any).__game?.state?.();
    return state?.grounded && state.tilesSettled && state.visTris > 100_000;
  }, null, { timeout: 90_000 });
  await page.evaluate(() => (window as any).__render.perf.reset());
  await page.waitForTimeout(1500);
  const sample = await page.evaluate(() => {
    const meshes: Record<string, number> = {};
    const materials: Record<string, string[]> = {};
    (window as any).__game.tiles.tiles.group.traverse((mesh: any) => {
      if (!mesh.isMesh || !mesh.visible) return;
      let parent = mesh;
      while (parent && !parent.userData?.layer) parent = parent.parent;
      const key = `${parent?.userData?.layer ?? 'unknown'}/${mesh.material?.userData?.material_key ?? 'unknown'}`;
      meshes[key] = (meshes[key] ?? 0) + 1;
      (materials[key] ??= []).push(mesh.material?.uuid ?? 'array');
    });
    return { perf: (window as any).__perf, state: (window as any).__game.state(), meshes,
      materialCounts: Object.fromEntries(Object.entries(materials).map(([key, values]) => [key, new Set(values).size])),
      visibleTiles: [...(window as any).__game.tiles.tiles.visibleTiles].map((tile: any) => tile.extras?.id) };
  });
  console.log(`[${info.project.name}] bridge gameplay draws ${sample.perf.drawCalls} triangles ${sample.perf.triangles} meshes ${JSON.stringify(sample.meshes)} materials ${JSON.stringify(sample.materialCounts)} visible ${sample.visibleTiles}`);
  await info.attach('bridge-gameplay-performance', { body: JSON.stringify(sample), contentType: 'application/json' });
  expect(sample.perf.drawCalls).toBeLessThan(500);
});

import { isolatedTest as test, expect } from '../e2e/fixtures';
import fs from 'node:fs';
import path from 'node:path';
import { reportsRoot, tileDist } from '../../../tools/dev/environment.mjs';
import { frames } from '../e2e/waits';

const manifest = JSON.parse(fs.readFileSync(path.join(tileDist, 'manifest.json'), 'utf8'));
const shots = path.join(reportsRoot, 'screenshots');
fs.mkdirSync(shots, { recursive: true });

function inside(point: number[], ring: number[][]) {
  let result = false;
  for (let i = 0, j = ring.length - 1; i < ring.length; j = i++) {
    const [x, z] = ring[i], [xx, zz] = ring[j];
    if ((z > point[1]) !== (zz > point[1]) && point[0] < (xx - x) * (point[1] - z) / (zz - z) + x) result = !result;
  }
  return result;
}

test('office integration: street spawn, L0, batched draws and visible facade pixels', { tag: ['@pipeline', '@render', '@walk'] }, async ({ page }, info) => {
  // Synthetic ground-only tile suites do not claim office/building acceptance.
  test.skip(!manifest.visual.t0_0_0 || manifest.visual.t0_0_0.tris < 1000, 'Requires the assembled office layers');
  expect(manifest.visual.t0_0_0.ground_texture_bytes, 'office ground must have a published texture binding').toBeGreaterThan(0);
  expect(manifest.spawn, 'assembly must publish a data-derived street spawn').toBeTruthy();
  const bad: string[] = [];
  page.on('pageerror', e => bad.push(e.message));
  page.on('console', m => { if (m.type() === 'error') bad.push(m.text()); });
  await page.setViewportSize({ width: 2560, height: 1440 });
  const backend = info.project.name;
  await page.goto(`/?backend=${backend}&tiles=/tiles/tileset.json`);
  await page.getByRole('button', { name: 'Start playing' }).click();
  await page.waitForFunction(() => {
    const game = (window as any).__game;
    const s = game?.state?.();
    const visible = game?.tiles.tiles.visibleTiles;
    return s?.grounded && s.tilesSettled && s.visTris > 100_000 &&
      [...(visible ?? [])].some((tile: any) => tile.extras?.id?.startsWith('t0_'));
  }, null, { timeout: 60_000 });
  const state = await page.evaluate(() => (window as any).__game.state());
  for (const footprint of manifest.spawn.footprints) {
    expect(inside([state.feet[0], state.feet[2]], footprint.coordinates[0])).toBe(false);
  }
  expect(state.groundMaterial).not.toBe(23); // roof
  expect(Math.abs(state.H - manifest.spawn.position[1])).toBeLessThan(0.2);
  const visible = await page.evaluate(() => [...(window as any).__game.tiles.tiles.visibleTiles].map((t: any) => t.extras?.id));
  expect(visible.some((id: string) => id.startsWith('t0_'))).toBe(true);
  await page.evaluate(() => (window as any).__render.perf.reset());
  await frames(page, 30);
  const perf = await page.evaluate(() => (window as any).__perf);
  const draws = await page.evaluate(() => {
    const rows: Record<string, number> = {};
    (window as any).__game.tiles.tiles.group.traverse((object: any) => {
      if (!object.isMesh || !object.visible) return;
      let parent = object;
      while (parent && !parent.userData?.layer) parent = parent.parent;
      const layer = parent?.userData?.layer ?? 'unknown';
      const material = object.material?.userData?.material_key ?? parent?.userData?.material_key ?? 'unknown';
      const key = `${layer}/${material}`;
      rows[key] = (rows[key] ?? 0) + (Array.isArray(object.material) ? object.material.length : 1);
    });
    return rows;
  });
  console.log(`[${backend}] visible mesh draws by layer/material ${JSON.stringify(draws)}`);
  expect(perf.drawCalls).toBeLessThan(500);
  await page.screenshot({ path: path.join(shots, `integration-${backend}-street.png`) });
  await info.attach('1440p-performance', { body: JSON.stringify(perf, null, 2), contentType: 'application/json' });
  console.log(`[${backend}] office ${JSON.stringify({ feet: state.feet, tris: state.visTris, visible, perf })}`);

  // Exercise the actual chase camera after entering the nearby parked vehicle.
  await page.evaluate(() => {
    const game = (window as any).__game;
    const p = game.state().vehicle.pos;
    game.physics.setCharacterEnabled(true, { x: p[0], y: p[1], z: p[2] + 2 });
  });
  await page.waitForFunction(() => {
    const s = (window as any).__game.state();
    return Math.hypot(s.feet[0] - s.vehicle.pos[0], s.feet[2] - s.vehicle.pos[2]) < 3;
  });
  await page.keyboard.press('KeyF');
  await page.waitForFunction(() => (window as any).__game.state().mode === 'car');
  await frames(page, 10);
  await page.screenshot({ path: path.join(shots, `integration-${backend}-car.png`) });
  expect(bad).toEqual([]);
});

test('office facades keep visible ID pixels and QA views', { tag: ['@pipeline', '@qa', '@render'] }, async ({ page }, info) => {
  test.skip(!manifest.visual.t0_0_0 || manifest.visual.t0_0_0.tris < 1000, 'Requires the assembled office layers');
  expect(manifest.spawn).toBeTruthy();
  const bad: string[] = [];
  page.on('pageerror', e => bad.push(e.message));
  page.on('console', m => { if (m.type() === 'error') bad.push(m.text()); });
  await page.setViewportSize({ width: 2560, height: 1440 });
  const backend = info.project.name;
  const [x, y, z] = manifest.spawn.position;
  const heading = manifest.spawn.headingDeg;
  const bearing = heading * Math.PI / 180;
  const viewX = x - Math.sin(bearing) * 10, viewZ = z + Math.cos(bearing) * 10;
  for (const pass of ['color', 'id']) {
    await page.goto(`/?backend=${backend}&tiles=/tiles/tileset.json&qa=1&pass=${pass}&cam=${23459500 + viewX},${6704500 - viewZ},${y + 1.7},${heading},5,60`);
    await page.waitForFunction(() => (window as any).__qaReady || (window as any).__qaError);
    expect(await page.evaluate(() => (window as any).__qaError)).toBeFalsy();
    const png = await page.screenshot({ path: path.join(shots, `integration-${backend}-facade-${pass}.png`) });
    if (pass === 'id') {
      const pixels = await page.evaluate(async b64 => {
        const img = new Image(); img.src = `data:image/png;base64,${b64}`; await img.decode();
        const canvas = document.createElement('canvas'); canvas.width = img.width; canvas.height = img.height;
        const ctx = canvas.getContext('2d')!; ctx.drawImage(img, 0, 0);
        const data = ctx.getImageData(0, 0, canvas.width, canvas.height).data;
        const table = (window as any).__qaIdTable;
        let walls = 0;
        for (let k = 0; k < data.length; k += 4) {
          const id = data[k] * 65536 + data[k + 1] * 256 + data[k + 2];
          if (table[id]?.kind === 'building' && table[id]?.facade !== undefined) walls++;
        }
        return walls;
      }, png.toString('base64'));
      expect(pixels).toBeGreaterThan(100_000);
    }
  }
  await page.goto(`/?backend=${backend}&tiles=/tiles/tileset.json&qa=1&cam=${23459500 + x + 60},${6704500 - z - 80},100,325,-45,60`);
  await page.waitForFunction(() => (window as any).__qaReady || (window as any).__qaError);
  expect(await page.evaluate(() => (window as any).__qaError)).toBeFalsy();
  await page.screenshot({ path: path.join(shots, `integration-${backend}-aerial.png`) });
  expect(bad).toEqual([]);
});

test('office ground texture keeps detail in QA views', { tag: ['@pipeline', '@qa', '@render'] }, async ({ page }, info) => {
  test.skip(!manifest.visual.t0_0_0 || manifest.visual.t0_0_0.tris < 1000, 'Requires the assembled office layers');
  expect(manifest.spawn).toBeTruthy();
  const bad: string[] = [];
  page.on('pageerror', e => bad.push(e.message));
  page.on('console', m => { if (m.type() === 'error') bad.push(m.text()); });
  await page.setViewportSize({ width: 2560, height: 1440 });
  const backend = info.project.name;
  const [x, y, z] = manifest.spawn.position;
  const heading = manifest.spawn.headingDeg;
  const groundCamera = `${23459500 + x},${6704500 - z},${y + 5},${heading},-80,60`;
  const captures: Record<string, Buffer> = {};
  let groundIds: Record<string, { kind?: string }> = {};
  for (const pass of ['id', 'color']) {
    await page.goto(`/?backend=${backend}&tiles=/tiles/tileset.json&qa=1&pass=${pass}&cam=${groundCamera}`);
    await page.waitForFunction(() => (window as any).__qaReady || (window as any).__qaError);
    expect(await page.evaluate(() => (window as any).__qaError)).toBeFalsy();
    if (pass === 'id') groundIds = await page.evaluate(() => (window as any).__qaIdTable);
    captures[pass] = await page.screenshot({ path: path.join(shots, `integration-${backend}-ground-${pass}.png`) });
  }
  const texturedMeshes = await page.evaluate(() => {
    let count = 0;
    (window as any).__game.tiles.tiles.group.traverse((node: any) => {
      if (node.isMesh && node.material?.name === 'ground-ortho-stochastic-v1') count++;
    });
    return count;
  });
  expect(texturedMeshes, 'runtime must apply the ground KTX2 material').toBeGreaterThan(0);
  const groundPixels = await page.evaluate(async ([idB64, colorB64, table]) => {
    const decode = async (b64: string) => {
      const image = new Image(); image.src = `data:image/png;base64,${b64}`; await image.decode();
      const canvas = document.createElement('canvas'); canvas.width = image.width; canvas.height = image.height;
      const context = canvas.getContext('2d')!; context.drawImage(image, 0, 0);
      return context.getImageData(0, 0, image.width, image.height).data;
    };
    const ids = await decode(idB64), colors = await decode(colorB64);
    const unique = new Set<number>();
    let count = 0;
    for (let k = 0; k < ids.length; k += 4 * 8) {
      const id = ids[k] * 65536 + ids[k + 1] * 256 + ids[k + 2];
      if (table[id]?.kind !== 'ground') continue;
      count++;
      unique.add((colors[k] >> 3 << 10) | (colors[k + 1] >> 3 << 5) | (colors[k + 2] >> 3));
    }
    return { count, colors: unique.size };
  }, [captures.id.toString('base64'), captures.color.toString('base64'), groundIds] as const);
  expect(groundPixels.count).toBeGreaterThan(10_000);
  expect(groundPixels.colors, 'ground pixels near spawn must show texture detail').toBeGreaterThan(30);
  expect(bad).toEqual([]);
});

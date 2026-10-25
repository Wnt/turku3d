import { test, expect } from '@playwright/test';
import fs from 'node:fs';
import path from 'node:path';
import { reportsRoot, tileDist } from '../../../tools/dev/environment.mjs';

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

test('office integration: street spawn, L0, batched draws and visible facade pixels', { tag: ['@pipeline', '@render', '@walk'] }, async ({ page, context }, info) => {
  // Synthetic ground-only tile suites do not claim office/building acceptance.
  test.skip(!manifest.visual.t0_0_0 || manifest.visual.t0_0_0.tris < 1000, 'Requires the assembled office layers');
  expect(manifest.spawn, 'assembly must publish a data-derived street spawn').toBeTruthy();
  const bad: string[] = [];
  page.on('pageerror', e => bad.push(e.message));
  page.on('console', m => { if (m.type() === 'error') bad.push(m.text()); });
  await context.addInitScript(() => Object.defineProperty(navigator, 'getGamepads', { value: () => [] }));
  const session = await context.newCDPSession(page);
  await session.send('Emulation.setFocusEmulationEnabled', { enabled: true });
  await page.setViewportSize({ width: 2560, height: 1440 });
  const backend = info.project.name;
  await page.goto(`/?backend=${backend}&tiles=/tiles/tileset.json`);
  await page.waitForFunction(() => {
    const s = (window as any).__game?.state?.();
    return s?.grounded && s.tilesSettled && s.visTris > 100_000;
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
  await page.waitForTimeout(4000);
  const perf = await page.evaluate(() => (window as any).__perf);
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
  await page.waitForTimeout(300);
  await page.keyboard.press('KeyF');
  await page.waitForFunction(() => (window as any).__game.state().mode === 'car');
  await page.waitForTimeout(1000);
  await page.screenshot({ path: path.join(shots, `integration-${backend}-car.png`) });

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

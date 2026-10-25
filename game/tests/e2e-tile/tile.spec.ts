import { settled } from '../e2e/waits';
// Tile packaging acceptance (ARCHITECTURE §4): Chrome shows the pipeline-assembled tile (dist/tiles from
// `make tile`, built locally) and the character walks on its collision. Also checks the qa/README.md §2
// render contract on the real tile: pose echo, __qaProject, and the object-ID pass.
// Run: npx playwright test -c playwright.tile.config.ts   (after `npm run build`; TILE_DIST=<dir> optional)
import { test, expect, type Page, type ConsoleMessage } from '@playwright/test';
import fs from 'node:fs';
import { reportsRoot, tileDist } from '../../../tools/dev/environment.mjs';
import path from 'node:path';

const TILE_DIST = tileDist;
const SHOTS = path.join(reportsRoot, 'screenshots');
fs.mkdirSync(SHOTS, { recursive: true });

// Keyboard routes must be independent of physical controllers connected to the shared host.
// Controller routes provide their own virtual gamepad in gamepad.spec.ts.
test.beforeEach(async ({ context, page }) => {
  const session = await context.newCDPSession(page);
  await session.send('Emulation.setFocusEmulationEnabled', { enabled: true });
  await context.addInitScript(() => {
    Object.defineProperty(navigator, 'getGamepads', { value: () => [] });
  });
});
const manifest = JSON.parse(fs.readFileSync(path.join(TILE_DIST, 'manifest.json'), 'utf8'));
const TILES = '/tiles/tileset.json';
// The address point lies inside the office footprint under its roof. Walk on the
// mapped sidewalk east of it, where the next 8 m north stays clear of buildings.
const WALK_START = { E: 23459685, N: 6704256 };
// a QA pose inside t0_0_0 (the office_front registry view stands 7 m south of it, in t0_0_1): at the office,
// looking north-west and slightly down, 1280x960 like the registry views
const VIEW = { E: 23459668, N: 6704256, H: 6.5, yaw: 324.819, pitch: -10, fov: 60, w: 1280, h: 960 };
const rad = Math.PI / 180;

function watchConsole(page: Page) {
  const bad: string[] = [];
  page.on('console', (m: ConsoleMessage) => { if (m.type() === 'error') bad.push(m.text()); });
  page.on('pageerror', (e) => bad.push(`pageerror: ${e.message}`));
  return bad;
}

async function decodePng(page: Page, png: Buffer): Promise<{ w: number; h: number; px: Uint8Array }> {
  // RGBA pixels come back base64-encoded (a JSON number array of 5M entries takes ~7 s per image)
  const r = await page.evaluate(async (b64) => {
    const img = new Image();
    img.src = `data:image/png;base64,${b64}`;
    await img.decode();
    const c = document.createElement('canvas');
    c.width = img.width; c.height = img.height;
    const g = c.getContext('2d', { colorSpace: 'srgb' })!;
    g.drawImage(img, 0, 0);
    const d = g.getImageData(0, 0, img.width, img.height).data;
    let bin = '';
    for (let k = 0; k < d.length; k += 0x8000) bin += String.fromCharCode(...d.subarray(k, k + 0x8000));
    return { w: img.width, h: img.height, b64: btoa(bin) };
  }, png.toString('base64'));
  return { w: r.w, h: r.h, px: new Uint8Array(Buffer.from(r.b64, 'base64')) };
}

test('assembled tile from the pipeline renders and the character walks on its collision', { tag: ['@walk', '@pipeline', '@ground', '@character', '@streaming', '@aoi'] }, async ({ page }, info) => {
  test.setTimeout(180_000);
  const backend = info.project.name;
  const bad = watchConsole(page);
  expect(Object.keys(manifest.visual).length).toBeGreaterThan(0);
  await page.goto(`/?backend=${backend}&tiles=${TILES}&spawn=${WALK_START.E},${WALK_START.N},0`);
  await page.getByRole('button', { name: 'Start playing' }).click();
  await page.waitForFunction(() => (window as any).__game?.ready === true, null, { timeout: 60_000 });
  await page.waitForFunction(() => { const s = (window as any).__game.state(); return s.tilesSettled && s.collision.tiles > 0 && s.grounded; }, null, { timeout: 90_000 });
  const start = await page.evaluate(() => (window as any).__game.state());
  const vis = manifest.visual.t0_0_0;
  expect(start.visTris).toBeGreaterThanOrEqual(vis.tris);
  expect(start.collision.tiles).toBeGreaterThan(0); // streaming continues in the background (4 in flight)
  expect(Math.abs(start.E - WALK_START.E)).toBeLessThan(0.5);
  expect(Math.abs(start.N - WALK_START.N)).toBeLessThan(0.5);
  expect(start.H).toBeGreaterThan(vis.h[0] - 0.1);
  expect(start.H).toBeLessThan(vis.h[1] + 0.1);
  expect(start.groundMaterial).toBeGreaterThan(0);
  await page.screenshot({ path: `${SHOTS}/tile-${backend}-start.png` });

  await page.keyboard.down('KeyW');
  await page.waitForFunction((s0) => { const s = (window as any).__game.state(); return Math.hypot(s.feet[0] - s0[0], s.feet[2] - s0[2]) >= 8; }, start.feet, { timeout: 60_000 });
  await page.keyboard.up('KeyW');
  await settled(page);
  const end = await page.evaluate(() => (window as any).__game.state());
  console.log(`[${backend}] assembled tile: walked ${Math.hypot(end.feet[0] - start.feet[0], end.feet[2] - start.feet[2]).toFixed(2)} m, `
    + `N ${start.N.toFixed(2)} -> ${end.N.toFixed(2)}, H ${start.H.toFixed(3)} -> ${end.H.toFixed(3)}, ground mat ${end.groundMaterial}, `
    + `vis tris ${end.visTris}, col tiles ${end.collision.tiles}, frame ${end.frameMs.toFixed(1)} ms`);
  expect(end.N - start.N).toBeGreaterThan(7);
  // every collision tile within the 200 m streaming radius gets loaded (>= 9 around the office)
  await page.waitForFunction(() => (window as any).__game.state().collision.tiles >= 9, null, { timeout: 15_000 });
  expect(end.grounded).toBe(true);
  expect(Math.abs(end.H - start.H)).toBeLessThan(2); // terrain follows the source ground, no fall-through
  await page.screenshot({ path: `${SHOTS}/tile-${backend}-walked.png` });
  expect(bad).toEqual([]);
  expect(await page.evaluate(() => (window as any).__game.errors)).toEqual([]);
});

test('QA render contract on the assembled tile: pose echo, projection, ID pass', { tag: ['@qa', '@render', '@aoi'] }, async ({ browser }, info) => {
  const backend = info.project.name;
  const ctx = await browser.newContext({ viewport: { width: VIEW.w, height: VIEW.h }, deviceScaleFactor: 1 });
  const page = await ctx.newPage();
  const bad = watchConsole(page);
  const cam = `cam=${VIEW.E},${VIEW.N},${VIEW.H},${VIEW.yaw},${VIEW.pitch},${VIEW.fov}`;
  // a point 10 m straight ahead must project to the image centre; a point behind the camera to null
  const f = [Math.sin(VIEW.yaw * rad) * Math.cos(VIEW.pitch * rad), Math.cos(VIEW.yaw * rad) * Math.cos(VIEW.pitch * rad), Math.sin(VIEW.pitch * rad)];
  const ahead = [VIEW.E + 10 * f[0], VIEW.N + 10 * f[1], VIEW.H + 10 * f[2]];
  const behind = [VIEW.E - 5 * f[0], VIEW.N - 5 * f[1], VIEW.H];
  for (const pass of ['color', 'id'] as const) {
    await page.goto(`/?backend=${backend}&tiles=${TILES}&${cam}&qa=1&pass=${pass}&time=2019-06-15T14:00:00%2B03:00`);
    await page.waitForFunction(() => (window as any).__qaReady === true || (window as any).__qaError, null, { timeout: 60_000 });
    const st = await page.evaluate(([ahead, behind]) => ({ err: (window as any).__qaError, pose: (window as any).__qaPose, info: (window as any).__qaInfo,
      ids: (window as any).__qaIdTable, inner: [innerWidth, innerHeight], canvas: [document.querySelector('canvas')!.width, document.querySelector('canvas')!.height],
      // a point straight ahead must project to the image centre; a point behind the camera to null
      ahead: (window as any).__qaProject(...ahead), behind: (window as any).__qaProject(...behind) }), [ahead, behind]);
    expect(st.err).toBeFalsy();
    expect(st.pose).toEqual({ E: VIEW.E, N: VIEW.N, H: VIEW.H, yaw: VIEW.yaw, pitch: VIEW.pitch, roll: 0, fov: VIEW.fov });
    expect(st.inner).toEqual([VIEW.w, VIEW.h]);
    expect(st.canvas).toEqual([VIEW.w, VIEW.h]);
    expect(st.ahead[0]).toBeCloseTo(VIEW.w / 2, 1);
    expect(st.ahead[1]).toBeCloseTo(VIEW.h / 2, 1);
    expect(st.behind).toBeNull();
    expect(st.info.tiles).toContain('t0_0_0');
    const png = await page.screenshot({ path: `${SHOTS}/tile-${backend}-qa-${pass}.png` });
    if (pass === 'color') {
      // not blank: the lower half (ground, ortho-draped) differs from the sky and has real texture detail
      const { w, h, px } = await decodePng(page, png);
      const lum = (x: number, y: number) => { const k = 4 * (y * w + x); return (px[k] + px[k + 1] + px[k + 2]) / 3; };
      const sky = lum(w >> 1, 5), ground = lum(w >> 1, h - 5);
      const cols = new Set<number>();
      for (let y = h >> 1; y < h; y += 7) for (let x = 0; x < w; x += 7) { const k = 4 * (y * w + x); cols.add((px[k] >> 3 << 10) | (px[k + 1] >> 3 << 5) | (px[k + 2] >> 3)); }
      console.log(`[${backend}] color pass: sky lum ${sky.toFixed(0)}, ground lum ${ground.toFixed(0)}, ${cols.size} colours in the lower half`);
      expect(Math.abs(sky - ground)).toBeGreaterThan(10);
      expect(cols.size).toBeGreaterThan(50);
    }
    if (pass === 'id') {
      const { w, h, px } = await decodePng(page, png);
      expect([w, h]).toEqual([VIEW.w, VIEW.h]);
      const at = (x: number, y: number) => { const k = 4 * (y * w + x); return (px[k] << 16) | (px[k + 1] << 8) | px[k + 2]; };
      const ground = at(w / 2, h - 5); // bottom centre looks at the ground
      console.log(`[${backend}] id pass: bottom-centre id ${ground} -> ${JSON.stringify(st.ids[String(ground)])}`);
      expect(st.ids[String(ground)]).toMatchObject({ kind: 'ground', tile: 't0_0_0' });
      // every pixel is either sky (0) or a listed id: no antialiasing / tone mapping / sRGB drift
      const seen = new Set<number>();
      for (let k = 0; k < px.length; k += 4 * 97) seen.add((px[k] << 16) | (px[k + 1] << 8) | px[k + 2]);
      for (const v of seen) if (v !== 0) expect(st.ids[String(v)], `id ${v}`).toBeTruthy();
    }
  }
  expect(bad).toEqual([]);
  await ctx.close();
});

test('character crosses all four L0 seams without a height step', { tag: ['@walk', '@pipeline', '@ground', '@character', '@streaming', '@aoi'] }, async ({ page }, info) => {
  const required = ['t0_0_0', 't0_1_0', 't0_1_1', 't0_0_1'];
  test.skip(required.some((id) => !manifest.visual[id]), 'Requires the full four-tile Phase 1 build');
  const bad = watchConsole(page);
  // A 12 m square around the four-tile junction, on the coarse river heightfield.
  // Start east of the junction so the car parked 6 m east stays outside the route.
  await page.goto(`/?backend=${info.project.name}&tiles=${TILES}&spawn=23459756,6704256,0`);
  await page.getByRole('button', { name: 'Start playing' }).click();
  await page.waitForFunction(() => {
    const s = (window as any).__game?.state?.();
    return s?.tilesSettled && s.grounded && s.collision.tiles >= 16;
  }, null, { timeout: 60_000 });
  const visited = new Set<string>();
  const heights: number[] = [];
  for (const [key, axis, direction] of [['KeyS', 2, 1], ['KeyA', 0, -1], ['KeyW', 2, -1], ['KeyD', 0, 1]] as const) {
    const start = await page.evaluate(() => (window as any).__game.state());
    await page.keyboard.down(key);
    await page.waitForFunction(({ axis, direction, from }) => {
      const s = (window as any).__game.state();
      return direction * (s.feet[axis] - from) >= 12;
    }, { axis, direction, from: start.feet[axis] }, { timeout: 60_000 });
    await page.keyboard.up(key);
    await settled(page);
    const end = await page.evaluate(() => (window as any).__game.state());
    visited.add(`t0_${Math.floor(end.feet[0] / 250)}_${Math.floor(end.feet[2] / 250)}`);
    heights.push(start.H, end.H);
    expect(end.grounded).toBe(true);
  }
  expect([...visited].sort()).toEqual([...required].sort());
  expect(Math.max(...heights) - Math.min(...heights)).toBeLessThan(0.15);
  expect(bad).toEqual([]);
  console.log(`[${info.project.name}] four L0 seams: grounded throughout the route endpoints, height range ${(Math.max(...heights) - Math.min(...heights)).toFixed(3)} m`);
  await page.screenshot({ path: `${SHOTS}/tile-${info.project.name}-four-seams.png` });
});

test('coarse terrain remains visible outside the four-tile AOI', { tag: ['@pipeline', '@qa', '@render'] }, async ({ page }, info) => {
  test.skip(!manifest.visual.t1_1_1?.coarse_dtm, 'Requires cached DTM/ortho coverage outside the AOI');
  const bad = watchConsole(page);
  await page.goto(`/?backend=${info.project.name}&tiles=${TILES}&cam=23460250,6703750,70,0,-45,60&qa=1&pass=id`);
  await page.waitForFunction(() => (window as any).__qaReady || (window as any).__qaError);
  const state = await page.evaluate(() => ({ error:(window as any).__qaError, info:(window as any).__qaInfo, ids:(window as any).__qaIdTable }));
  expect(state.error).toBeFalsy();
  expect(state.info.tiles).toContain('t1_1_1');
  const { w, h, px } = await decodePng(page, await page.screenshot());
  const k = 4 * (Math.floor(h/2) * w + Math.floor(w/2));
  const id = (px[k] << 16) | (px[k+1] << 8) | px[k+2];
  expect(state.ids[String(id)]).toMatchObject({kind:'ground',tile:'t1_1_1'});
  expect(bad).toEqual([]);
});

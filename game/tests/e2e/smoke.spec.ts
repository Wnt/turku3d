import path from 'node:path';
// Smoke test, both backends: page loads without console errors, the dummy tile renders, the player walks
// forward >= 5 m, the 3D Tiles frame is correct (NE marker at +x/-z), QA camera mode reports ready.
// Screenshots go to $TURKU3D_WORK/reports/screenshots/.
import { test, expect, type Page, type ConsoleMessage } from '@playwright/test';
import fs from 'node:fs';
import { reportsRoot } from '../../../tools/dev/environment.mjs';

// Isolate input focus from other parallel Chrome windows on the shared Mac.
test.beforeEach(async ({ page }) => {
  const session = await page.context().newCDPSession(page);
  await session.send('Emulation.setFocusEmulationEnabled', { enabled: true });
});

const SHOTS = path.join(reportsRoot, 'screenshots');
fs.mkdirSync(SHOTS, { recursive: true });

// Keyboard routes must be independent of physical controllers connected to the shared host.
// Controller routes provide their own virtual gamepad in gamepad.spec.ts.
test.beforeEach(async ({ context }) => {
  await context.addInitScript(() => {
    Object.defineProperty(navigator, 'getGamepads', { value: () => [] });
  });
});

function watchConsole(page: Page) {
  const bad: string[] = [];
  const all: string[] = [];
  page.on('console', (m: ConsoleMessage) => {
    all.push(`[${m.type()}] ${m.text()}`);
    if (m.type() === 'error') bad.push(m.text());
  });
  page.on('pageerror', (e) => bad.push(`pageerror: ${e.message}`));
  return { bad, all };
}

test('loads, walks 5 m forward on the dummy tile, no console errors', { tag: ["@walk"] }, async ({ page }, info) => {
  const backend = info.project.name; // 'webgpu' | 'webgl'
  const con = watchConsole(page);
  await page.goto(`/?backend=${backend}`);
  await page.waitForFunction(() => (window as any).__game?.ready === true, null, { timeout: 60_000 });
  // wait for the visual tile and the spawn collision tile
  await page.waitForFunction(() => { const s = (window as any).__game.state(); return s.tilesSettled && s.collision.tiles > 0 && s.grounded; }, null, { timeout: 60_000 });
  const g = await page.evaluate(() => ({ backend: (window as any).__game.backend, coi: self.crossOriginIsolated, three: (window as any).__game.three }));
  expect(g.backend).toBe(backend === 'webgl' ? 'WebGL2' : 'WebGPU');
  expect(g.coi).toBe(true);

  // 3D Tiles frame check: the NE marker of t0_0_0 (tile-local x 240, z 10) must end up at engine +x / -z (north-east)
  const marker: number[] | null = await page.evaluate(() => {
    const tiles = (window as any).__game.tiles.tiles;
    let out = null as number[] | null;
    tiles.group.updateMatrixWorld(true);
    tiles.group.traverse((o: any) => {
      if (o.name === 'marker_ne' && o.geometry) {
        o.geometry.computeBoundingBox();
        const c = o.geometry.boundingBox.getCenter(new o.position.constructor());
        c.applyMatrix4(o.matrixWorld);
        out = [c.x, c.y, c.z];
      }
    });
    return out;
  });
  expect(marker).not.toBeNull();
  expect(marker![0]).toBeCloseTo(240, 1);
  expect(marker![1]).toBeCloseTo(12, 1); // ground 2 m + half of the 20 m pillar
  expect(marker![2]).toBeCloseTo(10, 1);

  const start = await page.evaluate(() => (window as any).__game.state());
  expect(start.H).toBeCloseTo(2.0, 1);
  expect(start.inputSource).toBe('keyboard');
  await page.screenshot({ path: `${SHOTS}/${backend}-start.png` });

  // walk forward (camera faces north at spawn): hold W until 5 m covered (max 6 s)
  await page.keyboard.down('KeyW');
  await page.waitForFunction((s0) => { const s = (window as any).__game.state(); return Math.hypot(s.feet[0] - s0[0], s.feet[2] - s0[2]) >= 5; }, start.feet, { timeout: 6_000 });
  await page.keyboard.up('KeyW');
  await page.waitForTimeout(400);
  const end = await page.evaluate(() => (window as any).__game.state());
  const dist = Math.hypot(end.feet[0] - start.feet[0], end.feet[2] - start.feet[2]);
  console.log(`[${backend}] walked ${dist.toFixed(2)} m: N ${start.N.toFixed(2)} -> ${end.N.toFixed(2)}, H ${end.H.toFixed(3)}, frame ${end.frameMs.toFixed(2)} ms, visual tris ${end.visTris}, collision tiles ${end.collision.tiles}`);
  expect(dist).toBeGreaterThanOrEqual(5);
  expect(end.N - start.N).toBeGreaterThan(4.5); // went north
  expect(end.H).toBeCloseTo(2.0, 1);               // still on the ground
  expect(end.grounded).toBe(true);
  expect(end.inputSource).toBe('keyboard');
  await page.screenshot({ path: `${SHOTS}/${backend}-walked.png` });

  // (entering the car is covered by the car test below; the car is out of reach from here)

  expect(con.bad, con.all.join('\n')).toEqual([]);
  const errs = await page.evaluate(() => (window as any).__game.errors);
  expect(errs).toEqual([]);
});

test('QA camera mode renders the requested view and sets __qaReady', { tag: ["@qa", "@render"] }, async ({ page }, info) => {
  const backend = info.project.name;
  const con = watchConsole(page);
  // looking north-east from the SW part of the tile towards the NE marker, 1.7 m eye height above the 2 m ground
  await page.goto(`/?backend=${backend}&cam=23459540,6704290,3.7,40,2,75&time=2026-07-01T09:00:00Z`);
  await page.waitForFunction(() => (window as any).__qaReady === true || (window as any).__qaError, null, { timeout: 60_000 });
  const qa = await page.evaluate(() => ({ ready: (window as any).__qaReady, err: (window as any).__qaError, info: (window as any).__game.qa, hud: getComputedStyle(document.getElementById('hud')!).display }));
  expect(qa.err).toBeFalsy();
  expect(qa.ready).toBe(true);
  expect(qa.hud).toBe('none');
  await page.screenshot({ path: `${SHOTS}/${backend}-qa.png` });
  console.log(`[${backend}] qa ${JSON.stringify(qa.info)}`);
  expect(con.bad, con.all.join('\n')).toEqual([]);
});

test('enter the car with F, drive forward, exit with F', { tag: ["@drive"] }, async ({ page }, info) => {
  const backend = info.project.name;
  const con = watchConsole(page);
  await page.goto(`/?backend=${backend}`);
  await page.waitForFunction(() => { const g = (window as any).__game; return g?.ready && g.state().grounded && g.state().tilesSettled; }, null, { timeout: 60_000 });
  // the car is parked 6 m to the right (east) of the spawn: strafe right until within 3 m
  await page.keyboard.down('KeyD');
  await page.waitForFunction(() => { const s = (window as any).__game.state(); return Math.hypot(s.vehicle.pos[0] - s.feet[0], s.vehicle.pos[2] - s.feet[2]) < 3.2; }, null, { timeout: 6_000 });
  await page.keyboard.up('KeyD');
  await page.keyboard.press('KeyF');
  await page.waitForFunction(() => (window as any).__game.state().mode === 'car', null, { timeout: 3_000 });
  const c0 = await page.evaluate(() => (window as any).__game.state().vehicle.pos);
  await page.keyboard.down('KeyW');
  await page.waitForTimeout(2500);
  await page.keyboard.up('KeyW');
  const c1 = await page.evaluate(() => (window as any).__game.state().vehicle);
  const moved = Math.hypot(c1.pos[0] - c0[0], c1.pos[2] - c0[2]);
  console.log(`[${backend}] car moved ${moved.toFixed(2)} m, speed ${c1.speed.toFixed(2)} m/s, dN ${(c0[2] - c1.pos[2]).toFixed(2)}`);
  expect(moved).toBeGreaterThan(3);
  expect(c0[2] - c1.pos[2]).toBeGreaterThan(2); // drove north (the car faces the spawn heading)
  await page.screenshot({ path: `${SHOTS}/${backend}-car.png` });
  await page.keyboard.down('Space'); await page.waitForTimeout(1500); await page.keyboard.up('Space');
  await page.keyboard.press('KeyF');
  await page.waitForFunction(() => (window as any).__game.state().mode === 'foot', null, { timeout: 3_000 });
  await page.waitForTimeout(800);
  const s = await page.evaluate(() => (window as any).__game.state());
  expect(s.H).toBeCloseTo(2.0, 1);
  expect(con.bad, con.all.join('\n')).toEqual([]);
});

test('QA __qaProject matches the Python camera (qa/tests/golden_projection.json: vertical fov, roll, aspect)', { tag: ["@qa", "@render"] }, async ({ browser }, info) => {
  const backend = info.project.name;
  const golden = JSON.parse(fs.readFileSync(new URL('../../../qa/tests/golden_projection.json', import.meta.url), 'utf8'));
  const ctx = await browser.newContext({ viewport: { width: golden.width, height: golden.height }, deviceScaleFactor: 1 });
  const page = await ctx.newPage();
  const byPose = new Map<string, { pose: any; cases: any[] }>();
  for (const c of golden.cases) {
    const k = JSON.stringify(c.pose);
    if (!byPose.has(k)) byPose.set(k, { pose: c.pose, cases: [] });
    byPose.get(k)!.cases.push(c);
  }
  let n = 0;
  for (const { pose, cases } of byPose.values()) {
    await page.goto(`/?backend=${backend}&cam=${pose.E},${pose.N},${pose.H},${pose.yaw},${pose.pitch},${pose.fov}&roll=${pose.roll}`);
    await page.waitForFunction(() => typeof (window as any).__qaProject === 'function' || (window as any).__qaError, null, { timeout: 30_000 });
    for (const c of cases) {
      const px = await page.evaluate((p) => (window as any).__qaProject(...p), c.point);
      if (c.px === null) { expect(px, JSON.stringify(c)).toBeNull(); continue; }
      expect(px, JSON.stringify(c)).not.toBeNull();
      expect(Math.abs(px[0] - c.px[0]), JSON.stringify({ c, px })).toBeLessThan(0.05);
      expect(Math.abs(px[1] - c.px[1]), JSON.stringify({ c, px })).toBeLessThan(0.05);
      n++;
    }
  }
  expect(n).toBeGreaterThan(5);
  await ctx.close();
});

test('QA pose outside the tileset reports __qaError quickly (not the 120 s timeout)', { tag: ["@qa", "@render"] }, async ({ page }, info) => {
  const backend = info.project.name;
  const t0 = Date.now();
  await page.goto(`/?backend=${backend}&cam=23469668,6714254,5,0,-10,60&qa=1`); // 10 km NE of the dev tiles
  await page.waitForFunction(() => (window as any).__qaReady === true || (window as any).__qaError, null, { timeout: 30_000 });
  const r = await page.evaluate(() => ({ ready: (window as any).__qaReady, err: (window as any).__qaError }));
  expect(r.ready).toBeFalsy();
  expect(r.err).toMatch(/outside the tileset/);
  expect(Date.now() - t0).toBeLessThan(15_000);
});

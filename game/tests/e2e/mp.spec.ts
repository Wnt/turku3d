import { spawn } from 'node:child_process';
import fs from 'node:fs';
import path from 'node:path';
import { isolatedTest as test, expect } from './fixtures';
import { cells, routeCoarse } from './coarseFake';
import { frames, open } from './waits';
import { reportsRoot, workRoot, port } from '../../../tools/dev/environment.mjs';
import { MARKET, PIZZERIA } from '../../src/gameplay/content/locations';
import { Document, NodeIO } from '@gltf-transform/core';

const shots = path.join(reportsRoot, 'fix-mp-remote-height');

async function startServer() {
  const scores = path.join(workRoot, 'qa', 'mp-client', `scores-${process.pid}`);
  fs.mkdirSync(scores, { recursive: true });
  const fixture = `
    import { SPAWNS } from './server/room.js';
    import { ScoreStore } from './server/store.js';
    import { createServer } from './server/http.js';
    SPAWNS.splice(0, SPAWNS.length, { E: 160, N: -245, yaw: 0 }, { E: 170, N: -245, yaw: 0 });
    const app = createServer({ store: new ScoreStore(process.env.TURKU_MP_SCORES) });
    app.server.listen(Number(process.env.TURKU_MP_PORT), '127.0.0.1', () => process.stdout.write(String(app.server.address().port) + '\\n'));
    process.on('SIGTERM', async () => { await app.close(); process.exit(0); });
  `;
  const child = spawn(process.execPath, ['--input-type=module', '-e', fixture], {
    cwd: path.resolve(import.meta.dirname, '../../..'),
    env: { ...process.env, TURKU_MP_PORT: String(port(3)), TURKU_MP_SCORES: scores },
    stdio: ['ignore', 'pipe', 'pipe'],
  });
  let output = '';
  let error = '';
  child.stdout!.on('data', chunk => { output += chunk.toString(); });
  child.stderr!.on('data', chunk => { error += chunk.toString(); });
  for (let i = 0; i < 100; i++) {
    if (child.exitCode !== null) throw new Error(`multiplayer server exited ${child.exitCode}: ${error}`);
    const serverPort = Number(output.split('\n')[0]);
    if (serverPort > 0) return { child, ws: `ws://127.0.0.1:${serverPort}/` };
    await new Promise(resolve => setTimeout(resolve, 50));
  }
  child.kill('SIGTERM');
  throw new Error('multiplayer server did not start');
}

async function remoteGroundGap(page: import('@playwright/test').Page, mode: 'foot' | 'car') {
  return page.evaluate(async mode => {
    const game = window.__game!;
    const visual = (game.state as () => any)().multiplayer.remoteVisuals.find((r: any) => r.mode === mode);
    if (!visual) throw new Error(`remote ${mode} is not visible`);
    const hit = await (game.physics as any).raycast({ x: visual.x, y: visual.rootY + 4, z: visual.z },
      { x: 0, y: -1, z: 0 }, 12, 'solid');
    if (!hit) throw new Error(`no ground under remote ${mode}`);
    return { gap: visual.bottom - hit.point.y, bottom: visual.bottom, ground: hit.point.y, rootY: visual.rootY };
  }, mode);
}

test('two pages collide and share a paid delivery', { tag: ['@gameplay', '@synthetic'] }, async ({ page, browser }, info) => {
  const realTiles = info.project.name === 'real-webgpu';
  test.setTimeout(realTiles ? 180_000 : 90_000);
  const { child: server, ws } = await startServer();
  const contextB = await browser.newContext({ baseURL: info.project.use.baseURL });
  const pageB = await contextB.newPage();
  try {
    const doc = new Document(), buffer = doc.createBuffer();
    const positions = new Float32Array([-0.9, 0, -2, 0.9, 0, -2, 0.9, 1.4, -2, -0.9, 1.4, -2,
      -0.9, 0, 2, 0.9, 0, 2, 0.9, 1.4, 2, -0.9, 1.4, 2]);
    const indices = new Uint16Array([0, 2, 1, 0, 3, 2, 4, 5, 6, 4, 6, 7, 0, 1, 5, 0, 5, 4,
      3, 7, 6, 3, 6, 2, 1, 2, 6, 1, 6, 5, 0, 4, 7, 0, 7, 3]);
    const primitive = doc.createPrimitive()
      .setAttribute('POSITION', doc.createAccessor().setType('VEC3').setArray(positions).setBuffer(buffer))
      .setIndices(doc.createAccessor().setType('SCALAR').setArray(indices).setBuffer(buffer))
      .setMaterial(doc.createMaterial().setBaseColorFactor([1, 0, 0, 1]));
    doc.createScene().addChild(doc.createNode('red-car').setMesh(doc.createMesh().addPrimitive(primitive)));
    const glb = Buffer.from(await new NodeIO().writeBinary(doc));
    for (const p of [page, pageB]) {
      await p.route('**/{dev-tiles,tiles}/manifest.json', async route => {
        const response = await route.fetch(), manifest = await response.json();
        manifest.vehicles = [...(manifest.vehicles ?? []), { object_id: 'fixture/parked', model: 'fixture', color: 'red',
          x: 300, y: 2.75, z: 300, yaw: 0, width: 1.8, height: 1.4, length: 4 }];
        manifest.vehicle_models = { ...manifest.vehicle_models, 'fixture/red': { url: 'remote-test.glb', sha256: '', bytes: glb.length } };
        await route.fulfill({ response, json: manifest });
      });
      await p.route('**/{dev-tiles,tiles}/remote-test.glb', route => route.fulfill({ body: glb, contentType: 'model/gltf-binary' }));
    }
    fs.mkdirSync(shots, { recursive: true });
    const url = `/?scene=dev&backend=webgpu&mp=${encodeURIComponent(ws)}${realTiles ? '&tiles=/tiles/tileset.json' : ''}`;
    await Promise.all([open(page, url), pageB.goto(url)]);
    test.setTimeout(realTiles ? 180_000 : 90_000);
    console.log('[mp] pages loaded');
    await expect(page.getByRole('option', { name: /Multiplayer/ })).toBeEnabled();
    await Promise.all([
      page.getByRole('option', { name: /Multiplayer/ }).click(),
      pageB.getByRole('option', { name: /Multiplayer/ }).click(),
    ]);
    for (const p of [page, pageB]) await p.waitForFunction(() => {
      const s = (window.__game!.state as () => any)();
      return s.multiplayer?.connected && s.multiplayer.players === 2 && !s.startScreen && !s.loading && s.grounded;
    }, undefined, { timeout: 30_000 });
    console.log('[mp] joined and grounded');
    await Promise.all([page.waitForFunction(() => (window.__game!.state as () => any)().multiplayer.remotes.length === 1),
      pageB.waitForFunction(() => (window.__game!.state as () => any)().multiplayer.remotes.length === 1)]);

    const footGap = await remoteGroundGap(pageB, 'foot');
    expect(Math.abs(footGap.gap), `remote character feet: ${JSON.stringify(footGap)}`).toBeLessThan(0.05);
    if (realTiles) {
      await page.waitForFunction(() => (window.__game!.state as () => any)().vehicle?.wheels?.some((w: any) => w.contact));
      const localCar = await page.evaluate(() => (window.__game!.state as () => any)().vehicle.pos as number[]);
      await pageB.evaluate(([x, y, z]) => {
        const client = (window.__game as any).multiplayerClient();
        client.state.players.push({ id: 'real-car', nick: 'RC', spawn: { E: x, N: -z, yaw: 0 },
          pose: { t: performance.now(), E: x, N: -z, H: y - 0.6, yaw: 0,
            vE: 0, vN: 0, vH: 0, mode: 'car', vehicle: 'player', carrying: false } });
      }, localCar);
      await pageB.waitForFunction(() => (window.__game!.state as () => any)().multiplayer.remoteVisuals.some((r: any) => r.id === 'real-car'));
      const carGap = await remoteGroundGap(pageB, 'car');
      expect(Math.abs(carGap.gap), `remote car bottom: ${JSON.stringify(carGap)}`).toBeLessThan(0.05);
      fs.writeFileSync(path.join(shots, 'real-tile-heights.json'), JSON.stringify({ foot: footGap, car: carGap }, null, 2));
      await pageB.screenshot({ path: path.join(shots, 'real-tile-remote-car.png') });
      return;
    }

    const remoteA0 = await pageB.evaluate(() => (window.__game!.state as () => any)().multiplayer.remotes[0]);
    const b0 = await pageB.evaluate(() => (window.__game!.state as () => any)().feet as number[]);
    // The player car is placed between A and B, then A drives east into B's proxy.
    await page.evaluate(([x, y, z]) => {
      const physics = window.__game!.physics as { resetVehicle(pos: object, yaw: number): void };
      physics.resetVehicle({ x: x - 6.5, y: y + 0.75, z }, 0);
    }, b0);
    await frames(page, 12);
    await page.keyboard.press('KeyF');
    await page.waitForFunction(() => (window.__game!.state as () => any)().mode === 'car');
    await pageB.waitForFunction(() => (window.__game!.state as () => any)().multiplayer.remoteVisuals[0]?.mode === 'car');
    const carGap = await remoteGroundGap(pageB, 'car');
    expect(Math.abs(carGap.gap), `remote car bottom: ${JSON.stringify(carGap)}`).toBeLessThan(0.05);
    expect(await pageB.evaluate(() => (window.__game!.state as () => any)().multiplayer.remoteVisuals[0].vehicle)).toBe('player');
    console.log('[mp] car entered');
    await page.keyboard.down('KeyW');
    await frames(page, 100);
    await page.keyboard.up('KeyW');
    const a = await page.evaluate(() => (window.__game!.state as () => any)());
    const b = await pageB.evaluate(() => (window.__game!.state as () => any)());
    expect(a.multiplayer.remotes).toHaveLength(1);
    expect(b.multiplayer.remotes).toHaveLength(1);
    expect(a.multiplayer.remoteFrameMs).toBeLessThan(1);
    expect(b.multiplayer.remoteFrameMs).toBeLessThan(1);
    expect(Math.hypot(b.feet[0] - b0[0], b.feet[2] - b0[2])).toBeGreaterThan(0.05);
    expect(Math.hypot(a.multiplayer.remotes[0].x - b0[0], a.multiplayer.remotes[0].z - b0[2])).toBeGreaterThan(0.05);
    expect(Math.hypot(b.multiplayer.remotes[0].x - remoteA0.x, b.multiplayer.remotes[0].z - remoteA0.z)).toBeGreaterThan(0.05);
    console.log('[mp] collision observed');

    // Complete the real mission state machine; the callback sends the paid drop-off.
    await page.evaluate(({ market, pizzeria }) => {
      const missions = window.__game!.missions as any;
      const act = (position: object, extra: object = {}) => missions.update(0, { position, input: 'keyboard',
        vehicleId: null, interact: false, ...extra });
      act(market, { interact: true }); act(market, { choose: 'rent_permit' }); act(market, { leave: true });
      act(pizzeria, { interact: true }); act(pizzeria, { choose: 'take_order' });
      act(missions.currentStop.point, { interact: true });
    }, { market: MARKET.point, pizzeria: PIZZERIA.point });
    await pageB.waitForFunction(() => (window.__game!.state as () => any)().multiplayer.scores.some((s: any) => s.money > 0));
    console.log('[mp] payout observed');
    expect(await pageB.locator('#mp-scoreboard').textContent()).toContain('€14');
    const paidScores = await pageB.evaluate(() => (window.__game!.state as () => any)().multiplayer.scores);
    await page.evaluate(() => window.__render!.perf.reset());
    await frames(page, 60);
    const baselinePerf = await page.evaluate(() => window.__render!.perf.snapshot());
    // Nine extra synthetic peers make ten remote actors without opening ten more browsers.
    await page.evaluate(() => {
      const client = (window.__game as any).multiplayerClient();
      for (let i = 0; i < 9; i++) client.state.players.push({ id: `synthetic-${i}`, nick: `Z${String.fromCharCode(65 + i)}`,
        spawn: { E: 145 + i * 2, N: -245, yaw: 0 },
        pose: { t: performance.now(), E: 145 + i * 2, N: -245, H: 2, yaw: 0,
          vE: 0, vN: 0, vH: 0, mode: 'foot', vehicle: null, carrying: false } });
    });
    await page.evaluate(() => window.__render!.perf.reset());
    await frames(page, 100);
    console.log('[mp] ten remote frames sampled');
    const tenRemoteMs = await page.evaluate(() => (window.__game!.state as () => any)().multiplayer.remoteFrameMs);
    const tenPerf = await page.evaluate(() => window.__render!.perf.snapshot());
    const addedCpuMs = (tenPerf.cpuMs.p50 ?? 0) - (baselinePerf.cpuMs.p50 ?? 0);
    expect(tenRemoteMs).toBeLessThan(1);
    expect(addedCpuMs).toBeLessThan(1);
    await pageB.evaluate(() => {
      const client = (window.__game as any).multiplayerClient();
      const base = { t: performance.now(), E: 165, N: -245, H: 2, yaw: 0,
        vE: 0, vN: 0, vH: 0, carrying: false };
      client.state.players.push({ id: 'test-scooter', nick: 'SC', spawn: { E: 165, N: -245, yaw: 0 },
        pose: { ...base, mode: 'scooter', vehicle: 'scooter' } });
      client.state.players.push({ id: 'test-red-car', nick: 'RC', spawn: { E: 167, N: -245, yaw: 0 },
        pose: { ...base, E: 167, mode: 'car', vehicle: 'fixture/red' } });
    });
    await pageB.waitForFunction(() => {
      const visuals = (window.__game!.state as () => any)().multiplayer.remoteVisuals;
      return visuals.some((v: any) => v.id === 'test-scooter' && v.mode === 'scooter' && v.vehicle === 'scooter')
        && visuals.some((v: any) => v.id === 'test-red-car' && v.vehicle === 'fixture/red' && v.paint === 'ff0000');
    });
    fs.writeFileSync(path.join(shots, 'metrics.json'), JSON.stringify({
      remoteFrameMs: { a: a.multiplayer.remoteFrameMs, b: b.multiplayer.remoteFrameMs },
      tenRemoteFrameMs: tenRemoteMs, addedCpuMs, baselinePerf, tenPerf,
      movedMeters: { b: Math.hypot(b.feet[0] - b0[0], b.feet[2] - b0[2]),
        aOnB: Math.hypot(b.multiplayer.remotes[0].x - remoteA0.x, b.multiplayer.remotes[0].z - remoteA0.z) },
      score: paidScores,
      groundGaps: { foot: footGap, car: carGap },
    }, null, 2));
    fs.writeFileSync(path.join(shots, 'page-a.png'), await page.screenshot());
    fs.writeFileSync(path.join(shots, 'page-b.png'), await pageB.screenshot());
  } finally {
    await contextB.close();
    server.kill('SIGTERM');
  }
});

test('remote car follows chassis and wheels while driving, turning, reversing and climbing a banked ramp', async ({ page, browser }, info) => {
  test.setTimeout(120_000);
  const { child: server, ws } = await startServer();
  const context = await browser.newContext({ baseURL: info.project.use.baseURL });
  const observer = await context.newPage();
  try {
    for (const p of [page, observer]) {
      await routeCoarse(p, {
        ...Object.fromEntries([-1, 0, 1].flatMap(i => [-1, 0, 1].map(j => [`t0_${i}_${j}`, { cells: cells(i, j) }]))),
        t0_0_0: { cells: cells(0, 0), roads: { nodes: 2, edges: 1, json: {
        format: 'turku3d-roadgraph', version: 1, frame: 'engine-xz', area: 'orientation',
        bbox: [0, 0, 250, 250], nodes: [140, 245, 220, 245], edges: [[0, 1, 19, 80]],
      } } } });
      p.on('pageerror', e => console.log('[orientation pageerror]', e.message));
      p.on('console', m => { if (['error', 'warn'].includes(m.type())) console.log('[orientation]', m.text()); });
    }
    const url = `/?scene=dev&backend=webgpu&mp=${encodeURIComponent(ws)}`;
    await Promise.all([open(page, url), observer.goto(url)]);
    for (const p of [page, observer]) await p.getByRole('option', { name: /Multiplayer/ }).click({ timeout: 30000 });
    for (const p of [page, observer]) await p.waitForFunction(() => {
      const s = (window.__game!.state as () => any)();
      return s.multiplayer?.connected && s.multiplayer.players === 2 && !s.startScreen && !s.loading && s.grounded;
    });
    console.log('[orientation] joined');
    await observer.evaluate(() => (window.__game!.physics as any).setCharacterEnabled(true, { x: 150, y: 2, z: 245 }));
    await page.evaluate(() => {
      const physics = window.__game!.physics as any;
      physics.setCharacterEnabled(true, { x: 163, y: 2, z: 245 });
      physics.resetVehicle({ x: 165, y: 2.75, z: 245 }, 0);
    });
    await frames(page, 30);
    await page.keyboard.press('KeyF');
    await page.waitForFunction(() => (window.__game!.state as () => any)().mode === 'car');
    await observer.waitForFunction(() => (window.__game!.state as () => any)().multiplayer.remoteVisuals.some((v: any) => v.mode === 'car'));
    const assertPose = async () => {
      await frames(page, 25);
      // Compare the observer's latest received pose to its delayed visual.
      const result = await observer.evaluate(() => {
        const game = window.__game as any, state = game.state(), client = game.multiplayerClient();
        const p = client.state.players.find((v: any) => v.id !== client.state.you.id).pose;
        const visual = state.multiplayer.remoteVisuals.find((v: any) => v.mode === 'car');
        return { pose: p, visual };
      });
      const q = result.pose.rot, v = result.visual.rotation;
      expect(Math.abs(q.x * v[0] + q.y * v[1] + q.z * v[2] + q.w * v[3])).toBeGreaterThan(0.995);
      expect(result.visual.wheels).toHaveLength(4);
      result.pose.wheels.forEach((w: any, i: number) => {
        expect(Math.abs(result.visual.wheels[i].pos[1] - w.pos.y)).toBeLessThan(0.15);
      });
      return result;
    };
    await assertPose();
    console.log('[orientation] entered');
    await page.keyboard.down('KeyW');
    await page.keyboard.down('KeyA');
    await frames(page, 55);
    await page.keyboard.up('KeyA');
    await page.keyboard.up('KeyW');
    const turned = await assertPose();
    console.log('[orientation] turned');
    expect(Math.abs(turned.pose.rot.y)).toBeGreaterThan(0.02);
    await page.keyboard.down('KeyS');
    await frames(page, 150);
    await page.keyboard.up('KeyS');
    const reversed = await assertPose();
    console.log('[orientation] reversed');
    expect(reversed.pose.wheels.some((w: any) => w.rotation !== turned.pose.wheels[0].rotation)).toBe(true);

    // A long banked ramp exercises simultaneous chassis pitch and roll using real Rapier wheel contacts.
    const { writeCol } = await import('../../scripts/colWriter.mjs');
    const ramp = writeCol({ i: 2, j: 3,
      positions: new Float32Array([50, 2, 52.5, 65, 5, 52.5, 65, 5.8, 62, 50, 2, 62,
        80, 5, 52.5, 80, 5.8, 62]),
      indices: new Uint32Array([0, 2, 1, 0, 3, 2, 1, 5, 4, 1, 2, 5]),
      materials: new Uint8Array([1, 1, 1, 1]), prims: [], flags: 0, meta: {} });
    await page.evaluate(async bytes => {
      const physics = window.__game!.physics as any;
      await physics.loadTile('orientation-ramp', new Uint8Array(bytes).buffer);
      physics.resetVehicle({ x: 171, y: 2.8, z: 245 }, 0);
      (window as any).slopeSamples = [];
      const capture = () => {
        const v = (window.__game!.state as () => any)().vehicle;
        (window as any).slopeSamples.push(v);
        if ((window as any).captureSlope) requestAnimationFrame(capture);
      };
      (window as any).captureSlope = true; capture();
    }, [...ramp]);
    await frames(page, 30);
    await page.keyboard.down('KeyW');
    await page.waitForFunction(() => (window.__game!.state as () => any)().vehicle.pos[0] > 184);
    await page.keyboard.up('KeyW');
    const sloped = await assertPose();
    expect(Math.abs(sloped.pose.rot.z)).toBeGreaterThan(0.025);
    const samples = await page.evaluate(() => { (window as any).captureSlope = false; return (window as any).slopeSamples; });
    expect(samples.some((s: any) => s.pos[1] > 3.5 && Math.abs(s.rot.x) > 0.01 && Math.abs(s.rot.z) > 0.025)).toBe(true);
    await info.attach('orientation-evidence', { body: JSON.stringify({ turned, reversed, sloped, samples }), contentType: 'application/json' });
    await info.attach('remote-ramp', { body: await observer.screenshot(), contentType: 'image/png' });
  } finally { await context.close(); server.kill('SIGTERM'); }
});

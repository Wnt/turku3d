import { spawn } from 'node:child_process';
import fs from 'node:fs';
import path from 'node:path';
import { isolatedTest as test, expect } from './fixtures';
import { frames, open } from './waits';
import { port, reportsRoot, workRoot } from '../../../tools/dev/environment.mjs';
import { MARKET, PIZZERIA } from '../../src/gameplay/content/locations';

const serverPort = port(3);
const shots = path.join(reportsRoot, 'mp-client');
const ws = `ws://127.0.0.1:${serverPort}/`;

async function startServer() {
  const scores = path.join(workRoot, 'qa', 'mp-client', `scores-${process.pid}`);
  fs.mkdirSync(scores, { recursive: true });
  const fixture = `
    import { SPAWNS } from './server/room.js';
    import { ScoreStore } from './server/store.js';
    import { createServer } from './server/http.js';
    SPAWNS.splice(0, SPAWNS.length, { E: 160, N: -245, yaw: 0 }, { E: 170, N: -245, yaw: 0 });
    const app = createServer({ store: new ScoreStore(process.env.TURKU_MP_SCORES) });
    app.server.listen(Number(process.env.TURKU_MP_PORT), '127.0.0.1');
    process.on('SIGTERM', async () => { await app.close(); process.exit(0); });
  `;
  const child = spawn(process.execPath, ['--input-type=module', '-e', fixture], {
    cwd: path.resolve(import.meta.dirname, '../../..'),
    env: { ...process.env, TURKU_MP_PORT: String(serverPort), TURKU_MP_SCORES: scores },
    stdio: ['ignore', 'pipe', 'pipe'],
  });
  for (let i = 0; i < 100; i++) {
    if (child.exitCode !== null) throw new Error(`multiplayer server exited ${child.exitCode}`);
    try { if ((await fetch(`http://127.0.0.1:${serverPort}/healthz`)).ok) return child; } catch { /* startup */ }
    await new Promise(resolve => setTimeout(resolve, 50));
  }
  child.kill('SIGTERM');
  throw new Error('multiplayer server did not start');
}

test('two pages collide and share a paid delivery', { tag: ['@gameplay', '@synthetic'] }, async ({ page, browser }, info) => {
  test.setTimeout(90_000);
  const server = await startServer();
  const contextB = await browser.newContext({ baseURL: info.project.use.baseURL });
  const pageB = await contextB.newPage();
  try {
    fs.mkdirSync(shots, { recursive: true });
    const url = `/?backend=${info.project.name}&mp=${encodeURIComponent(ws)}`;
    await Promise.all([open(page, url), pageB.goto(url)]);
    test.setTimeout(90_000); // keep the browser-budget bound after open() measures a loaded host
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
    // Nine extra synthetic peers make ten remote actors without opening ten more browsers.
    await page.evaluate(() => {
      const client = (window.__game as any).multiplayerClient();
      for (let i = 0; i < 9; i++) client.state.players.push({ id: `synthetic-${i}`, nick: `Z${String.fromCharCode(65 + i)}`,
        spawn: { E: 145 + i * 2, N: -245, yaw: 0 },
        pose: { t: performance.now(), E: 145 + i * 2, N: -245, H: 2, yaw: 0,
          vE: 0, vN: 0, vH: 0, mode: 'foot', carrying: false } });
    });
    await frames(page, 100);
    console.log('[mp] ten remote frames sampled');
    const tenRemoteMs = await page.evaluate(() => (window.__game!.state as () => any)().multiplayer.remoteFrameMs);
    expect(tenRemoteMs).toBeLessThan(1);
    fs.writeFileSync(path.join(shots, 'metrics.json'), JSON.stringify({
      remoteFrameMs: { a: a.multiplayer.remoteFrameMs, b: b.multiplayer.remoteFrameMs },
      tenRemoteFrameMs: tenRemoteMs,
      movedMeters: { b: Math.hypot(b.feet[0] - b0[0], b.feet[2] - b0[2]),
        aOnB: Math.hypot(b.multiplayer.remotes[0].x - remoteA0.x, b.multiplayer.remotes[0].z - remoteA0.z) },
      score: paidScores,
    }, null, 2));
    fs.writeFileSync(path.join(shots, 'page-a.png'), await page.screenshot());
    fs.writeFileSync(path.join(shots, 'page-b.png'), await pageB.screenshot());
  } finally {
    await contextB.close();
    server.kill('SIGTERM');
  }
});

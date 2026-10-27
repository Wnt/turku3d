// The localhost game's landing paths on a Vite dev server (tools/dev/localhost/README.md): a HUD module change swaps
// the play session in place (new text, same position and money, no page load); a physics worker change autosaves,
// reloads and continues at the same place without the start menu. The server runs on a copy of game/ and tools/dev
// under $TURKU3D_WORK/tmp, so the edits never touch the checkout.
import { spawn, type ChildProcess } from 'node:child_process';
import fs from 'node:fs';
import path from 'node:path';
import { isolatedTest as test, expect, gameState, type Page } from '../e2e/fixtures';
import { open, untilSim } from '../e2e/waits';
import { port, reportsRoot, workRoot } from '../../../tools/dev/environment.mjs';
import { MARKET } from '../../src/gameplay/content/locations';

test.describe.configure({ mode: 'serial' });

const repo = path.resolve(import.meta.dirname, '../../..');
const copy = path.join(workRoot, 'tmp', `hotswap-${process.pid}`);
const PORT = Number(process.env.E2E_HOTSWAP_PORT ?? port(4));
const shots = path.join(reportsRoot, 'tool-localhost-hotswap');
let vite: ChildProcess | null = null;

test.beforeAll(async () => {
  fs.rmSync(copy, { recursive: true, force: true });
  fs.mkdirSync(path.join(copy, 'game'), { recursive: true });
  for (const name of ['src', 'index.html', 'vite.config.ts', 'package.json']) {
    fs.cpSync(path.join(repo, 'game', name), path.join(copy, 'game', name), { recursive: true });
  }
  fs.cpSync(path.join(repo, 'tools/dev'), path.join(copy, 'tools/dev'), { recursive: true });
  for (const name of ['node_modules', 'public']) fs.symlinkSync(path.join(repo, 'game', name), path.join(copy, 'game', name));
  const env = { ...process.env };
  delete env.TURKU3D_LOCALHOST_STATE;
  vite = spawn(path.join(copy, 'game/node_modules/.bin/vite'), ['--port', String(PORT), '--strictPort'],
    { cwd: path.join(copy, 'game'), env, stdio: 'ignore' });
  for (let i = 0; ; i++) {
    try { if ((await fetch(`http://localhost:${PORT}/`)).ok) break; } catch { /* not listening yet */ }
    if (i > 300) throw new Error('vite dev server did not start');
    await new Promise(r => setTimeout(r, 100));
  }
});

test.afterAll(() => {
  vite?.kill('SIGTERM');
  fs.rmSync(copy, { recursive: true, force: true });
});

function edit(file: string, change: (text: string) => string) {
  const target = path.join(copy, 'game/src', file);
  fs.writeFileSync(target, change(fs.readFileSync(target, 'utf8')));
}

/** The game runs (after a page load too): menus closed, the player placed on the ground. */
const placed = (page: Page) => untilSim(page, () => {
  if (!window.__game?.ready) return false;
  const s = (window.__game.state as () => { grounded: boolean; startScreen: boolean; loading: boolean })();
  return s.grounded && !s.startScreen && !s.loading;
}, null, 60);

/** New game, rent the permit (money and permit change), walk a few metres. */
async function play(page: Page) {
  await open(page, '/');
  await page.getByRole('option', { name: 'New game' }).click();
  await placed(page);
  await page.evaluate(point => {
    const missions = window.__game!.missions as { update(dt: number, frame: object): unknown };
    const frame = (extra: object) => ({ position: point, interact: false, input: 'keyboard', vehicleId: null, ...extra });
    missions.update(0, frame({ interact: true }));
    missions.update(0, frame({ choose: 'rent_permit' }));
    missions.update(0, frame({ leave: true }));
  }, MARKET.point);
  await page.keyboard.down('KeyW');
  await untilSim(page, () => (window.__game!.state as () => { vel: number[] })().vel.some(v => Math.abs(v) > 1), null, 10);
  await page.keyboard.up('KeyW');
  await untilSim(page, () => { const s = (window.__game!.state as () => { vel: number[]; grounded: boolean })();
    return s.grounded && Math.hypot(s.vel[0], s.vel[2]) < 0.05; }, null, 10);
}

const missionsOf = (page: Page) => page.evaluate(() => {
  const m = window.__game!.missions as { money: number; permit: number };
  return { money: m.money, permit: m.permit };
});

test('a HUD module change swaps the play session in place', async ({ page }) => {
  let loads = 0;
  page.on('load', () => { loads++; });
  await play(page);
  const before = await gameState(page);
  const wallet = await missionsOf(page);
  expect(wallet.permit).toBeGreaterThan(0);
  edit('ui/hud.ts', text => text.replace('`${d.mode}  ${d.speed', '`HOT SWAPPED ${d.mode}  ${d.speed'));
  await expect(page.locator('#hud')).toContainText('HOT SWAPPED', { timeout: 20_000 });
  const after = await gameState(page);
  expect(loads, 'no page load').toBe(1);
  expect(Math.hypot(after.feet[0] - before.feet[0], after.feet[2] - before.feet[2])).toBeLessThan(0.05);
  expect(await missionsOf(page)).toEqual(wallet);
  expect(after.errors).toEqual([]);
  await page.screenshot({ path: path.join(shots, 'e2e-hud-hot-swapped.png') });
});

test('a physics worker change autosaves, reloads and continues in place', async ({ page }) => {
  await play(page);
  const before = await gameState(page);
  const wallet = await missionsOf(page);
  const reloaded = page.waitForEvent('load');
  edit('physics/worker.ts', text => `${text}\n// worker landing\n`);
  await reloaded;
  await placed(page);
  const after = await gameState(page);
  expect(Math.hypot(after.feet[0] - before.feet[0], after.feet[2] - before.feet[2])).toBeLessThan(0.3);
  expect(await missionsOf(page)).toEqual(wallet);
  expect(after.errors).toEqual([]);
});

// Play telemetry on the localhost game's Vite dev server (src/telemetry/README.md): a new game in a browser under
// automation (navigator.webdriver is true here, as in a Chrome window chrome-devtools-mcp launched, where the player
// may be playing) posts its first batch within 10 s, without ?telemetry=1, and the server appends it to the session
// file. The server runs on the checkout's game/ with its telemetry directory under $TURKU3D_WORK/tmp.
import { spawn, type ChildProcess } from 'node:child_process';
import fs from 'node:fs';
import path from 'node:path';
import type { Request } from '@playwright/test';
import { isolatedTest as test, expect } from '../e2e/fixtures';
import { open } from '../e2e/waits';
import { port, workRoot } from '../../../tools/dev/environment.mjs';

const game = path.resolve(import.meta.dirname, '../..');
const PORT = Number(process.env.E2E_TELEMETRY_PORT ?? port(6));
const dir = path.join(workRoot, 'tmp', `telemetry-${process.pid}`);
let vite: ChildProcess | null = null;

test.use({ baseURL: `http://localhost:${PORT}/` });

test.beforeAll(async () => {
  fs.rmSync(dir, { recursive: true, force: true });
  vite = spawn(path.join(game, 'node_modules/.bin/vite'), ['--port', String(PORT), '--strictPort'],
    { cwd: game, env: { ...process.env, TURKU3D_TELEMETRY_DIR: dir }, stdio: 'ignore' });
  for (let i = 0; ; i++) {
    try { if ((await fetch(`http://localhost:${PORT}/`)).ok) break; } catch { /* not listening yet */ }
    if (i > 300) throw new Error('vite dev server did not start');
    await new Promise(r => setTimeout(r, 100));
  }
});

test.afterAll(() => {
  vite?.kill('SIGTERM');
  fs.rmSync(dir, { recursive: true, force: true });
});

test('a new game under browser automation posts telemetry samples to the dev server within 10 s', async ({ page }) => {
  const posts: Request[] = [];
  page.on('request', r => { if (r.method() === 'POST' && new URL(r.url()).pathname === '/telemetry') posts.push(r); });
  // 10 s on an idle host, scaled by the measured frame time on a loaded one (waits.ts).
  const { scale } = await open(page, '/');
  expect(await page.evaluate(() => navigator.webdriver)).toBe(true);
  await page.getByRole('option', { name: 'New game' }).click();
  await page.getByRole('button', { name: 'Skip' }).click();
  await expect.poll(() => posts.length, { timeout: 10_000 * scale }).toBeGreaterThan(0);
  const sent = posts[0].postData()!.trimEnd().split('\n').map(line => JSON.parse(line) as { session: string; E: number; N: number });
  expect(sent.length).toBeGreaterThanOrEqual(1);
  const session = await page.evaluate(() => (window.__game!.telemetry as { session: string }).session);
  expect(sent[0]).toMatchObject({ session });
  expect(Number.isFinite(sent[0].E) && Number.isFinite(sent[0].N)).toBe(true);
  expect((await posts[0].response())?.status()).toBe(204);
  const [file] = fs.readdirSync(dir);
  expect(file).toMatch(/^session-\d{8}\.jsonl$/);
  const written = fs.readFileSync(path.join(dir, file), 'utf8').trimEnd().split('\n').map(line => JSON.parse(line) as { session: string });
  expect(written.filter(row => row.session === session).length).toBeGreaterThanOrEqual(1);
});

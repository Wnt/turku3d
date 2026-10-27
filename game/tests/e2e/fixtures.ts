import { test as base, expect, type BrowserContext, type Page, type TestInfo } from '@playwright/test';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import type { PhysicsClient } from '../../src/physics/client';
import { frames } from './waits';

/** 1-minute host load above which a failed test gets its one retry (configs set `retries: 1`). */
const RETRY_LOAD = 40;
const failureMarker = (info: TestInfo) => path.join(info.project.outputDir, '.failure-load', `${info.testId}.json`);

interface GameState {
  mode: 'foot' | 'car'; paused: boolean; feet: number[]; vel: number[]; grounded: boolean;
  H: number; E: number; N: number; cameraYaw: number; groundMaterial: number; inputSource: string;
  vehicle: { pos: number[]; speed: number } | null;
  collision: { tiles: number; tris: number; colBytes: number; estWasmBytes: number };
  visTris: number; visModels: number; tilesSettled: boolean; frameMs: number; errors: string[];
}
export interface RouteSample { time: number; feet: number[]; speed: number; grounded: boolean; mode: string; frameMs: number; ground: number | null; groundChecked: boolean }
declare global {
  interface Window {
    __virtualPad: { connected: boolean; axes: number[]; buttons: number[] };
    __routeSamples: RouteSample[];
  }
}
export const gameState = (page: Page) => page.evaluate(() => (window.__game!.state as () => GameState)());
export const isolatedTest = base.extend<{ loadRetry: void; isolatedInput: void }>({
  // Retry policy: a failure is retried once in the same run only when the host was loaded when it failed (the
  // 1-minute load at teardown). A failure on a quieter host fails its retry at once. TURKU3D_BROWSER_LOAD_LOG,
  // when set, receives one JSON row per attempt for the live-build report.
  // eslint-disable-next-line no-empty-pattern -- Playwright requires a destructured fixture argument
  loadRetry: [async ({}, use, info) => {
    const marker = failureMarker(info);
    const started = Date.now();
    if (info.retry > 0) {
      let failedAt: number;
      try { failedAt = JSON.parse(fs.readFileSync(marker, 'utf8')).hostLoad; } catch { failedAt = os.loadavg()[0]; }
      info.annotations.push({ type: 'load-retry', description: `attempt ${info.retry} after a failure at host load ${failedAt.toFixed(1)}` });
      console.log(`[retry] ${info.project.name} ${info.title}: attempt ${info.retry} after a failure at host load ${failedAt.toFixed(1)}`);
      if (failedAt <= RETRY_LOAD) {
        throw new Error(`not retried: the previous attempt failed at host load ${failedAt.toFixed(1)} <= ${RETRY_LOAD}, so it is not a load failure`);
      }
    }
    await use();
    const hostLoad = os.loadavg()[0];
    const failed = info.status !== info.expectedStatus;
    if (failed) {
      fs.mkdirSync(path.dirname(marker), { recursive: true });
      fs.writeFileSync(marker, JSON.stringify({ hostLoad }));
    }
    const log = process.env.TURKU3D_BROWSER_LOAD_LOG;
    if (log) {
      const pace = info.annotations.find(row => row.type === 'pace')?.description;
      fs.appendFileSync(log, JSON.stringify({ project: info.project.name, file: path.basename(info.file), title: info.title,
        retry: info.retry, status: info.status, seconds: (Date.now() - started) / 1000, hostLoad, pace: pace ? JSON.parse(pace) : null }) + '\n');
    }
  }, { auto: true }],
  isolatedInput: [async ({ page }, use) => {
    // Parallel Chrome windows must not steal focus or expose a host controller to a test.
    const session = await page.context().newCDPSession(page);
    await session.send('Emulation.setFocusEmulationEnabled', { enabled: true });
    await page.addInitScript(() => {
      if (Object.getOwnPropertyDescriptor(navigator, 'getGamepads')?.configurable === false) return;
      Object.defineProperty(navigator, 'getGamepads', { configurable: true, value: () => [] });
    });
    await use();
  }, { auto: true }],
});
export const cachedTest = isolatedTest.extend<object, { cachedContext: BrowserContext }>({
  cachedContext: [async ({ browser }, use, workerInfo) => {
    const context = await browser.newContext({
      baseURL: workerInfo.project.use.baseURL,
      viewport: workerInfo.project.use.viewport ?? { width: 960, height: 540 },
      deviceScaleFactor: workerInfo.project.use.deviceScaleFactor ?? 1,
    });
    await use(context);
    await context.close();
  }, { scope: 'worker' }],
  page: async ({ cachedContext }, use) => {
    const page = await cachedContext.newPage();
    // The shared context keeps the HTTP cache, not the game: the previous test's autosave (written on page hide)
    // would turn New game into an overwrite confirmation. The first document of each test's page starts with empty
    // storage; reloads within the test keep it (sessionStorage is per page).
    await page.addInitScript(() => {
      if (!location.protocol.startsWith('http') || sessionStorage.getItem('e2e-fresh-storage')) return;
      localStorage.clear();
      sessionStorage.setItem('e2e-fresh-storage', '1');
    });
    await use(page);
    await page.close();
  },
});
export async function installPad(page: Page) {
  await page.addInitScript(() => {
    const virtual = { connected: true, axes: [0, 0, 0, 0], buttons: Array<number>(17).fill(0) };
    window.__virtualPad = virtual;
    Object.defineProperty(navigator, 'getGamepads', { value: () => virtual.connected ? [{
      id: 'Xbox Wireless Controller', index: 0, mapping: 'standard', connected: true, timestamp: performance.now(),
      axes: virtual.axes, buttons: virtual.buttons.map(value => ({ value, pressed: value > 0.5, touched: value > 0 })),
    }] : [] });
  });
}
export async function padAxes(page: Page, x: number, y: number, rx = 0, ry = 0) {
  await page.evaluate(a => { window.__virtualPad.axes = a; }, [x, y, rx, ry]);
}
export async function padButton(page: Page, index: number, value: number) {
  await page.evaluate(([i, v]) => { window.__virtualPad.buttons[i] = v; }, [index, value]);
  await frames(page, 2);
}

/** Per-frame positions catch transient falls; worker raycasts sample the ground at 10 Hz. */
async function capture(page: Page) {
  await page.addInitScript(() => {
    window.__routeSamples = [];
    let lastRay = -Infinity, busy = false;
    const sample = async () => {
      const game = window.__game;
      if (game?.ready && game.state) {
        const state = (game.state as () => GameState)();
        const row: RouteSample = { time: performance.now(), feet: state.feet, speed: state.mode === 'car' ? state.vehicle?.speed ?? 0 : Math.hypot(state.vel[0], state.vel[2]),
          grounded: state.grounded, mode: state.mode, frameMs: state.frameMs, ground: null, groundChecked: false };
        window.__routeSamples.push(row);
        if (!busy && row.time - lastRay >= 100) {
          busy = true; lastRay = row.time;
          const [x, y, z] = state.feet;
          (game.physics as PhysicsClient).raycast({ x, y: y + 3, z }, { x: 0, y: -1, z: 0 }, 30).then(hit => {
            row.ground = hit?.point.y ?? null; row.groundChecked = true; busy = false;
          });
        }
      }
      requestAnimationFrame(sample);
    };
    requestAnimationFrame(sample);
  });
}

export const test = isolatedTest.extend<{ evidence: void }>({
  evidence: [async ({ page }, use, info) => {
    const errors: string[] = [];
    page.on('pageerror', error => errors.push(error.message));
    page.on('console', message => { if (message.type() === 'error') errors.push(message.text()); });
    await capture(page);
    await use();
    if (!page.isClosed()) {
      const evidence = await page.evaluate(() => {
        const perf: unknown = window.__perf;
        return { samples: window.__routeSamples ?? [], state: window.__game?.state ? (window.__game.state as () => GameState)() : null,
          backend: window.__game?.backend, perf: typeof perf === 'function' ? perf() : perf ?? null };
      });
      await info.attach('route-metrics', { body: JSON.stringify(evidence, null, 2), contentType: 'application/json' });
      await info.attach('route-finish', { body: await page.screenshot(), contentType: 'image/png' });
      await info.attach('browser-errors', { body: JSON.stringify(errors), contentType: 'application/json' });
      expect(errors, 'Browser console / uncaught errors').toEqual([]);
      expect(evidence.state?.errors ?? [], 'Game errors').toEqual([]);
    }
  }, { auto: true, timeout: 15_000 }],
});
export { expect };
export type { Page, ConsoleMessage } from '@playwright/test';

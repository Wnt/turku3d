import { test as base, expect, type Page } from '@playwright/test';
import type { PhysicsClient } from '../../src/physics/client';
import { frames } from './waits';

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
export const isolatedTest = base.extend<{ isolatedInput: void }>({
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

import os from 'node:os';
import { test, type Page, type TestInfo } from '@playwright/test';

// Browser checks wait on the game's own clock (`__game.clock`, src/main.ts), not on wall-clock time: a host
// starved by other sandboxes renders fewer frames per second, so the same simulated progress takes longer.
// Budgets are therefore in simulated seconds; the test timeout only guards against hangs and is scaled by the
// frame time measured when the test opens its first page. No wall-clock sleeps.

/** Frames sampled for the frame-time scale; the sample stops early after this much wall time. */
const PACE_FRAMES = 30;
const PACE_WINDOW_MS = 3_000;
/** Upper bound for the timeout scale, so one pathological stall cannot turn a hang guard into hours. */
const MAX_PACE_SCALE = 20;
const IDLE_FRAME_MS = 1000 / 60;

interface Pace { p95FrameMs: number; meanFrameMs: number; frames: number; scale: number; hostLoad: number; timeoutMs: number }
const paced = new WeakMap<TestInfo, Pace>();

/** Timeout scale for a measured frame p95: 1 on an idle 60 Hz host, at most MAX_PACE_SCALE. */
function paceScale(p95FrameMs: number) {
  return Math.min(MAX_PACE_SCALE, Math.max(1, p95FrameMs / IDLE_FRAME_MS));
}

/**
 * Measures frame time on the current page once per test and scales the test timeout by
 * max(1, p95FrameMs / 16.7). Logs and attaches the factor. Later calls return the first measurement.
 */
export async function pace(page: Page, info: TestInfo = test.info()): Promise<Pace> {
  const known = paced.get(info);
  if (known) return known;
  const intervals = await page.evaluate(([count, windowMs]) => new Promise<number[]>(resolve => {
    const rows: number[] = [];
    const start = performance.now();
    let last = start;
    const step = (now: number) => {
      rows.push(now - last);
      last = now;
      if (rows.length >= count || now - start >= windowMs) resolve(rows);
      else requestAnimationFrame(step);
    };
    requestAnimationFrame(now => { last = now; requestAnimationFrame(step); });
  }), [PACE_FRAMES, PACE_WINDOW_MS] as const);
  const sorted = [...intervals].sort((a, b) => a - b);
  // A single frame longer than the window still means the host is starved: count the whole window.
  const p95FrameMs = sorted.length ? sorted[Math.min(sorted.length - 1, Math.floor(sorted.length * 0.95))] : PACE_WINDOW_MS;
  const meanFrameMs = sorted.length ? sorted.reduce((sum, value) => sum + value, 0) / sorted.length : PACE_WINDOW_MS;
  const scale = paceScale(p95FrameMs);
  const timeoutMs = Math.round(info.timeout * scale);
  info.setTimeout(timeoutMs);
  const result = { p95FrameMs, meanFrameMs, frames: sorted.length, scale, hostLoad: os.loadavg()[0], timeoutMs };
  paced.set(info, result);
  info.annotations.push({ type: 'pace', description: JSON.stringify(result) });
  console.log(`[pace] ${info.project.name} ${info.title}: frame p95 ${p95FrameMs.toFixed(1)} ms over ${sorted.length} frames, `
    + `host load ${result.hostLoad.toFixed(1)}, timeout x${scale.toFixed(2)} = ${(timeoutMs / 1000).toFixed(0)} s`);
  return result;
}

/** Navigates and measures the frame-time scale on the first page a test opens. */
export async function open(page: Page, url: string) {
  await page.goto(url);
  return pace(page);
}

/**
 * Waits until `predicate(arg)` holds in the page, failing after `simSeconds` of simulated game time
 * (`__game.clock.simSeconds`) without it. On an idle host one simulated second is one wall second; on a starved
 * host it takes longer, bounded by the scaled test timeout. The predicate must be self-contained (it is serialised).
 */
export async function untilSim<A>(page: Page, predicate: (arg: A) => unknown, arg: A, simSeconds = 60) {
  const start: number = await page.evaluate(() => (window as any).__game?.clock?.simSeconds ?? 0);
  const wallStart = Date.now();
  const expression = `((predicate, arg, start, budget) => {
    if (predicate(arg)) return { met: true };
    const elapsed = (window.__game?.clock?.simSeconds ?? 0) - start;
    return elapsed > budget ? { met: false, elapsed } : false;
  })(${predicate.toString()}, ${JSON.stringify(arg) ?? 'undefined'}, ${start}, ${simSeconds})`;
  const result = await (await page.waitForFunction(expression, undefined, { timeout: 0 })).jsonValue() as { met: boolean; elapsed?: number };
  if (!result.met) {
    throw new Error(`condition not met within ${simSeconds} simulated s (${result.elapsed!.toFixed(1)} simulated s, `
      + `${((Date.now() - wallStart) / 1000).toFixed(1)} wall s): ${predicate.toString()}`);
  }
}

/** Observes browser frames, including while physics is paused. */
export async function frames(page: Page, count = 3) {
  await page.evaluate(n => new Promise<void>(resolve => {
    const step = () => { if (--n <= 0) resolve(); else requestAnimationFrame(step); };
    requestAnimationFrame(step);
  }), count);
}

/** Waits until the character stands still on the ground, within 60 simulated seconds. */
export async function settled(page: Page) {
  await untilSim(page, () => {
    const s = (window as any).__game.state();
    return s.grounded && Math.hypot(...s.vel) < 0.05 &&
      Math.abs(s.visualFeet[1] - s.feet[1]) < 0.04 && s.contacts.every((c: any) => c.lift < 0.05);
  }, undefined, 60);
}

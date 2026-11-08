import { afterEach, expect, test, vi } from 'vitest';
import { paceYields } from '../../src/render/yieldPacer';

afterEach(() => { vi.unstubAllGlobals(); vi.useRealTimers(); });

test('during play shader-build continuations run after frames within a budget; outside play they are not held', async () => {
  let clock = 0, frames: (() => void)[] = [], nativeCalls = 0;
  vi.stubGlobal('scheduler', { yield: () => { nativeCalls++; return Promise.resolve(); } });
  vi.stubGlobal('document', { visibilityState: 'visible' });
  vi.stubGlobal('requestAnimationFrame', (callback: () => void) => { frames.push(callback); return 0; });
  vi.spyOn(performance, 'now').mockImplementation(() => clock);
  let playing = false;
  const pacer = paceYields(() => playing, () => 3)!;
  const scheduler = (globalThis as unknown as { scheduler: { yield(): Promise<void> } }).scheduler;
  await scheduler.yield();
  expect(nativeCalls).toBe(1);                 // loading: the browser's own yield
  playing = true;
  // Three build stages of 10 ms each, as r186's buildAsync runs them between yields.
  const ran: number[] = [];
  const build = (async () => { for (let stage = 0; stage < 3; stage++) { await scheduler.yield(); clock += 10; ran.push(clock); } })();
  const frame = async () => {
    const pending = frames; frames = [];
    for (const callback of pending) callback();
    await new Promise(resolve => setTimeout(resolve, 25));
  };
  await new Promise(resolve => setTimeout(resolve, 25));
  expect(ran).toEqual([]);                     // nothing runs before the next frame
  await frame(); expect(ran.length).toBe(1);   // one 10 ms stage ends the gap's 3 ms share
  await frame(); expect(ran.length).toBe(2);
  await frame(); await build; expect(ran.length).toBe(3);
  expect(nativeCalls).toBe(1);
  // Leaving play releases whatever is still queued.
  const late = scheduler.yield(); playing = false;
  await frame(); await late;
  pacer.release();
  expect(scheduler.yield).not.toBe(undefined);
});

test('cheap continuations, even ones that start later in the same gap, all run inside one frame gap', async () => {
  let clock = 0, frames: (() => void)[] = [];
  vi.stubGlobal('scheduler', { yield: () => Promise.resolve() });
  vi.stubGlobal('document', { visibilityState: 'visible' });
  vi.stubGlobal('requestAnimationFrame', (callback: () => void) => { frames.push(callback); return 0; });
  vi.spyOn(performance, 'now').mockImplementation(() => clock);
  const pacer = paceYields(() => true, () => 3)!;
  const scheduler = (globalThis as unknown as { scheduler: { yield(): Promise<void> } }).scheduler;
  const frame = async () => { const pending = frames; frames = []; for (const callback of pending) callback(); await new Promise(resolve => setTimeout(resolve, 25)); };
  // A tile's preparation: compileAsync per mesh, each yielding once for a cached program (no build time).
  let compiled = 0;
  const prepare = (async () => { for (let mesh = 0; mesh < 40; mesh++) { await scheduler.yield(); compiled++; } })();
  await frame();
  expect(compiled).toBe(40);
  await prepare;
  // A second preparation arriving inside the same gap continues at once instead of waiting a frame.
  clock += 1;
  const late = (async () => { await scheduler.yield(); compiled++; })();
  await new Promise(resolve => setTimeout(resolve, 25));
  expect(compiled).toBe(41);
  await late;
  pacer.release();
});

test('a yield early in a gap after frames the pacer did not wait for continues at once', async () => {
  let clock = 0, frames: (() => void)[] = [];
  vi.stubGlobal('scheduler', { yield: () => Promise.resolve() });
  vi.stubGlobal('document', { visibilityState: 'visible' });
  vi.stubGlobal('requestAnimationFrame', (callback: () => void) => { frames.push(callback); return 0; });
  vi.spyOn(performance, 'now').mockImplementation(() => clock);
  const pacer = paceYields(() => true, () => 3)!;
  const scheduler = (globalThis as unknown as { scheduler: { yield(): Promise<void> } }).scheduler;
  const frame = async () => { clock += 16; const pending = frames; frames = []; for (const callback of pending) callback(); await new Promise(resolve => setTimeout(resolve, 25)); };
  let ran = 0;
  const first = (async () => { await scheduler.yield(); ran++; })();
  await frame(); await first;
  expect(ran).toBe(1);
  // Frames pass while nothing yields (a compile waited for a budget window); the next yield comes 1 ms after one.
  await frame(); await frame();
  clock += 1;
  const second = (async () => { await scheduler.yield(); ran++; })();
  await new Promise(resolve => setTimeout(resolve, 25));
  expect(ran).toBe(2);
  // Late in a gap (past the share) a yield still waits for the next frame.
  clock += 5;
  const third = (async () => { await scheduler.yield(); ran++; })();
  await new Promise(resolve => setTimeout(resolve, 25));
  expect(ran).toBe(2);
  await frame(); await Promise.all([second, third]);
  expect(ran).toBe(3);
  pacer.release();
});

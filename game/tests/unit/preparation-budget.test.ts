import { expect, test, vi } from 'vitest';
import { MainThreadIdle, PreparationBudget, StreamingAllowance } from '../../src/render/preparationBudget';
import { STREAMING_TIERS } from '../../src/render/quality';
import { afterRender } from '../../src/render/afterRender';

test('preparation resumes after animation callbacks rather than inside the frame being drawn', async () => {
  vi.useFakeTimers();
  const events: string[] = [];
  let frame!: FrameRequestCallback;
  vi.stubGlobal('requestAnimationFrame', (callback: FrameRequestCallback) => { frame = callback; return 1; });
  try {
    afterRender(() => events.push('preparation'));
    frame(0);
    events.push('live render');
    expect(events).toEqual(['live render']);
    await vi.advanceTimersByTimeAsync(0);
    expect(events).toEqual(['live render', 'preparation']);
  } finally { vi.useRealTimers(); vi.unstubAllGlobals(); }
});

test('preparation yields between atomic draws and shares one pending frame', async () => {
  let now = 0, advance: (() => void) | undefined, frames = 0;
  const budget = new PreparationBudget(() => now, () => { frames++; return new Promise(resolve => { advance = resolve; }); });
  await budget.checkpoint();
  now = 1; await budget.checkpoint(); expect(frames).toBe(0);
  now = 3;
  let completed = 0;
  const first = budget.checkpoint()!.then(() => completed++);
  const second = budget.checkpoint()!.then(() => completed++);
  expect(frames).toBe(1); expect(completed).toBe(0);
  now = 20; advance!(); await Promise.all([first, second]);
  expect(completed).toBe(2);
  now = 21; await budget.checkpoint(); expect(frames).toBe(1);
});

test('a streaming budget resumes a stale window after one task hop, not a drawn frame', async () => {
  let now = 0, frames = 0, tasks = 0;
  const frame = () => { frames++; return Promise.resolve(); }, task = () => { tasks++; return Promise.resolve(); };
  const budget = new PreparationBudget(() => now, frame, task);
  await budget.checkpoint(); now = 3;
  await budget.checkpoint(); // A continuous burst past 2 ms still waits for the next frame.
  expect([frames, tasks]).toEqual([1, 0]);
  now = 100; // A worker reply long after the window opened.
  await budget.checkpoint();
  expect([frames, tasks]).toEqual([1, 1]);
  now = 101; expect(budget.checkpoint()).toBeUndefined(); // the new window is open
  const plain = new PreparationBudget(() => now, frame);
  await plain.checkpoint(); now = 300; await plain.checkpoint();
  expect([frames, tasks]).toEqual([2, 1]); // frame-aligned budgets (shader compiles) keep waiting for a frame
});

test('the window steers the frame interval to 24 ms; prefetch only spends what frames leave under 20.5 ms', () => {
  let now = 0;
  const account = new StreamingAllowance(() => now);
  const frames = (n: number, interval: number, windows = 1) => {
    for (let i = 0; i < n; i++) { for (let w = 0; w < windows; w++) account.opened(); account.frame(8, interval); }
  };
  frames(60, 16.7); // frames come faster than 48 fps while prefetch has work
  expect(account.limit).toBe(3); // prefetch-only: capped
  frames(80, 22); // 45 fps: prefetch is over its 20.5 ms target and backs off to its floor
  expect(account.limit).toBe(StreamingAllowance.MIN_MS);
  account.urgent = true; frames(60, 22); // the 3x3 window waits: the same frames are under the 24 ms target
  expect(account.limit).toBe(6);
  frames(1, 22, 4);
  expect(account.limit).toBe(3); // four budgets worked last frame: they share the 12 ms total
  frames(80, 30); // over the window target too
  expect(account.limit).toBeCloseTo(6, 3); // the window keeps its 20 % share of the frame: 6 of 30 ms
  frames(60, 16.7, 4); const before = account.limit;
  frames(1, 60, 4); // one overlong frame halves the total at once
  expect(account.limit).toBeLessThan(before);
  now = 500; expect(account.limit).toBe(2); // no frame reports: the old fixed window
});

test('while the player moves fast the window keeps frames smooth: 17.5 ms instead of 24 ms, its share kept', () => {
  const account = new StreamingAllowance(() => 0);
  const frames = (n: number, interval: number) => { for (let i = 0; i < n; i++) { account.opened(); account.frame(8, interval); } };
  account.urgent = true; frames(60, 20); // 50 fps: under the window's 24 ms target, the window grows
  expect(account.limit).toBe(6);
  account.moving = true; frames(80, 20); // the same frames at speed are over 17.5 ms: back to the window's share
  expect(account.target).toBe(StreamingAllowance.MOVING_TARGET);
  expect(account.limit).toBeCloseTo(4, 3); // 20 % of 20 ms
  account.moving = false; frames(80, 20); // stopped: the window waits at 24 ms again
  expect(account.limit).toBe(6);
});

test('every streaming budget keeps its own window, so one stage cannot starve another', async () => {
  let now = 0, frames = 0;
  const account = new StreamingAllowance(() => now);
  account.urgent = true; for (let i = 0; i < 60; i++) { account.opened(); account.frame(8, 16.7); }
  const frame = () => { frames++; return Promise.resolve(); };
  const a = new PreparationBudget(() => now, frame, null, account), b = new PreparationBudget(() => now, frame, null, account);
  const limit = account.limit;
  expect(limit).toBeGreaterThan(2);
  a.checkpoint(); now = limit + 0.1; await a.checkpoint(); // a used its window
  expect(frames).toBe(1);
  b.checkpoint(); now += limit - 0.5; expect(b.checkpoint()).toBeUndefined(); // b still has its own
});

test('CPU-bound frames (busy main thread, no idle) steer the interval as before, whatever the GPU time', () => {
  const account = new StreamingAllowance(() => 0);
  account.urgent = true;
  // M1 Max-like: 25 ms frames, callback 20 ms, GPU 24 ms, the main thread never idles.
  for (let i = 0; i < 80; i++) { account.opened(); account.frame(20, 25, 0, 24); }
  // Over the 24 ms target: the window keeps its 20 % share (5 of 25 ms), not the 2 ms floor that left tiles under the
  // car 17-40 s behind on the M1 Max (city.lab drive 2026-11-08).
  expect(account.limit).toBeCloseTo(5, 3);
  for (let i = 0; i < 5; i++) { account.opened(); account.frame(20, 45, 0, 24); }
  expect(account.limit).toBeCloseTo(6, 3); // CPU-caused long frames halve the total; the share counts 30 ms at most
});

test('GPU-bound frames spend the main thread idle time without halving on GPU-caused long frames', () => {
  const account = new StreamingAllowance(() => 0);
  account.urgent = true;
  // Radeon Pro 560X-like: 37 ms frames held by a 33 ms GPU, callback 23 ms, about 12 ms of main-thread idle.
  for (let i = 0; i < 80; i++) { account.opened(); account.frame(23, 37, 12, 33); }
  expect(account.limit).toBe(6); // the idle is slack: streaming grows to its cap instead of sitting at the floor
  for (let i = 0; i < 3; i++) { account.opened(); account.frame(23, 45, 12, 40); }
  expect(account.limit).toBe(6); // a long frame the GPU caused does not halve the total
  // Idle around the margin: no growth; it gives a little back below the margin, not the CPU-bound collapse.
  for (let i = 0; i < 30; i++) { account.opened(); account.frame(27, 37, 3.5, 33); }
  expect(account.limit).toBeGreaterThan(4);
  // Streaming used the idle up: the frame is CPU-bound again and the interval controller takes it back.
  expect(account.stats().total).toBeGreaterThan(10);
  for (let i = 0; i < 80; i++) { account.opened(); account.frame(30, 37, 1, 33); }
  expect(account.stats().total).toBe(6); // down to the window's share (20 % of 30 ms at most)
  // Idle from a missed vsync (a fast GPU) is not free: CPU-bound control.
  const vsync = new StreamingAllowance(() => 0); vsync.urgent = true;
  for (let i = 0; i < 80; i++) { vsync.opened(); vsync.frame(14, 33, 15, 8); }
  expect(vsync.stats().total).toBe(6); // the share, not the GPU-bound growth to 12
});

test('the idle meter counts each idle period once and caps it by the frame', () => {
  const callbacks: ((deadline: IdleDeadline) => void)[] = [];
  const original = (globalThis as { requestIdleCallback?: unknown }).requestIdleCallback;
  (globalThis as { requestIdleCallback?: unknown }).requestIdleCallback = (callback: (deadline: IdleDeadline) => void) => { callbacks.push(callback); return 1; };
  let now = 0;
  try {
    const idle = new MainThreadIdle(() => now); idle.start(); idle.start();
    expect(callbacks).toHaveLength(1);
    const run = (remaining: number) => callbacks.shift()!({ timeRemaining: () => remaining, didTimeout: false });
    // Callbacks re-posted inside one idle period see the same deadline: the period counts once.
    now = 0; run(7); now = 0.5; run(6.5); now = 3; run(4); now = 6.9; run(0.1);
    expect(idle.take()).toBe(7);
    // The next period counts again.
    now = 20; run(3);
    expect(idle.take()).toBe(3);
    // A frame caps idle by what its interval leaves after the callback.
    now = 40; run(9);
    expect(idle.take(0.7)).toBe(0.7);
    expect(idle.take()).toBe(0);
  } finally { (globalThis as { requestIdleCallback?: unknown }).requestIdleCallback = original; }
});

test('a fast static scene (84 fps, GPU about the interval, little idle) is not GPU-bound', () => {
  const account = new StreamingAllowance(() => 0);
  // perf3 bench=static on the M1 Max: interval 11.9 ms, callback 11.2 ms, GPU 13 ms; idle capped at 0.7 ms.
  for (let i = 0; i < 80; i++) { account.opened(); account.frame(11.2, 11.9, 0.7, 13); }
  expect((account as unknown as { idle: number }).idle).toBeLessThan(StreamingAllowance.GPU_BOUND_IDLE_MS);
  // The double-counted idle (a whole interval) would have looked like a GPU wait.
  const overcounted = new StreamingAllowance(() => 0);
  for (let i = 0; i < 80; i++) { overcounted.opened(); overcounted.frame(11.2, 11.9, 11.9, 13); }
  expect((overcounted as unknown as { idle: number }).idle).toBeGreaterThan(StreamingAllowance.GPU_BOUND_IDLE_MS);
});

test('while the world loads behind the loading screen every budget gets the long loading window', async () => {
  let now = 0, frames = 0, loading = true;
  const account = new StreamingAllowance(() => now);
  account.loading = () => loading;
  // No frame reports during boot: the play-time fallback would be BASE_MS.
  expect(account.limit).toBe(StreamingAllowance.LOADING_MS);
  const budget = new PreparationBudget(() => now, () => { frames++; return Promise.resolve(); }, null, account);
  await budget.checkpoint();
  now = StreamingAllowance.LOADING_MS - 1; expect(budget.checkpoint()).toBeUndefined();
  now = StreamingAllowance.LOADING_MS; await budget.checkpoint(); expect(frames).toBe(1);
  // The menu is ready: play-time windows again.
  loading = false;
  expect(account.limit).toBe(StreamingAllowance.BASE_MS);
});

test('continuous streaming (ultra) resumes after a task hop between frames and waits for a frame once one is overdue', async () => {
  let now = 0, hops = 0, frames = 0;
  const account = new StreamingAllowance(() => now, () => { hops++; return Promise.resolve(); }, () => { frames++; return Promise.resolve(); });
  account.tuning = STREAMING_TIERS.ultra;
  const budget = new PreparationBudget(() => now, () => { throw new Error('a continuous budget never waits on its own frame'); }, null, account);
  account.frame(8, 120); // a GPU-bound ultra frame
  expect(account.continuous).toBe(true);
  expect(account.limit).toBe(12); // one slice, however long ago the frame was
  await budget.checkpoint();
  now = 13; await budget.checkpoint(); // the slice is used: one task hop, no frame
  expect([hops, frames]).toEqual([1, 0]);
  now = 26; await budget.checkpoint();
  expect([hops, frames]).toEqual([2, 0]);
  now = 600; await budget.checkpoint(); // no frame for 600 ms: past the 500 ms gap, streaming waits for the next one
  expect([hops, frames]).toEqual([2, 1]);
  account.loading = () => true; // behind the loading screen streaming keeps its frame-paced windows
  expect(account.continuous).toBe(false);
  account.loading = () => false; account.tuning = STREAMING_TIERS.high;
  expect(account.continuous).toBe(false);
});

test('continuous streaming holds back sooner while frames come slower than 1 FPS allows', () => {
  let now = 0;
  const account = new StreamingAllowance(() => now, () => Promise.resolve(), () => Promise.resolve());
  account.tuning = STREAMING_TIERS.ultra;
  account.frame(8, 120); now = 499;
  expect(account.overdue).toBe(false);
  account.frame(8, 900); // a frame behind a GPU queue of uploads: over 75 % of the 1 s budget
  now += 300;
  expect(account.overdue).toBe(true); // the gap halved to 250 ms
  for (let i = 0; i < 40; i++) account.frame(8, 120); // fast frames let it grow back
  now += 499;
  expect(account.overdue).toBe(false);
  expect(account.stats().gap).toBe(500);
});

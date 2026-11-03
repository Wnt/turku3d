import { expect, test, vi } from 'vitest';
import { MainThreadIdle, PreparationBudget, StreamingAllowance } from '../../src/render/preparationBudget';
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
  expect(account.limit).toBe(StreamingAllowance.BASE_MS); // the window's floor
  frames(60, 16.7, 4); const before = account.limit;
  frames(1, 60, 4); // one overlong frame halves the total at once
  expect(account.limit).toBeLessThan(before);
  now = 500; expect(account.limit).toBe(2); // no frame reports: the old fixed window
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
  expect(account.limit).toBe(StreamingAllowance.BASE_MS); // over the 24 ms target: the window's floor
  for (let i = 0; i < 5; i++) { account.opened(); account.frame(20, 45, 0, 24); }
  expect(account.limit).toBe(StreamingAllowance.BASE_MS); // CPU-caused long frames halve (already at the floor)
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
  for (let i = 0; i < 80; i++) { account.opened(); account.frame(30, 37, 1, 33); }
  expect(account.limit).toBe(StreamingAllowance.BASE_MS);
  // Idle from a missed vsync (a fast GPU) is not free: CPU-bound control.
  const vsync = new StreamingAllowance(() => 0); vsync.urgent = true;
  for (let i = 0; i < 80; i++) { vsync.opened(); vsync.frame(14, 33, 15, 8); }
  expect(vsync.limit).toBe(StreamingAllowance.BASE_MS);
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

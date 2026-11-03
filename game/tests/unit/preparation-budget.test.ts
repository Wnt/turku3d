import { expect, test, vi } from 'vitest';
import { PreparationBudget, StreamingAllowance } from '../../src/render/preparationBudget';
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

test('streaming windows steer the measured frame interval to 24 ms; the current window never drops below 2 ms', () => {
  let now = 0;
  const account = new StreamingAllowance(() => now);
  const frames = (n: number, interval: number, windows = 1) => {
    for (let i = 0; i < n; i++) { for (let w = 0; w < windows; w++) account.opened(); account.frame(8, interval); }
  };
  frames(60, 16.7); // frames come faster than 40 fps while streaming has work
  expect(account.limit).toBe(3); // prefetch-only: capped
  account.urgent = true; frames(60, 16.7);
  expect(account.limit).toBe(6); // the 3x3 window waits: larger windows
  frames(1, 16.7, 4);
  expect(account.limit).toBe(3); // four budgets worked last frame: they share the 12 ms total
  frames(80, 30); // the interval (callback + every post-frame task) runs over the target
  expect(account.limit).toBe(StreamingAllowance.BASE_MS); // shrinks to the window's floor
  account.urgent = false; frames(5, 30);
  expect(account.limit).toBe(StreamingAllowance.MIN_MS);
  account.urgent = true; frames(60, 16.7, 4); const before = account.limit;
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

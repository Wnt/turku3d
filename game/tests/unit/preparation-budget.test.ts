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

test('streaming windows follow the frame headroom against a 40 fps target; the current window never drops below 2 ms', () => {
  let now = 0;
  const account = new StreamingAllowance(() => now);
  for (let i = 0; i < 30; i++) account.frame(10, 16.7); // a light 10 ms frame callback: 13 ms of slack
  expect(account.limit).toBe(3); // prefetch-only installs: capped
  account.urgent = true;
  expect(account.limit).toBe(6); // the 3x3 window waits: one budget may take most of the slack (capped)
  for (let i = 0; i < 4; i++) account.opened();
  account.frame(10, 16.7);
  expect(account.limit).toBeCloseTo(0.8 * 13 / 4, 0); // four budgets worked last frame: they share it
  for (let i = 0; i < 30; i++) account.frame(21, 22);
  expect(account.limit).toBe(2); // little slack: the window keeps the old 2 ms
  account.urgent = false;
  expect(account.limit).toBeCloseTo(1.6, 1); // prefetch takes 80 % of the 2 ms slack
  for (let i = 0; i < 3; i++) account.opened();
  account.frame(21, 22);
  expect(account.limit).toBe(StreamingAllowance.MIN_MS); // shared by three budgets: the 1 ms floor
  account.frame(21, 45); account.urgent = true;
  expect(account.limit).toBe(2); // an overlong frame: the window still gets its floor
  now = 500; expect(account.limit).toBe(2); // no frame reports: the old fixed window
});

test('every streaming budget keeps its own window, so one stage cannot starve another', async () => {
  let now = 0, frames = 0;
  const account = new StreamingAllowance(() => now);
  account.urgent = true; for (let i = 0; i < 30; i++) account.frame(15, 20);
  const frame = () => { frames++; return Promise.resolve(); };
  const a = new PreparationBudget(() => now, frame, null, account), b = new PreparationBudget(() => now, frame, null, account);
  const limit = account.limit;
  expect(limit).toBeGreaterThan(2);
  a.checkpoint(); now = limit + 0.1; await a.checkpoint(); // a used its window
  expect(frames).toBe(1);
  b.checkpoint(); now += limit - 0.5; expect(b.checkpoint()).toBeUndefined(); // b still has its own
});

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

test('streaming spends the frame headroom against a 40 fps target, more for the current window, little after long frames', async () => {
  let now = 0;
  const account = new StreamingAllowance(() => now);
  for (let i = 0; i < 30; i++) account.frame(10, 16.7); // a light 10 ms frame callback
  expect(account.limit).toBe(3); // prefetch-only installs: capped
  account.urgent = true;
  expect(account.limit).toBe(8); // the 3x3 window waits: most of the 13 ms slack
  for (let i = 0; i < 30; i++) account.frame(21, 22);
  expect(account.limit).toBeCloseTo(1.6, 0); // little slack left
  account.frame(21, 45);
  expect(account.limit).toBe(StreamingAllowance.MIN_MS); // an overlong frame: back off
  now = 500; expect(account.limit).toBe(2); // no frame reports: the old fixed window
  // Budgets share one account: what one stage spent this frame is gone for the others.
  now = 0; account.urgent = true; for (let i = 0; i < 30; i++) account.frame(15, 20);
  let frames = 0;
  const frame = () => { frames++; return Promise.resolve(); };
  const a = new PreparationBudget(() => now, frame, null, account), b = new PreparationBudget(() => now, frame, null, account);
  const limit = account.limit;
  a.checkpoint(); b.checkpoint();
  now = limit - 0.5; expect(a.checkpoint()).toBeUndefined();
  now = limit + 0.1; await a.checkpoint(); // a spent the frame's allowance
  expect(frames).toBe(1);
  await b.checkpoint(); // b's own window is short, but the account is spent
  expect(frames).toBe(2);
});

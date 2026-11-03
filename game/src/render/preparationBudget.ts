import { afterRender } from './afterRender';

/** A window opened longer ago than this (ms) belongs to an earlier frame. */
const STALE_MS = 16;
const nextTask = () => new Promise<void>(resolve => setTimeout(resolve, 0));

/** Streaming windows from the measured frame interval (render/performance.ts reports each frame). The interval
 *  between frame callbacks includes everything the main thread did in between: post-frame streaming tasks, worker
 *  replies, fetch handling and GC, not only the callback. An integral controller steers the total streaming time
 *  per frame so the interval averages the 24 ms target (40 fps): it grows while frames come faster and streaming has
 *  work, shrinks while they come slower, and halves after an overlong frame. The budgets that worked in the last
 *  frame share it; each keeps a floor (2 ms while the current 3x3 window waits for installs, `urgent`, else 1 ms). */
export class StreamingAllowance {
  /** Target frame interval (ms) for a 40 fps average. */
  readonly target = 24;
  static readonly MIN_MS = 1;
  static readonly BASE_MS = 2;
  /** Streaming ms per frame across all budgets (controller state). */
  private total = 4;
  private interval = 20;
  private frameAt = -Infinity;
  /** Budget windows opened since the last frame, and in the frame before: they share the total. */
  private windows = 0;
  private lastWindows = 1;
  /** The current 3x3 window still waits for full installs (coarseTiles). */
  urgent = false;
  constructor(private now = () => performance.now()) {}
  frame(_cpuMs: number, intervalMs: number | null) {
    this.frameAt = this.now();
    const worked = this.windows > 0;
    this.lastWindows = Math.max(1, this.windows); this.windows = 0;
    if (intervalMs === null) return;
    this.interval += (intervalMs - this.interval) * 0.15;
    if (intervalMs > this.target * 1.6) this.total *= 0.5;
    // Grow only while streaming has work: no wind-up during quiet periods.
    else if (worked || this.interval > this.target) this.total += 0.1 * (this.target - this.interval);
    this.total = Math.min(this.urgent ? 12 : 6, Math.max(StreamingAllowance.MIN_MS, this.total));
  }
  /** A budget starts a window this frame. */
  opened() { this.windows++; }
  /** Milliseconds one streaming budget may work in this frame. */
  get limit() {
    // No frame reports (a page without the monitor, or a stalled loop): the old fixed window.
    if (this.now() - this.frameAt > 100) return StreamingAllowance.BASE_MS;
    const floor = this.urgent ? StreamingAllowance.BASE_MS : StreamingAllowance.MIN_MS;
    return Math.min(this.urgent ? 6 : 3, Math.max(floor, this.total / this.lastWindows));
  }
}
export const streamingAllowance = new StreamingAllowance();

/** Shader preparation shares a frame budget across streamed models and render passes. Individual draws are atomic.
 *  `resumeStale`: sporadic work (a worker reply or fetch arriving long after the window opened) starts a new window
 *  after one task hop instead of waiting for the next drawn frame; still never inside an animation callback.
 *  `account`: the window follows the frame's measured headroom instead of a fixed 2 ms. */
export class PreparationBudget {
  private started: number | null = null;
  private pending: Promise<void> | null = null;
  constructor(private now = () => performance.now(), private nextFrame = () => new Promise<void>(resolve => afterRender(resolve)),
    private resumeStale: (() => Promise<void>) | null = null, private account: StreamingAllowance | null = null) {}
  checkpoint(): Promise<void> | undefined {
    if (this.started === null) { this.started = this.now(); this.account?.opened(); }
    if (this.pending) return this.pending;
    const elapsed = this.now() - this.started;
    const stale = elapsed > STALE_MS;
    const limit = this.account ? this.account.limit : 2;
    if (elapsed < limit) return;
    const wait = this.resumeStale && stale ? this.resumeStale() : this.nextFrame();
    this.pending = wait.then(() => { this.started = this.now(); this.pending = null; this.account?.opened(); });
    return this.pending;
  }
}

/** A streaming install budget: the frame's adaptive allowance, and sporadic steps do not wait a whole frame. */
export function streamingBudget() {
  return new PreparationBudget(undefined, undefined, nextTask, streamingAllowance);
}

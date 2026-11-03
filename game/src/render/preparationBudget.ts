import { afterRender } from './afterRender';

/** A window opened longer ago than this (ms) belongs to an earlier frame. */
const STALE_MS = 16;
const nextTask = () => new Promise<void>(resolve => setTimeout(resolve, 0));

/** Streaming window per budget and frame from the frame's measured headroom (render/performance.ts reports each
 *  frame). The frame callback's CPU is compared with a 40 fps target: with slack, each streaming budget's window grows
 *  from the old 2 ms (up to 6 ms while the current 3x3 window waits for installs, `urgent`, 3 ms otherwise); with
 *  little slack or after an overlong frame, prefetch-only work shrinks to 1 ms. The window's installs never drop
 *  below the old 2 ms: every budget keeps its own window, so no stage starves another. */
export class StreamingAllowance {
  /** Target main-thread frame time (ms) for a 40 fps average. */
  readonly target = 23;
  static readonly MIN_MS = 1;
  static readonly BASE_MS = 2;
  private cpu = 16;
  private slow = false;
  private frameAt = -Infinity;
  /** Budget windows opened since the last frame, and in the frame before: they share the slack. */
  private windows = 0;
  private lastWindows = 1;
  /** The current 3x3 window still waits for full installs (coarseTiles). */
  urgent = false;
  constructor(private now = () => performance.now()) {}
  frame(cpuMs: number, intervalMs: number | null) {
    this.cpu += (cpuMs - this.cpu) * 0.2;
    this.slow = intervalMs !== null && intervalMs > this.target * 1.6;
    this.frameAt = this.now();
    this.lastWindows = Math.max(1, this.windows); this.windows = 0;
  }
  /** A budget starts a window this frame. */
  opened() { this.windows++; }
  /** Milliseconds one streaming budget may work in this frame. */
  get limit() {
    // No frame reports (a page without the monitor, or a stalled loop): the old fixed window.
    if (this.now() - this.frameAt > 100) return StreamingAllowance.BASE_MS;
    const floor = this.urgent ? StreamingAllowance.BASE_MS : StreamingAllowance.MIN_MS;
    if (this.slow) return floor;
    // 80 % of the slack, shared by the budgets that worked in the last frame.
    const scaled = 0.8 * (this.target - this.cpu) / this.lastWindows;
    return Math.min(this.urgent ? 6 : 3, Math.max(floor, scaled));
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

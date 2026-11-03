import { afterRender } from './afterRender';

/** A window opened longer ago than this (ms) belongs to an earlier frame. */
const STALE_MS = 16;
const nextTask = () => new Promise<void>(resolve => setTimeout(resolve, 0));

/** Streaming work per frame from the frame's measured headroom (render/performance.ts reports each frame).
 *  The frame callback's CPU is compared with a 40 fps target: the slack is spent on streaming, more of it while
 *  the current 3x3 window waits for installs (`urgent`), and only a minimum after an overlong frame. All streaming
 *  budgets draw from this one account, so several stages never add up past the slack. */
export class StreamingAllowance {
  /** Target main-thread frame time (ms) for a 40 fps average. */
  readonly target = 23;
  static readonly MIN_MS = 1;
  private cpu = 16;
  private slow = false;
  private frameAt = -Infinity;
  /** Streaming window time spent since the last frame. */
  spent = 0;
  /** The current 3x3 window still waits for full installs (coarseTiles). */
  urgent = false;
  constructor(private now = () => performance.now()) {}
  frame(cpuMs: number, intervalMs: number | null) {
    this.cpu += (cpuMs - this.cpu) * 0.2;
    this.slow = intervalMs !== null && intervalMs > this.target * 1.6;
    this.spent = 0; this.frameAt = this.now();
  }
  /** Milliseconds of streaming work this frame may take in total. */
  get limit() {
    // No frame reports (a page without the monitor, or a stalled loop): fall back to the old fixed window.
    if (this.now() - this.frameAt > 100) { this.spent = 0; return 2; }
    if (this.slow) return StreamingAllowance.MIN_MS;
    const slack = this.target - this.cpu;
    return Math.min(this.urgent ? 8 : 3, Math.max(StreamingAllowance.MIN_MS, slack * (this.urgent ? 0.8 : 0.4)));
  }
}
export const streamingAllowance = new StreamingAllowance();

/** Shader preparation shares a frame budget across streamed models and render passes. Individual draws are atomic.
 *  `resumeStale`: sporadic work (a worker reply or fetch arriving long after the window opened) starts a new window
 *  after one task hop instead of waiting for the next drawn frame; still never inside an animation callback.
 *  `account`: the window is the frame's adaptive streaming allowance instead of a fixed 2 ms. */
export class PreparationBudget {
  private started: number | null = null;
  private pending: Promise<void> | null = null;
  constructor(private now = () => performance.now(), private nextFrame = () => new Promise<void>(resolve => afterRender(resolve)),
    private resumeStale: (() => Promise<void>) | null = null, private account: StreamingAllowance | null = null) {}
  checkpoint(): Promise<void> | undefined {
    this.started ??= this.now();
    if (this.pending) return this.pending;
    const elapsed = this.now() - this.started;
    const stale = elapsed > STALE_MS;
    const limit = this.account ? this.account.limit - this.account.spent : 2;
    if (elapsed < limit) return;
    if (this.account && !stale) this.account.spent += elapsed;
    const wait = this.resumeStale && stale ? this.resumeStale() : this.nextFrame();
    this.pending = wait.then(() => { this.started = this.now(); this.pending = null; });
    return this.pending;
  }
}

/** A streaming install budget: the frame's adaptive allowance, and sporadic steps do not wait a whole frame. */
export function streamingBudget() {
  return new PreparationBudget(undefined, undefined, nextTask, streamingAllowance);
}

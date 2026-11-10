import { afterRender } from './afterRender';
import { STREAMING_TIERS, type StreamingTier } from './quality';

/** A window opened longer ago than this (ms) belongs to an earlier frame. */
const STALE_MS = 16;
const nextTask = () => new Promise<void>(resolve => setTimeout(resolve, 0));

/** Main-thread idle time between frames, from the scheduler's idle periods (requestIdleCallback). Each idle period
 *  counts once, with the remaining time its first callback sees: a callback re-posted inside the same period sees the
 *  same deadline and adds nothing. The frame caps the total at its interval minus the callback's CPU. A main thread
 *  that is busy all the time (CPU-bound) reports none; one that waits on the GPU reports the wait. Zero where the API
 *  is missing. */
export class MainThreadIdle {
  private idle = 0;
  private periodEnd = -Infinity;
  private started = false;
  constructor(private now = () => performance.now()) {}
  start() {
    if (this.started || typeof requestIdleCallback !== 'function') return;
    this.started = true;
    requestIdleCallback(this.sample);
  }
  /** One idle callback: a deadline more than 1 ms past the last one opens a new period. */
  readonly sample = (deadline: IdleDeadline) => {
    const remaining = deadline.timeRemaining(), end = this.now() + remaining;
    if (end > this.periodEnd + 1) this.idle += remaining;
    this.periodEnd = Math.max(this.periodEnd, end);
    requestIdleCallback(this.sample);
  };
  /** Idle milliseconds since the previous call, at most `cap`. */
  take(cap = Infinity) { const idle = Math.min(this.idle, Math.max(0, cap)); this.idle = 0; return idle; }
}

/** Streaming windows from the measured frame (render/performance.ts reports each frame). The target is main-thread
 *  work per frame: 24 ms while the current 3x3 window lacks full tiles (`urgent`), 20.5 ms for prefetch, wedge and
 *  path work, so they only use what a frame leaves under about 48 fps.
 *  - CPU-bound (the main thread is busy the whole interval, as on the M1 Max): the interval between frame callbacks
 *    is the main thread's work, callback plus every post-frame task. An integral controller steers it to the target
 *    and halves the total after an overlong frame.
 *  - GPU-bound (the main thread idles while the GPU holds the interval, as on a Radeon Pro 560X): the scheduler's idle
 *    time is slack that costs no FPS; streaming grows until only IDLE_MARGIN_MS of it is left, and an overlong frame
 *    the GPU caused does not halve it.
 *  The budgets that worked in the last frame share the total; each keeps a floor (2 ms while the window waits). */
export class StreamingAllowance {
  static readonly WINDOW_TARGET = 24;
  static readonly PREFETCH_TARGET = 20.5;
  /** While the player moves fast (`moving`, coarseTiles) frames come first: at 23 m/s the window has stand-ins nearly
   *  all the time, so the urgent 24 ms target held the Air at ~50 fps once the graphics queue kept up (fastdrive
   *  fly 2026-11-08: 17.5 ms gave the same stand-ins at 53.5 fps). The per-budget floors still move installs along. */
  static readonly MOVING_TARGET = 17.5;
  /** The session tier's targets and caps (render/quality.ts STREAMING_TIERS; main.ts sets it with the tier): every
   *  tier but ultra has the values documented here. */
  tuning: StreamingTier = STREAMING_TIERS.medium;
  /** The window waits for full installs, or the tier streams as if it always did (ultra). */
  get pressed() { return this.tuning.always || this.urgent; }
  get target() {
    return this.moving && !this.tuning.always ? this.tuning.moving : this.pressed ? this.tuning.window : this.tuning.prefetch;
  }
  static readonly MIN_MS = 0.5;
  /** Each budget's window while the world loads behind the loading screen (boot and city switches, `loading`): no
   *  frame is drawn then (main.ts renderFrame), and the 2 ms fallback window paced boot work at ~12 % of the main
   *  thread. The parked fleet's bake alone held a cold city.lab load 28 s on the M1 Air with the main thread 79 % idle
   *  (loadspeed 2026-11-09). 12 ms keeps the loading screen's progress drawing every frame. */
  static readonly LOADING_MS = 12;
  /** True while the world loads behind the loading screen (main.ts: play, until the menu is ready). */
  loading: () => boolean = () => false;
  /** While the 3x3 window waits for full installs, streaming keeps at least this share of the frame interval,
   *  whatever the frame costs: a frame that costs more than the target on its own (the M1 Max at 20 ms of rendering
   *  in the city centre, a busy desktop) otherwise left streaming its per-budget floors, about one GLB continuation
   *  and one graphics step a frame, and tiles under the car took 17-40 s (city.lab drive 2026-11-08). A share s
   *  costs at most s of the frame rate: 24 ms frames become 30 ms (42 to 33 fps) until the window is full; frames
   *  over 30 ms (a slow machine) keep 6 ms. */
  static readonly URGENT_SHARE = 0.2;
  static readonly BASE_MS = 2;
  /** Idle the main thread keeps per frame: streaming grows into idle above IDLE_MARGIN_MS and gives back below it;
   *  under GPU_BOUND_IDLE_MS of idle the frame counts as CPU-bound (the gap keeps the two modes from cycling). */
  static readonly IDLE_MARGIN_MS = 4;
  static readonly GPU_BOUND_IDLE_MS = 2.5;
  /** Streaming ms per frame across all budgets (controller state). */
  private total = 4;
  private interval = 20;
  private idle = 0;
  private gpu = 0;
  private frameAt = -Infinity;
  /** Budget windows opened since the last frame, and in the frame before: they share the total. */
  private windows = 0;
  private lastWindows = 1;
  /** The current 3x3 window still waits for full installs (coarseTiles). */
  urgent = false;
  /** The player moves at MOVING_SPEED or faster (coarseTiles). */
  moving = false;
  constructor(private now = () => performance.now()) {}
  /** `idleMs`: the main thread's idle time since the previous frame (MainThreadIdle); `gpuMs`: the latest GPU frame
   *  time (0 without timestamp queries: CPU-bound control only). */
  frame(_cpuMs: number, intervalMs: number | null, idleMs = 0, gpuMs = 0) {
    this.frameAt = this.now();
    const worked = this.windows > 0;
    this.lastWindows = Math.max(1, this.windows); this.windows = 0;
    if (intervalMs === null) return;
    this.interval += (intervalMs - this.interval) * 0.15;
    this.idle += (Math.min(idleMs, intervalMs) - this.idle) * 0.15;
    this.gpu += (gpuMs - this.gpu) * 0.15;
    // The GPU, not a missed vsync, holds the interval while the main thread idles: that idle is free.
    const gpuBound = this.idle >= StreamingAllowance.GPU_BOUND_IDLE_MS && this.gpu >= 0.8 * this.interval;
    if (intervalMs > this.target * 1.6 && !gpuBound) this.total *= 0.5;
    // Grow only while streaming has work: no wind-up during quiet periods.
    else if (gpuBound) { if (worked || this.idle < StreamingAllowance.IDLE_MARGIN_MS) this.total += 0.1 * (this.idle - StreamingAllowance.IDLE_MARGIN_MS); }
    else if (worked || this.interval > this.target) this.total += 0.1 * (this.target - this.interval);
    const t = this.tuning;
    const floor = this.pressed ? t.floorShare * Math.min(this.interval, t.floorInterval) : StreamingAllowance.MIN_MS;
    this.total = Math.min(this.pressed ? t.urgentCap : t.cap, Math.max(floor, this.total));
  }
  /** Streaming milliseconds per frame across all budgets: also the paced shader-build share while the window waits
   *  (main.ts paceYields), which the controller then counts in the frame interval it steers. */
  get share() { return this.now() - this.frameAt > 100 ? StreamingAllowance.BASE_MS : this.total; }
  /** A budget starts a window this frame. */
  opened() { this.windows++; }
  /** Changes with every reported frame: budgets that keep their own window open a new one when it changes. */
  get frameStamp() { return this.frameAt; }
  /** Diagnostics (window.__perf.allowance): the controller's state and what budgets and the yield pacer get now. */
  stats() {
    const r = (x: number) => Math.round(x * 10) / 10;
    return { urgent: this.urgent, moving: this.moving, total: r(this.total), limit: r(this.limit), share: r(this.share), windows: this.lastWindows,
      interval: r(this.interval), idle: r(this.idle), gpu: r(this.gpu) };
  }
  /** Milliseconds one streaming budget may work in this frame. */
  get limit() {
    if (this.loading()) return StreamingAllowance.LOADING_MS;
    // No frame reports (a page without the monitor, or a stalled loop): the old fixed window.
    if (this.now() - this.frameAt > 100) return StreamingAllowance.BASE_MS;
    const floor = this.pressed ? StreamingAllowance.BASE_MS : StreamingAllowance.MIN_MS;
    return Math.min(this.pressed ? this.tuning.urgentLimit : this.tuning.limit, Math.max(floor, this.total / this.lastWindows));
  }
}
export const streamingAllowance = new StreamingAllowance();
export const mainThreadIdle = new MainThreadIdle();

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

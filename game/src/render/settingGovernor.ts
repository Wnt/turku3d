/**
 * Setting-step governor for tiers below medium (QUALITY[tier].governor): when frames stay slower than the 40 FPS goal
 * while AdaptiveResolution already sits at its floor, it applies the next rendering-only step; after sustained fast
 * delivery it undoes the last one. Thermal throttling is the case it exists for (a fanless M1 Air loses 15-20 % FPS
 * after about 18 minutes), and a reduced-tier Windows laptop that still misses the goal. Steps never touch simulation.
 */
export interface GovernorStep { name: string; apply(): void; undo(): void }

export class SettingGovernor {
  /** Steps currently applied (0 = the tier as configured). */
  level = 0;
  readonly log: { t: number; level: number; step: string }[] = [];
  static readonly WINDOW_MS = 5000;
  /** Median interval above which a window is slow (35 FPS) or fast (46 FPS); hysteresis keeps the steps apart. */
  static readonly SLOW_MS = 1000 / 35;
  static readonly FAST_MS = 1000 / 46;
  /** Consecutive slow windows before a step down, fast windows before a step up, and the gap between changes. */
  static readonly DOWN_WINDOWS = 2;
  static readonly UP_WINDOWS = 6;
  static readonly MIN_GAP_MS = 10000;
  private intervals: number[] = [];
  private windowStart: number | null = null;
  private previous: number | null = null;
  private slow = 0;
  private fast = 0;
  private changedAt = -Infinity;

  constructor(private steps: readonly GovernorStep[]) {}

  /** Once per rendered frame. `atFloor`: AdaptiveResolution cannot lower resolution any further. */
  update(now: number, atFloor: boolean): GovernorStep | null {
    const elapsed = this.previous === null ? null : now - this.previous;
    this.previous = now;
    // Paused, hidden or loading: start over rather than read a gap as slow delivery.
    if (elapsed === null || elapsed > 250) { this.intervals.length = 0; this.windowStart = now; return null; }
    this.intervals.push(elapsed);
    if (now - (this.windowStart ?? now) < SettingGovernor.WINDOW_MS) return null;
    const sorted = this.intervals.sort((a, b) => a - b), median = sorted[Math.floor(sorted.length / 2)];
    this.intervals = []; this.windowStart = now;
    this.slow = median > SettingGovernor.SLOW_MS && atFloor ? this.slow + 1 : 0;
    this.fast = median < SettingGovernor.FAST_MS ? this.fast + 1 : 0;
    if (now - this.changedAt < SettingGovernor.MIN_GAP_MS) return null;
    if (this.slow >= SettingGovernor.DOWN_WINDOWS && this.level < this.steps.length) {
      const step = this.steps[this.level++]; step.apply();
      return this.changed(now, step);
    }
    if (this.fast >= SettingGovernor.UP_WINDOWS && this.level > 0) {
      const step = this.steps[--this.level]; step.undo();
      return this.changed(now, step);
    }
    return null;
  }

  private changed(now: number, step: GovernorStep) {
    this.changedAt = now; this.slow = 0; this.fast = 0;
    this.log.push({ t: Math.round(now), level: this.level, step: step.name });
    return step;
  }
}

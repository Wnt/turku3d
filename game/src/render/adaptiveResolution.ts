/** Frame-delivery feedback; capped mobile play also reserves headroom from measured CPU/GPU cost. */
export class AdaptiveResolution {
  scale = 1;
  private intervals: number[] = [];
  private previous: number | null = null;
  private changedAt = -Infinity;
  private healthySince: number | null = null;
  private costs: number[] = [];

  constructor(private targetFps = 60) {}

  update(now: number, workMs?: number): number {
    const elapsed = this.previous === null ? 0 : now - this.previous;
    this.previous = now;
    const target = 1000 / this.targetFps;
    if (elapsed < 4 || elapsed > Math.max(80, target * 4)) {
      this.intervals.length = 0; this.costs.length = 0; this.healthySince = null;
      return this.scale;
    }
    this.intervals.push(elapsed);
    if (workMs !== undefined && Number.isFinite(workMs) && workMs > 0) this.costs.push(workMs);
    if (this.intervals.length < 30 || now - this.changedAt < 1000) return this.scale;
    const sorted = this.intervals.sort((a, b) => a - b);
    const median = sorted[Math.floor(sorted.length / 2)];
    const missed = sorted.filter(ms => ms > target * 1.2).length / sorted.length;
    this.costs.sort((a, b) => a - b);
    const cost = this.costs.length ? this.costs[Math.floor(this.costs.length / 2)] : null;
    this.intervals = [];
    this.costs = [];
    if (median > target * 1.14 || missed > 0.2 || (cost !== null && cost > target * 0.7)) {
      this.scale = Math.max(0.5, this.scale - 0.1);
      this.changedAt = now;
      this.healthySince = null;
    } else if (median < target * 1.05 && missed < 0.05 && (cost === null || cost < target * 0.45)) {
      this.healthySince ??= now;
      // Vsync hides spare GPU headroom: cautiously probe upward after sustained healthy delivery.
      if (now - this.healthySince >= (this.targetFps === 30 ? 30_000 : 8000) && this.scale < 1) {
        this.scale = Math.min(1, this.scale + 0.05);
        this.changedAt = now;
        this.healthySince = null;
      }
    } else this.healthySince = null;
    return this.scale;
  }
}

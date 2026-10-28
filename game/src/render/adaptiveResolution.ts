/** Frame-interval feedback for a 60 Hz display. Isolated load/GC stalls do not reduce image quality. */
export class AdaptiveResolution {
  scale = 1;
  private intervals: number[] = [];
  private previous: number | null = null;
  private changedAt = -Infinity;
  private healthySince: number | null = null;

  update(now: number): number {
    const elapsed = this.previous === null ? 0 : now - this.previous;
    this.previous = now;
    if (elapsed < 4 || elapsed > 80) { this.intervals.length = 0; return this.scale; }
    this.intervals.push(elapsed);
    if (this.intervals.length < 30 || now - this.changedAt < 1000) return this.scale;
    const sorted = this.intervals.sort((a, b) => a - b);
    const median = sorted[Math.floor(sorted.length / 2)];
    const missed = sorted.filter(ms => ms > 20).length / sorted.length;
    this.intervals = [];
    if (median > 19 || missed > 0.2) {
      this.scale = Math.max(0.5, this.scale - 0.1);
      this.changedAt = now;
      this.healthySince = null;
    } else if (median < 17.5 && missed < 0.05) {
      this.healthySince ??= now;
      // Vsync hides spare GPU headroom: cautiously probe upward after sustained healthy delivery.
      if (now - this.healthySince >= 8000 && this.scale < 1) {
        this.scale = Math.min(1, this.scale + 0.05);
        this.changedAt = now;
        this.healthySince = null;
      }
    } else this.healthySince = null;
    return this.scale;
  }
}

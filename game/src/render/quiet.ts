/** Explicit readiness for capture tools: work the next frames still depend on, and frames drawn since none was
 *  outstanding. Tools wait for `quietFrames` instead of fixed startup and settling delays. */
export interface QuietSnapshot {
  frame: number;
  quietFrames: number;
  /** Graphics preparation jobs queued or running (models, styling, static batches, shadows). */
  preparing: number;
  /** Shader/pipeline builds asked for and not finished. */
  builds: number;
  /** Texture uploads queued. */
  uploads: number;
}

export class QuietFrames {
  private frame = 0;
  private busyAt = 0;
  constructor(private pending: () => { preparing: number; builds: number; uploads: number }) {}
  /** Once per drawn frame. */
  tick() {
    this.frame++;
    const p = this.pending();
    if (p.preparing || p.builds || p.uploads) this.busyAt = this.frame;
  }
  /** Restart the count after a change the counters cannot see (camera, time or weather set by a tool). */
  touch() { this.busyAt = this.frame; }
  snapshot(): QuietSnapshot { return { frame: this.frame, quietFrames: this.frame - this.busyAt, ...this.pending() }; }
}

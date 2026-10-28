/** Shader preparation shares a frame budget across streamed models and render passes. Individual draws are atomic. */
export class PreparationBudget {
  private started: number | null = null;
  private pending: Promise<void> | null = null;
  constructor(private now = () => performance.now(), private nextFrame = () => new Promise<void>(resolve => requestAnimationFrame(() => resolve()))) {}
  checkpoint(): Promise<void> | undefined {
    this.started ??= this.now();
    if (this.pending) return this.pending;
    if (this.now() - this.started < 2) return;
    this.pending = this.nextFrame().then(() => { this.started = this.now(); this.pending = null; });
    return this.pending;
  }
}

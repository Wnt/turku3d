/** Skip the entire play/render update between deadlines. Physics keeps its own fixed worker tick. */
export class FramePacer {
  private next = -Infinity;
  private last = -Infinity;
  constructor(readonly fps: number) {}

  ready(now: number): boolean {
    if (this.fps === 0) return true;
    const interval = 1000 / this.fps;
    if (now + 0.5 < this.next || now - this.last < interval * 0.8) return false;
    if (!Number.isFinite(this.next) || now - this.next >= interval) this.next = now + interval;
    else this.next += interval;
    this.last = now;
    return true;
  }
}

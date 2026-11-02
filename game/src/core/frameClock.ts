/** A scene clock may follow real frames or an explicit replay timeline. Loading and GPU submission never
 * advance a replay. Consumers use the same elapsed time, including water, weather and ambient populations. */
export class FrameClock {
  frames = 0;
  simSeconds = 0;
  private manual = false;
  private requested = 0;
  private lastWall = 0;
  constructor(public epochMs: number) {}
  get utcMs() { return this.epochMs + this.simSeconds * 1000; }
  reset(epochMs: number) {
    if (!Number.isFinite(epochMs)) throw Error('Invalid scene epoch');
    this.epochMs = epochMs; this.simSeconds = this.requested = 0; this.manual = true;
  }
  seek(seconds: number) {
    if (!Number.isFinite(seconds) || seconds < this.simSeconds - 1e-9) throw Error('Reset before rewinding a scene');
    this.manual = true; this.requested = seconds;
  }
  tick(wallMs: number) {
    const dt = this.manual ? Math.max(0, this.requested - this.simSeconds)
      : this.lastWall ? Math.min(.1, Math.max(0, (wallMs - this.lastWall) / 1000)) : 0;
    this.lastWall = wallMs; this.frames++;
    this.simSeconds = this.manual ? this.requested : this.simSeconds + dt;
    return dt;
  }
}

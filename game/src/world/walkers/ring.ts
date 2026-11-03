// Which shared walkers this client draws. The ring never moves a walker; it only picks the nearest ones within the
// tier's cap, preferring those in view, and fades the ones that would otherwise pop in or out in plain sight.

export const RING = {
  /** Draw radius in view; out of view walkers count as `inView / outOfView` times farther (40 m behind you). */
  inView: 100, outOfView: 40,
  /** Hysteresis: drawn walkers stay until this much past the radius, and win ties against newcomers. */
  keep: 10, incumbent: 6,
  /** Entering or leaving the drawn set closer than this, in view, fades instead of popping. */
  popFree: 55, fadeSeconds: 0.6,
  /** Extra instances for walkers fading out after the cap dropped them. */
  margin: 8,
};

export interface RingView { x: number; z: number; fx: number; fz: number; cosHalf: number }

export interface Placed { id: number; x: number; z: number }

/** True when (x, z) lies inside the camera's horizontal view cone (`cosHalf` includes a margin). */
export function inView(view: RingView, x: number, z: number): boolean {
  const dx = x - view.x, dz = z - view.z, d = Math.sqrt(dx * dx + dz * dz);
  return d < 2 || (dx * view.fx + dz * view.fz) / d >= view.cosHalf;
}

/** Ring distance: true distance in view, scaled up out of view. */
function ringScore(view: RingView, x: number, z: number): number {
  const dx = x - view.x, dz = z - view.z, d = Math.sqrt(dx * dx + dz * dz);
  return inView(view, x, z) ? d : d * RING.inView / RING.outOfView;
}

/** A drawn walker and this client's cosmetic state for it (ring fade, smoothed yaw, last animation phase/clip). */
export interface Drawn<T extends Placed> { walker: T; fade: number; yaw: number; phase: number; clip: number; first: boolean; lod?: 0 | 1 }
/** Frame stamps of a drawn entry: last frame it was a candidate (with that candidate) and was chosen. */
interface Entry<T extends Placed> extends Drawn<T> { leaving: boolean; seen: number; chosen: number; candidate: T | null }

export class Ring<T extends Placed> {
  private entries = new Map<number, Entry<T>>();
  private frame = 0;

  get size() { return this.entries.size; }
  /** This walker is drawn (or fading out). */
  has(id: number) { return this.entries.has(id); }

  /** Per-call scratch, reused (the ring runs every frame). */
  private scored: { walker: T; rank: number }[] = [];
  private out: Entry<T>[] = [];

  /** The walkers to draw this frame; `walkers` are every candidate whose position (and ground) is known now. */
  select(walkers: readonly T[], view: RingView, cap: number, dt: number): Drawn<T>[] {
    const scored = this.scored, frame = ++this.frame;
    let n = 0;
    for (let w = 0; w < walkers.length; w++) {
      const walker = walkers[w], score = ringScore(view, walker.x, walker.z), drawn = this.entries.get(walker.id);
      if (drawn) { drawn.seen = frame; drawn.candidate = walker; }
      if (score > RING.inView + (drawn && !drawn.leaving ? RING.keep : 0)) continue;
      const slot = scored[n] ??= { walker, rank: 0 };
      n++;
      slot.walker = walker; slot.rank = score - (drawn && !drawn.leaving ? RING.incumbent : 0);
    }
    scored.length = n;
    scored.sort((a, b) => a.rank - b.rank || a.walker.id - b.walker.id);
    for (let i = 0; i < Math.min(n, Math.max(0, cap)); i++) {
      const walker = scored[i].walker;
      const entry = this.entries.get(walker.id);
      if (entry) { entry.walker = walker; entry.leaving = false; entry.chosen = frame; continue; }
      const visible = inView(view, walker.x, walker.z) && (walker.x - view.x) ** 2 + (walker.z - view.z) ** 2 < RING.popFree * RING.popFree;
      this.entries.set(walker.id, { walker, fade: visible ? 0 : 1, yaw: 0, phase: 0, clip: -1, first: true, leaving: false,
        seen: frame, chosen: frame, candidate: walker });
    }
    this.entries.forEach((entry, id) => {
      if (entry.chosen === frame) return;
      const walker = entry.seen === frame ? entry.candidate : null;
      // Gone from the shared model (end of life, unknown route): its lifecycle fade already ran.
      if (!walker) { this.entries.delete(id); return; }
      const visible = inView(view, walker.x, walker.z) && (walker.x - view.x) ** 2 + (walker.z - view.z) ** 2 < RING.popFree * RING.popFree;
      if (!visible || entry.fade <= 0) { this.entries.delete(id); return; }
      entry.walker = walker; entry.leaving = true;
    });
    const step = dt / RING.fadeSeconds, out = this.out;
    out.length = 0;
    this.entries.forEach((entry, id) => {
      entry.fade = Math.min(1, Math.max(0, entry.fade + (entry.leaving ? -step : step)));
      if (entry.leaving && entry.fade <= 0) { this.entries.delete(id); return; }
      out.push(entry);
    });
    // Leaving walkers may exceed the cap briefly, never the instance margin.
    return out.length > cap + RING.margin ? out.sort((a, b) => Number(a.leaving) - Number(b.leaving)).slice(0, cap + RING.margin) : out;
  }

  clear() { this.entries.clear(); }
}

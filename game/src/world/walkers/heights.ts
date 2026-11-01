// Ground height along walker chains, sampled lazily with physics rays (local and cosmetic: every client samples the
// same collision tiles). Draft bridge decks carry surveyed Y in roads.json and need no rays.
import type { V3 } from '../../core/origin';
import type { RayFilter, RayHit } from '../../physics/protocol';
import type { Chain } from './network';

/** Metres between samples along a chain; walkers interpolate between them. */
export const HEIGHT_SPACING = 2;
/** A sample higher than both neighbours by this much is an overhead deck, re-cast from just above them. */
const OVERHEAD = 2.5;
const RETRY_MS = 1500;

type Raycast = (origin: V3, dir: V3, maxToi: number, filter: RayFilter) => Promise<RayHit | null>;
interface Samples { values: Float32Array; missing: number; retry: number; fixed: boolean }

export class GroundHeights {
  private chains = new Map<number, Samples>();
  private queue: { id: number; chain: Chain; index: number; top: number; depth: number }[] = [];
  private inFlight = 0;
  rays = 0;

  constructor(private raycast: Raycast) {}

  /** Ground Y at arc length s, or null until the chain's samples have arrived. */
  height(chain: Chain, s: number): number | null {
    if (chain.heights && !chain.heights.some(Number.isNaN)) return null; // deck Y comes with the walker
    const entry = this.chains.get(chain.id);
    if (!entry || entry.missing > 0 || !entry.fixed) return null;
    const v = entry.values, f = Math.min(Math.max(s / HEIGHT_SPACING, 0), v.length - 1), i = Math.min(Math.floor(f), v.length - 2);
    return v.length === 1 ? v[0] : v[i] + (v[i + 1] - v[i]) * (f - i);
  }

  /** Queue the chain's samples (first request wins; later calls are free). */
  request(chain: Chain, now: number) {
    if (chain.heights && !chain.heights.some(Number.isNaN)) return;
    let entry = this.chains.get(chain.id);
    if (entry && (entry.missing === 0 || now < entry.retry)) return;
    if (!entry) {
      const n = Math.max(2, Math.ceil(chain.length / HEIGHT_SPACING) + 1);
      entry = { values: new Float32Array(n).fill(NaN), missing: n, retry: 0, fixed: false };
      this.chains.set(chain.id, entry);
    }
    entry.retry = Infinity;
    entry.values.forEach((v, index) => { if (Number.isNaN(v)) this.queue.push({ id: chain.id, chain, index, top: 400, depth: 800 }); });
  }

  /** Issue at most `budget` rays this frame. */
  pump(budget: number, now: number) {
    while (budget-- > 0 && this.queue.length && this.inFlight < 64) {
      const job = this.queue.shift()!;
      const entry = this.chains.get(job.id);
      if (!entry) continue;
      const n = entry.values.length, s = Math.min(job.chain.length, job.index * HEIGHT_SPACING);
      const p = job.chain.points, c = job.chain.cumulative;
      let k = 0; while (k < c.length - 2 && c[k + 1] < s) k++;
      const span = c[k + 1] - c[k], t = span > 0 ? (s - c[k]) / span : 0;
      const x = p[2 * k] + (p[2 * k + 2] - p[2 * k]) * t, z = p[2 * k + 1] + (p[2 * k + 3] - p[2 * k + 1]) * t;
      this.inFlight++; this.rays++;
      void this.raycast({ x, y: job.top, z }, { x: 0, y: -1, z: 0 }, job.depth, 'foot').then(hit => {
        this.inFlight--;
        if (this.chains.get(job.id) !== entry) return;
        const overhead = job.depth < 10;
        // No collision yet: retry later. Nothing under a deck within reach: keep the neighbours' level.
        if (!hit && !overhead) { entry.retry = now + RETRY_MS; return; }
        if (Number.isNaN(entry.values[job.index])) entry.missing--;
        entry.values[job.index] = hit ? hit.point.y : job.top - 1.5;
        if (entry.missing === 0 && !entry.fixed) this.fixOverhead(job.id, job.chain, entry, n);
      }, () => { this.inFlight--; entry.retry = now + RETRY_MS; });
    }
  }

  /** Paths under bridges: a top-down ray lands on the deck. Re-cast those samples from just above their neighbours. */
  private fixOverhead(id: number, chain: Chain, entry: Samples, n: number) {
    const v = entry.values, redo: number[] = [];
    for (let i = 0; i < n; i++) {
      const low = Math.min(v[Math.max(0, i - 1)], v[Math.min(n - 1, i + 1)]);
      if (v[i] > low + OVERHEAD) redo.push(i);
    }
    entry.fixed = true;
    for (const i of redo) {
      const low = Math.min(v[Math.max(0, i - 1)], v[Math.min(n - 1, i + 1)]);
      entry.missing++; v[i] = NaN;
      this.queue.unshift({ id, chain, index: i, top: low + 1.5, depth: 4 });
    }
  }

  /** Forget chains that left the network. */
  retain(ids: ReadonlySet<number>) {
    for (const id of this.chains.keys()) if (!ids.has(id)) this.chains.delete(id);
    this.queue = this.queue.filter(job => ids.has(job.id));
  }

  get pending() { return this.queue.length + this.inFlight; }
}

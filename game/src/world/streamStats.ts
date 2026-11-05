/** Tile streaming latency for `window.__perf.stream` (main.ts), read by the CDP logger and checks: how long a tile
 *  takes from its first load request to being drawn (`ready`: a collision preview or a full visual) and to its full
 *  visual (`full`), as p50/p90 over the last WINDOW_MS, plus what is waiting right now. */

const WINDOW_MS = 30_000;
/** Samples kept per series; at most a few dozen tiles arrive in 30 s. */
const KEEP = 256;

type Sample = { at: number; ms: number };

const pct = (values: number[], p: number) => {
  if (!values.length) return null;
  const sorted = [...values].sort((a, b) => a - b);
  return Math.round(sorted[Math.min(sorted.length - 1, Math.max(0, Math.ceil(sorted.length * p) - 1))]);
};

export interface StreamSnapshot {
  /** Window tiles requested and not drawn yet. */
  pending: number;
  /** Full-visual installs in flight. */
  installing: number;
  /** Age (s) of the oldest pending window tile. */
  oldestPendingS: number;
  /** Request to first draw (ms) over the last 30 s. */
  readyMs: { n: number; p50: number | null; p90: number | null };
  /** Request to full visual (ms) over the last 30 s. */
  fullMs: { n: number; p50: number | null; p90: number | null };
  /** Tiles drawn and full visuals installed since load. */
  drawn: number; full: number;
}

export class StreamStats {
  private ready: Sample[] = [];
  private fulls: Sample[] = [];
  drawn = 0;
  full = 0;

  /** A tile requested at `since` was drawn for the first time at `now` (clock ms). */
  readyAt(since: number | undefined, now: number) {
    if (since === undefined) return;
    this.drawn++;
    this.push(this.ready, { at: now, ms: now - since });
  }

  /** A tile requested at `since` committed its full visual at `now`. */
  fullAt(since: number | undefined, now: number) {
    if (since === undefined) return;
    this.full++;
    this.push(this.fulls, { at: now, ms: now - since });
  }

  private push(series: Sample[], sample: Sample) {
    series.push(sample);
    if (series.length > KEEP) series.splice(0, series.length - KEEP);
  }

  snapshot(now: number, waiting: readonly number[], installing: number): StreamSnapshot {
    const recent = (series: Sample[]) => {
      const ms = series.filter(s => now - s.at <= WINDOW_MS).map(s => s.ms);
      return { n: ms.length, p50: pct(ms, 0.5), p90: pct(ms, 0.9) };
    };
    return { pending: waiting.length, installing,
      oldestPendingS: waiting.length ? Math.round((now - Math.min(...waiting)) / 100) / 10 : 0,
      readyMs: recent(this.ready), fullMs: recent(this.fulls), drawn: this.drawn, full: this.full };
  }
}

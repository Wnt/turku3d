import { DAY_MS, cycleAt, cycleHour, dayCycle, formatClock, localHour, type Segment } from '../core/dayCycle';
import type { Environment } from './environment';

/** `cycle`: the shared UTC clock (every client agrees; ?cycle= rescales it locally). `hour`: a frozen cycle hour
 *  (?time=HH:MM, the developer clock paused). `run`: the developer clock running from `hour` at local `since` (ms)
 *  at `rate` clock hours per real hour (1 = real time). `date`: a frozen calendar instant with the astronomical sun
 *  (QA poses, ?time=ISO). Every mode but the 30-minute cycle is a local view of this browser only. */
export type ClockMode = { kind: 'cycle'; periodMs: number } | { kind: 'hour'; hour: number }
  | { kind: 'run'; hour: number; since: number; rate: number } | { kind: 'date'; date: Date };

/** Clock hours per real hour of the shared cycle's 12-hour day segment (20 real minutes). */
const LIVE_RATE = 36;
/** The fastest IBL refresh of a fast clock or a scrub (ms); the sun and timecycle still follow every frame. */
const MIN_IBL_MS = 250, SCRUB_IBL_MS = 150;
/** How long `hourAt` remembers earlier modes: longer than a walker's life (world/walkers/population.ts). */
const HISTORY_MS = 5 * 60_000;
/** The developer clock's speeds: clock time per real time (×60 is a clock minute per second). */
export const CLOCK_SPEEDS = [1, 10, 60, 600] as const;

/** Sun direction and the timecycle follow the clock every frame. Sky IBL is reused for 30 s by day, regenerated
 *  each degree of sun movement around sunset/sunrise, and only every two minutes in deep night; a clock that runs
 *  faster than the shared cycle (the developer speeds, ?cycle=) shortens those intervals in proportion, and a
 *  scrub refreshes it at most every 150 ms. */
export class DayClock {
  time: Date;
  hour = 0;
  segment: Segment | null = null;
  /** Speed the developer clock resumes at (clock hours per real hour). */
  speed = 60;
  private serverOffset = 0;
  /** Started from the URL (?time=, QA): saves never move it. */
  private pinned: boolean;
  /** The mode "back to live" returns to: the shared cycle, with ?cycle='s period if one was given. */
  private readonly live: { kind: 'cycle'; periodMs: number };
  /** Earlier modes with the local instant each started, for `hourAt` (newest last). */
  private history: { from: number; mode: ClockMode }[];
  private iblPending = false;
  private lastIBL: number;
  private frames = 0;
  private totalMs = 0;
  private maxMs = 0;
  private iblUpdates = 0;
  private maxIblMs = 0;
  private label: HTMLElement | null = null;
  private badge: HTMLElement | null = null;
  private lastLabel = 0;
  private formatter = new Intl.DateTimeFormat('en-GB', { timeZone: 'Europe/Helsinki', dateStyle: 'short', timeStyle: 'short' });

  /** `now`: local wall-clock milliseconds (Date.now; tests drive their own). */
  constructor(private env: Environment, private mode: ClockMode = { kind: 'cycle', periodMs: DAY_MS }, showClock = true,
    private readonly now: () => number = Date.now) {
    this.pinned = mode.kind !== 'cycle';
    this.live = mode.kind === 'cycle' ? mode : { kind: 'cycle', periodMs: DAY_MS };
    this.history = [{ from: -Infinity, mode }];
    this.lastIBL = now();
    this.time = new Date(0);
    this.apply();
    if (showClock) {
      this.label = Object.assign(document.createElement('div'), { id: 'city-clock' });
      this.badge = Object.assign(document.createElement('span'), { id: 'local-time-badge', hidden: true });
      this.badge.title = 'Only this browser sees this time; other players and the shared world keep the live clock.';
      this.label.append(document.createElement('span'), this.badge);
      document.body.append(this.label);
    }
  }

  /** 0 by day, 1 at full night. */
  get night() { return this.env.nightFactor; }
  /** Elevation of the rendered sun, degrees. */
  get sunElevation() { return this.env.sunElevation; }
  /** The clock moves on its own: the shared cycle or a running developer clock. */
  get running() { return this.mode.kind === 'cycle' || this.mode.kind === 'run'; }
  /** The running developer clock's speed, or null (live cycle or frozen). */
  get rate() { return this.mode.kind === 'run' ? this.mode.rate : null; }
  /** This browser shows a time of its own, not the shared 30-minute cycle (developer clock, ?time=, ?cycle=, QA). */
  get local() { return this.mode.kind !== 'cycle' || this.mode.periodMs !== DAY_MS; }

  /** Server time aligns the shared cycle on every client; a frozen local view (?time=) ignores it. */
  synchronize(serverTime: number) { if (Number.isFinite(serverTime)) this.serverOffset = serverTime - this.now(); }
  /** UTC milliseconds on the shared (server-synchronised) clock, also while a QA or developer time is pinned. */
  sharedNow() { return this.now() + this.serverOffset; }

  /** The clock ambient traffic follows in single player: the cycle as shown, or the view's hour. A running developer
   *  clock is sent in steps of one real second's clock time (at least a minute), so a fast clock does not re-send
   *  every frame. */
  trafficClock(): { kind: 'cycle'; periodMs: number } | { kind: 'hour'; hour: number } {
    const mode = this.mode;
    if (mode.kind === 'cycle') return { kind: 'cycle', periodMs: mode.periodMs };
    if (mode.kind !== 'run') return { kind: 'hour', hour: this.hour };
    const step = Math.max(1 / 60, mode.rate / 3600);
    return { kind: 'hour', hour: Math.floor(this.hour / step + 1e-9) * step };
  }

  /** The view's clock hour at a shared-clock instant, also shortly in the past: each instant keeps the mode that was
   *  in effect then, so single-player walkers born before a time edit keep their life (world/walkers). */
  hourAt(sharedUtcMs: number): number {
    const local = sharedUtcMs - this.serverOffset;
    let entry = this.history[0];
    for (const h of this.history) if (h.from <= local) entry = h;
    return this.hourOf(entry.mode, local);
  }

  private hourOf(mode: ClockMode, local: number): number {
    switch (mode.kind) {
      case 'cycle': return cycleHour(local + this.serverOffset, mode.periodMs).hour;
      case 'hour': return ((mode.hour % 24) + 24) % 24;
      case 'run': { const h = mode.hour + (local - mode.since) * mode.rate / 3_600_000; return ((h % 24) + 24) % 24; }
      case 'date': return localHour(mode.date);
    }
  }

  /** Freeze a calendar instant with the astronomical sun (window.__render.setTime). */
  set(date: Date) { this.freeze({ kind: 'date', date }); }
  /** Freeze a clock hour on the cycle's sun path (window.__render.setHour). */
  setHour(hour: number) { this.freeze({ kind: 'hour', hour }); }
  /** Old saves never override the shared cycle or an explicit ?time= view; they only move a developer clock. */
  restore(date: Date) { if (this.mode.kind !== 'cycle' && !this.pinned) this.seek(localHour(date)); }

  // Developer time controls (ui/timePanel.ts): a local view, like ?time=. Sky, sun, shadows, timecycle, lit windows
  // and street lights follow the rendered clock; single-player traffic and walkers follow trafficClock/hourAt.
  /** Go to a clock hour; a running developer clock keeps running from there, anything else freezes. */
  seek(hour: number) {
    if (!Number.isFinite(hour)) return;
    const h = ((hour % 24) + 24) % 24;
    this.switchTo(this.mode.kind === 'run' ? { kind: 'run', hour: h, since: this.now(), rate: this.mode.rate } : { kind: 'hour', hour: h });
  }
  step(hours: number) { this.seek(this.hour + hours); }
  /** Freeze the view at its current hour. */
  pause() { this.switchTo({ kind: 'hour', hour: this.hour }); }
  /** Run from the current hour at `rate` clock hours per real hour (default: the last speed). */
  resume(rate = this.speed) {
    if (!(rate > 0)) return;
    this.speed = rate;
    this.switchTo({ kind: 'run', hour: this.hour, since: this.now(), rate });
  }
  /** Follow the shared cycle again. */
  goLive() { this.switchTo(this.live); }

  private switchTo(mode: ClockMode) {
    const now = this.now();
    this.mode = mode;
    this.history.push({ from: now, mode });
    // Keep the newest entry that started before the window, so every remembered instant has its mode.
    const cut = this.history.findLastIndex(h => h.from <= now - HISTORY_MS);
    if (cut > 0) this.history.splice(0, cut);
    this.apply();
    this.iblPending = true;
  }

  private freeze(mode: ClockMode) {
    this.switchTo(mode);
    this.refreshIBL(this.now());
  }

  private apply() {
    const mode = this.mode;
    if (mode.kind === 'date') {
      this.time = mode.date; this.hour = localHour(mode.date); this.segment = null;
      this.env.setTime(mode.date);
      return;
    }
    const state = mode.kind === 'cycle' ? dayCycle(this.sharedNow(), mode.periodMs)
      : { ...cycleAt(this.hourOf(mode, this.now())), segment: null };
    this.time = state.time; this.hour = state.hour; this.segment = state.segment;
    this.env.setSun(state.sun.azimuth, state.sun.elevation, state.hour);
  }

  /** How many times faster than the shared cycle the view runs (1 for the live clock or anything slower). */
  private speedup() {
    const mode = this.mode;
    return mode.kind === 'cycle' ? Math.max(1, DAY_MS / mode.periodMs) : mode.kind === 'run' ? Math.max(1, mode.rate / LIVE_RATE) : 1;
  }

  private refreshIBL(at: number) {
    const start = performance.now();
    this.env.updateIBL();
    this.maxIblMs = Math.max(this.maxIblMs, performance.now() - start);
    this.iblUpdates++; this.lastIBL = at; this.iblPending = false;
  }

  update() {
    const start = performance.now(), now = this.now();
    const since = now - this.lastIBL;
    if (this.running) {
      this.apply();
      const elevation = this.env.sunElevation, scale = 1 / this.speedup();
      const wait = (ms: number) => since >= Math.max(MIN_IBL_MS, ms * scale);
      const twilight = elevation > -12 && elevation < 10 && this.env.skyDrift() >= 1 && wait(5_000);
      const seeked = this.iblPending && since >= SCRUB_IBL_MS;
      if (twilight || seeked || wait(elevation <= -12 ? 120_000 : 30_000)) this.refreshIBL(now);
    } else if (this.iblPending && since >= SCRUB_IBL_MS) this.refreshIBL(now);
    if (this.label && start - this.lastLabel >= 250) {
      (this.label.firstChild as HTMLElement).textContent = this.formatter.format(this.time).replaceAll('/', '.');
      this.badge!.hidden = !this.local;
      if (this.local) this.badge!.textContent = this.badgeText();
      this.lastLabel = start;
    }
    const ms = performance.now() - start;
    this.frames++; this.totalMs += ms; this.maxMs = Math.max(this.maxMs, ms);
  }

  /** "Local time ×600 · live 14:05": what this browser shows, and the shared clock everyone else sees. */
  badgeText() {
    const mode = this.mode;
    const how = mode.kind === 'run' ? ` ×${mode.rate}` : mode.kind === 'cycle' ? ` (${Math.round(mode.periodMs / 60_000)} min cycle)` : ' · paused';
    return `Local time${how} · live ${formatClock(cycleHour(this.sharedNow()).hour)}`;
  }

  snapshot() { return { time: this.time.toISOString(), cycling: this.mode.kind === 'cycle', mode: this.mode.kind, local: this.local,
    rate: this.rate, hour: this.hour, segment: this.segment,
    sunElevation: this.env.sunElevation, night: this.night, shadowsFrozen: this.env.shadowsFrozen, frames: this.frames,
    meanMs: this.totalMs / Math.max(1, this.frames), maxMs: this.maxMs, iblUpdates: this.iblUpdates, maxIblMs: this.maxIblMs }; }
}

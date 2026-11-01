import { DAY_MS, cycleAt, dayCycle, localHour, type Segment } from '../core/dayCycle';
import type { Environment } from './environment';

/** `cycle`: the shared UTC clock (every client agrees). `hour`: a frozen cycle hour (?time=HH:MM, developer menu).
 *  `date`: a frozen calendar instant with the astronomical sun (QA poses, ?time=ISO). */
export type ClockMode = { kind: 'cycle'; periodMs: number } | { kind: 'hour'; hour: number } | { kind: 'date'; date: Date };

/** Sun direction and the timecycle follow the clock every frame. Sky IBL is reused for 30 s by day, regenerated
 *  each degree of sun movement around sunset/sunrise, and only every two minutes in deep night. */
export class DayClock {
  time: Date;
  hour = 0;
  segment: Segment | null = null;
  private serverOffset = 0;
  private pinned: boolean;
  private lastIBL = performance.now();
  private frames = 0;
  private totalMs = 0;
  private maxMs = 0;
  private iblUpdates = 0;
  private maxIblMs = 0;
  private label: HTMLElement | null = null;
  private lastLabel = 0;
  private formatter = new Intl.DateTimeFormat('en-GB', { timeZone: 'Europe/Helsinki', dateStyle: 'short', timeStyle: 'short' });

  constructor(private env: Environment, private mode: ClockMode = { kind: 'cycle', periodMs: DAY_MS }, showClock = true) {
    this.pinned = mode.kind !== 'cycle';
    this.time = new Date(0);
    this.apply();
    if (showClock) {
      this.label = Object.assign(document.createElement('div'), { id: 'city-clock' });
      document.body.append(this.label);
    }
  }

  /** 0 by day, 1 at full night. */
  get night() { return this.env.nightFactor; }

  /** Server time aligns the shared cycle on every client; a frozen local view (?time=) ignores it. */
  synchronize(serverTime: number) { if (Number.isFinite(serverTime)) this.serverOffset = serverTime - Date.now(); }
  /** UTC milliseconds on the shared (server-synchronised) clock, also while a QA or developer time is pinned. */
  sharedNow() { return Date.now() + this.serverOffset; }

  /** The clock ambient traffic follows in single player: the cycle as shown, or the frozen hour. */
  trafficClock(): { kind: 'cycle'; periodMs: number } | { kind: 'hour'; hour: number } {
    return this.mode.kind === 'cycle' ? { kind: 'cycle', periodMs: this.mode.periodMs } : { kind: 'hour', hour: this.hour };
  }

  /** Freeze a calendar instant with the astronomical sun (window.__render.setTime). */
  set(date: Date) { this.freeze({ kind: 'date', date }); }
  /** Freeze a clock hour on the cycle's sun path (developer time menu). */
  setHour(hour: number) { this.freeze({ kind: 'hour', hour }); }
  /** Old saves never override the shared cycle or an explicit ?time= view; they only move a developer-frozen clock. */
  restore(date: Date) { if (this.mode.kind !== 'cycle' && !this.pinned) this.setHour(localHour(date)); }

  private freeze(mode: ClockMode) {
    this.mode = mode;
    this.apply();
    this.env.updateIBL();
  }

  private apply() {
    const mode = this.mode;
    if (mode.kind === 'date') {
      this.time = mode.date; this.hour = localHour(mode.date); this.segment = null;
      this.env.setTime(mode.date);
      return;
    }
    const state = mode.kind === 'hour' ? { ...cycleAt(mode.hour), segment: null } : dayCycle(Date.now() + this.serverOffset, mode.periodMs);
    this.time = state.time; this.hour = state.hour; this.segment = state.segment;
    this.env.setSun(state.sun.azimuth, state.sun.elevation, state.hour);
  }

  update() {
    const start = performance.now();
    if (this.mode.kind === 'cycle') {
      this.apply();
      const elevation = this.env.sunElevation, since = start - this.lastIBL;
      const twilight = elevation > -12 && elevation < 10 && this.env.skyDrift() >= 1 && since >= 5_000;
      if (twilight || since >= (elevation <= -12 ? 120_000 : 30_000)) {
        const iblStart = performance.now();
        this.env.updateIBL();
        this.maxIblMs = Math.max(this.maxIblMs, performance.now() - iblStart);
        this.iblUpdates++; this.lastIBL = start;
      }
    }
    if (this.label && start - this.lastLabel >= 250) {
      this.label.textContent = this.formatter.format(this.time).replaceAll('/', '.');
      this.lastLabel = start;
    }
    const ms = performance.now() - start;
    this.frames++; this.totalMs += ms; this.maxMs = Math.max(this.maxMs, ms);
  }

  snapshot() { return { time: this.time.toISOString(), cycling: this.mode.kind === 'cycle', hour: this.hour, segment: this.segment,
    sunElevation: this.env.sunElevation, night: this.night, shadowsFrozen: this.env.shadowsFrozen, frames: this.frames,
    meanMs: this.totalMs / Math.max(1, this.frames), maxMs: this.maxMs, iblUpdates: this.iblUpdates, maxIblMs: this.maxIblMs }; }
}

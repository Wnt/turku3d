import { dayCycle } from '../core/dayCycle';
import type { Environment } from './environment';

/** Sun direction changes continuously. PMREM is reused for 30 s, and throughout the ten-second transition. */
export class DayClock {
  time: Date;
  private fixed: Date | null;
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

  constructor(private env: Environment, fixed: Date | null, showClock: boolean) {
    this.fixed = fixed; this.pinned = !!fixed;
    this.time = fixed ?? dayCycle(Date.now()).time;
    if (showClock) {
      this.label = Object.assign(document.createElement('div'), { id: 'city-clock' });
      document.body.append(this.label);
    }
  }

  set(date: Date) { this.fixed = date; this.time = date; this.env.sky.showSunDisc.value = 1; this.env.setTime(date); this.env.updateIBL(); }
  /** Old saves never override a wall-clock cycle or an explicit ?time= QA pose. */
  restore(date: Date) { if (this.fixed && !this.pinned) this.set(date); }

  update() {
    const start = performance.now();
    if (!this.fixed) {
      const cycle = dayCycle(Date.now());
      this.time = cycle.time;
      this.env.setTime(cycle.lightTime);
      this.env.sky.showSunDisc.value = cycle.sunDisc;
      if (!cycle.transition && start - this.lastIBL >= 30_000) {
        const iblStart = performance.now();
        this.env.updateIBL();
        this.maxIblMs = Math.max(this.maxIblMs, performance.now() - iblStart);
        this.iblUpdates++; this.lastIBL = start;
      }
    }
    if (this.label && start - this.lastLabel >= 250) {
      this.label.textContent = `${this.formatter.format(this.time).replaceAll('/', '.')} · Helsinki`;
      this.lastLabel = start;
    }
    const ms = performance.now() - start;
    this.frames++; this.totalMs += ms; this.maxMs = Math.max(this.maxMs, ms);
  }

  snapshot() { return { time: this.time.toISOString(), cycling: !this.fixed, frames: this.frames,
    meanMs: this.totalMs / Math.max(1, this.frames), maxMs: this.maxMs, iblUpdates: this.iblUpdates, maxIblMs: this.maxIblMs }; }
}

// Shared play clock: a pure function of UTC, so every client (and every multiplayer peer) sees the same time,
// sun, night factor and lit windows. The sun follows the clock on a fixed synthetic path, decoupled from the
// calendar, so a real night happens in every cycle.
import { nightFactor } from '../render/timecycle';
import { TURKU_LAT } from './sun';

export { nightFactor };

/** Phase origin: the start of the day segment. The displayed date is cosmetic. */
export const CYCLE_EPOCH = Date.UTC(2027, 8, 8, 4, 0);
export const DAY_MS = 30 * 60_000;

/** Real minutes per segment (20/4/4/2) and the clock hours each covers; hours run continuously past 24. */
export const SEGMENTS = [
  { name: 'day', minutes: 20, from: 7, to: 19 },
  { name: 'dusk', minutes: 4, from: 19, to: 21.5 },
  { name: 'night', minutes: 4, from: 21.5, to: 28.5 },
  { name: 'dawn', minutes: 2, from: 28.5, to: 31 },
] as const;
export type Segment = (typeof SEGMENTS)[number]['name'];
const NOMINAL_MINUTES = SEGMENTS.reduce((sum, s) => sum + s.minutes, 0);

/** Synthetic sun: fixed declination and solar noon; the night bottoms out near -23.5 degrees. */
const DECLINATION = 6, SOLAR_NOON = 13.25;
/** Local midnight (EEST, UTC+3) of the displayed date, 8 September 2027. */
const LOCAL_MIDNIGHT = Date.UTC(2027, 8, 7, 21);

export interface CycleState {
  /** Displayed clock (Europe/Helsinki). */
  time: Date;
  /** Clock hour of day, 0 <= hour < 24. */
  hour: number;
  sun: { azimuth: number; elevation: number };
  /** 0 by day, 1 at full night. */
  night: number;
}

/** Sun azimuth (degrees clockwise from true north) and elevation for a clock hour on the synthetic path. */
export function sunPath(hour: number): { azimuth: number; elevation: number } {
  const rad = Math.PI / 180;
  const h = (hour - SOLAR_NOON) * 15 * rad, phi = TURKU_LAT * rad, dec = DECLINATION * rad;
  const elevation = Math.asin(Math.sin(phi) * Math.sin(dec) + Math.cos(phi) * Math.cos(dec) * Math.cos(h)) / rad;
  const south = Math.atan2(Math.sin(h), Math.cos(h) * Math.sin(phi) - Math.tan(dec) * Math.cos(phi)) / rad;
  return { azimuth: ((south + 180) % 360 + 360) % 360, elevation };
}

/** The cycle's state at a fixed clock hour (the ?time=HH:MM view and the developer time menu). */
export function cycleAt(hour: number): CycleState {
  const h = ((hour % 24) + 24) % 24;
  const sun = sunPath(h);
  return { time: new Date(LOCAL_MIDNIGHT + h * 3_600_000), hour: h, sun, night: nightFactor(sun.elevation) };
}

/** The shared clock at a UTC instant; `periodMs` scales the whole cycle (?cycle=) for local viewing. */
export function dayCycle(utcMs: number, periodMs = DAY_MS): CycleState & { segment: Segment; phase: number } {
  const phase = ((utcMs - CYCLE_EPOCH) % periodMs + periodMs) % periodMs / periodMs;
  let minutes = phase * NOMINAL_MINUTES;
  for (const segment of SEGMENTS) {
    if (minutes < segment.minutes || segment === SEGMENTS[SEGMENTS.length - 1]) {
      const t = Math.min(1, minutes / segment.minutes);
      return { ...cycleAt(segment.from + (segment.to - segment.from) * t), segment: segment.name, phase };
    }
    minutes -= segment.minutes;
  }
  throw new Error('unreachable');
}

const helsinki = new Intl.DateTimeFormat('en-GB', { timeZone: 'Europe/Helsinki', hour: 'numeric', minute: 'numeric',
  second: 'numeric', hourCycle: 'h23' });
/** Local Helsinki clock hour of an instant (QA dates, saved clocks). */
export function localHour(date: Date): number {
  const part = (type: string) => Number(helsinki.formatToParts(date).find(p => p.type === type)?.value ?? 0);
  return part('hour') + part('minute') / 60 + (part('second') + date.getUTCMilliseconds() / 1000) / 3600;
}

export type DayPhase = 'dawn' | 'noon' | 'dusk' | 'night';
export const DAY_PHASES: readonly DayPhase[] = ['dawn', 'noon', 'dusk', 'night'];
/** Developer time menu: representative clock hours of the cycle (sunrise, high sun, sunset, deep night). */
export const PHASE_HOURS: Record<DayPhase, number> = { dawn: 6.5, noon: 13.25, dusk: 19.75, night: 23.5 };

/** `HH:MM` (or `H:MM`) as a clock hour, or null when the text is not a clock time. */
export function parseClockHour(text: string): number | null {
  const m = /^(\d{1,2}):(\d{2})$/.exec(text.trim());
  if (!m) return null;
  const hour = Number(m[1]), minute = Number(m[2]);
  return hour < 24 && minute < 60 ? hour + minute / 60 : null;
}

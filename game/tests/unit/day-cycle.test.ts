import { expect, test } from 'vitest';
import { CYCLE_EPOCH, DAY_MS, SEGMENTS, cycleAt, dayCycle, formatClock, hourOfPhase, localHour, parseClockHour, phaseOfHour, sunPath } from '../../src/core/dayCycle';

const local = (date: Date) => new Intl.DateTimeFormat('en-GB', { timeZone: 'Europe/Helsinki',
  day: '2-digit', month: '2-digit', year: 'numeric', hour: '2-digit', minute: '2-digit', hourCycle: 'h23' }).format(date);
const MINUTE = 60_000;

test('the cycle is 23 min day, 3.5 min dusk, 2 min night and 1.5 min dawn, with a continuous clock', () => {
  expect(SEGMENTS.map(s => s.minutes)).toEqual([23, 3.5, 2, 1.5]);
  expect(SEGMENTS.reduce((sum, s) => sum + s.minutes, 0) * MINUTE).toBe(DAY_MS);
  for (let i = 1; i < SEGMENTS.length; i++) expect(SEGMENTS[i].from).toBe(SEGMENTS[i - 1].to);
  expect(SEGMENTS[SEGMENTS.length - 1].to - 24).toBe(SEGMENTS[0].from);
  const at = (minutes: number) => dayCycle(CYCLE_EPOCH + minutes * MINUTE);
  expect(at(0).segment).toBe('day'); expect(local(at(0).time)).toBe('08/09/2027, 07:00');
  expect(at(23).segment).toBe('dusk'); expect(local(at(23).time)).toBe('08/09/2027, 19:00');
  expect(at(26.5).segment).toBe('night'); expect(local(at(26.5).time)).toBe('08/09/2027, 21:30');
  expect(at(28.5).segment).toBe('dawn'); expect(local(at(28.5).time)).toBe('08/09/2027, 04:30');
  // The clock never jumps: one real second never moves it more than the fastest segment's rate.
  const fastest = Math.max(...SEGMENTS.map(s => (s.to - s.from) / (s.minutes * 60)));
  let previous = at(0).hour;
  for (let s = 1; s <= 30 * 60; s++) {
    const hour = at(s / 60).hour;
    const step = ((hour - previous) % 24 + 24) % 24;
    expect(step).toBeGreaterThan(0); expect(step).toBeLessThan(fastest + 1e-9);
    previous = hour;
  }
});

test('the clock is a pure function of UTC: every client and every cycle agrees, including before the epoch', () => {
  for (const offset of [0, 7 * MINUTE + 123, 25 * MINUTE]) {
    const reference = dayCycle(CYCLE_EPOCH + offset);
    for (const period of [-123, -1, 1, 500]) {
      const later = dayCycle(CYCLE_EPOCH + offset + period * DAY_MS);
      expect(later.hour).toBeCloseTo(reference.hour, 9);
      expect(later.sun).toEqual(reference.sun);
      expect(later.night).toBe(reference.night);
    }
  }
  // A scaled local cycle keeps the proportions: 1/15 of a two-minute cycle is still midday.
  expect(dayCycle(CYCLE_EPOCH + 2 * MINUTE / 3, 2 * MINUTE).segment).toBe('day');
  expect(dayCycle(CYCLE_EPOCH + 1.8 * MINUTE, 2 * MINUTE).segment).toBe('night');
});

test('night is short: the scene reads dark for under a fifth of the cycle and daylight takes three quarters', () => {
  let dark = 0, daylight = 0;
  for (let s = 0; s < 30 * 60; s++) {
    const { night } = dayCycle(CYCLE_EPOCH + s * 1000);
    if (night >= 0.5) dark++;
    if (night === 0) daylight++;
  }
  expect(dark / 1800).toBeGreaterThan(0.12); expect(dark / 1800).toBeLessThan(0.2);
  expect(daylight / 1800).toBeGreaterThan(0.72);
});

test('phaseOfHour inverts the cycle\'s hour of each phase', () => {
  for (let p = 0; p < 1; p += 1 / 997) expect(phaseOfHour(hourOfPhase(p).hour)).toBeCloseTo(p, 9);
  for (let h = 0; h < 24; h += 0.37) expect(hourOfPhase(phaseOfHour(h)).hour).toBeCloseTo(h, 9);
  expect(phaseOfHour(7)).toBe(0); expect(phaseOfHour(19)).toBeCloseTo(23 / 30, 12);
});

test('the sun stays below -12 degrees for about two real minutes of every cycle', () => {
  let below = 0, lowest = 90;
  for (let s = 0; s < 30 * 60; s++) {
    const { sun } = dayCycle(CYCLE_EPOCH + s * 1000);
    if (sun.elevation < -12) below++;
    lowest = Math.min(lowest, sun.elevation);
  }
  expect(below).toBeGreaterThanOrEqual(2 * 60);
  expect(lowest).toBeLessThan(-20);
  // Full night factor throughout deep night; none at midday.
  expect(dayCycle(CYCLE_EPOCH + 27.5 * MINUTE).night).toBe(1);
  expect(dayCycle(CYCLE_EPOCH + 8 * MINUTE).night).toBe(0);
});

test('the synthetic sun rises in the east, culminates in the south and sets in the west', () => {
  const noon = sunPath(13.25);
  expect(noon.azimuth).toBeCloseTo(180, 6);
  expect(noon.elevation).toBeCloseTo(90 - 60.448 + 6, 6);
  expect(sunPath(7).azimuth).toBeGreaterThan(70); expect(sunPath(7).azimuth).toBeLessThan(110);
  expect(sunPath(19.5).azimuth).toBeGreaterThan(260); expect(sunPath(19.5).azimuth).toBeLessThan(300);
  expect(sunPath(13).elevation).toBeGreaterThan(30);
  expect(sunPath(19.5).elevation).toBeGreaterThan(0); expect(sunPath(19.5).elevation).toBeLessThan(6);
  expect(sunPath(22).elevation).toBeLessThan(-12);
  for (const hour of [4, 6.5, 13, 19.5, 20.5, 22]) expect(cycleAt(hour).hour).toBe(hour);
  expect(cycleAt(23.5).night).toBe(1);
  expect(cycleAt(13.25).night).toBe(0);
});

test('?time=HH:MM clock hours and the local hour of an instant', () => {
  expect(parseClockHour('22:00')).toBe(22);
  expect(parseClockHour('7:30')).toBe(7.5);
  expect(parseClockHour('24:00')).toBeNull();
  expect(parseClockHour('2026-10-15T14:00:00+03:00')).toBeNull();
  expect(localHour(new Date('2026-10-15T14:30:00+03:00'))).toBeCloseTo(14.5, 9);
  expect(localHour(new Date('2026-12-21T12:00:00+02:00'))).toBeCloseTo(12, 9);
  expect(localHour(cycleAt(19.5).time)).toBeCloseTo(19.5, 9);
  // formatClock is its inverse to the minute, and wraps.
  expect(formatClock(22)).toBe('22:00'); expect(formatClock(7.5)).toBe('07:30'); expect(formatClock(0)).toBe('00:00');
  expect(formatClock(24)).toBe('00:00'); expect(formatClock(23.9999)).toBe('00:00'); expect(formatClock(-0.5)).toBe('23:30');
  expect(formatClock(13 + 1 / 6 - 1e-9)).toBe('13:10');
  for (let m = 0; m < 1440; m += 7) expect(parseClockHour(formatClock(m / 60))).toBeCloseTo(m / 60, 9);
});

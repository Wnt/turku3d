import { expect, test } from 'vitest';
import table from '../../src/render/timecycle.json';
import { MESOPIC, linearRgb, litFraction, nightFactor, timecycle } from '../../src/render/timecycle';
import { WINDOW_HASH } from '../../src/render/nightLights';

test('timecycle rows interpolate linearly between elevation keys and clamp outside them', () => {
  table.elevation.forEach((elevation, i) => {
    const sample = timecycle(elevation);
    expect(sample.sunIntensity).toBeCloseTo(table.rows.sunIntensity[i], 12);
    expect(sample.exposure).toBeCloseTo(table.rows.exposure[i], 12);
    expect(sample.fogColor).toEqual(linearRgb(table.rows.fogColor[i]));
  });
  // Halfway between the 2 and 6 degree keys, in linear light for colours.
  const middle = timecycle(4), low = timecycle(2), high = timecycle(6);
  expect(middle.sunIntensity).toBeCloseTo((low.sunIntensity + high.sunIntensity) / 2, 12);
  expect(middle.night).toBeCloseTo((low.night + high.night) / 2, 12);
  for (let c = 0; c < 3; c++) expect(middle.sunColor[c]).toBeCloseTo((low.sunColor[c] + high.sunColor[c]) / 2, 12);
  expect(timecycle(-200)).toEqual(timecycle(-90));
  expect(timecycle(200)).toEqual(timecycle(90));
  expect(linearRgb('#ffffff')).toEqual([1, 1, 1]);
  expect(linearRgb('#000000')).toEqual([0, 0, 0]);
});

test('night: no sun light below the horizon, artificial ambient on; day uses neutral diffuse fill alongside sky reflections', () => {
  for (let e = -90; e <= -1; e += 0.5) {
    expect(timecycle(e).sunIntensity).toBe(0);
    expect(timecycle(e).hemisphereIntensity).toBeGreaterThan(0);
  }
  const deep = timecycle(-15);
  expect(deep.night).toBe(1);
  expect(deep.exposure).toBeGreaterThan(1);
  expect(deep.bloomStrength).toBeGreaterThan(0.25); expect(deep.bloomThreshold).toBeLessThan(1);
  // Reference-judged daylight: a stronger sun against neutral fill restores street-level contrast without lowering exposure.
  const afternoon = timecycle(30);
  expect(afternoon).toMatchObject({ sunIntensity: 4.0, environmentIntensity: 0.08, hemisphereIntensity: 1.0, exposure: 0.65,
    bloomStrength: 0.045, bloomThreshold: 1.5, night: 0, fogNear: 500, fogFar: 3500 });
  expect(afternoon.fogColor).toEqual(linearRgb('#b8cde0'));
  // The night factor only grows as the sun sinks.
  let previous = 0;
  for (let e = 90; e >= -90; e -= 0.25) { const n = nightFactor(e); expect(n).toBeGreaterThanOrEqual(previous); previous = n; }
});

test('the lit-window share follows the hour: 15 to 60 %, peaking early evening, thinnest before dawn', () => {
  const hours = Array.from({ length: 24 * 60 }, (_, m) => m / 60);
  const shares = hours.map(litFraction);
  expect(Math.min(...shares)).toBeCloseTo(0.15, 9);
  expect(Math.max(...shares)).toBeCloseTo(0.6, 9);
  expect(hours[shares.indexOf(Math.max(...shares))]).toBe(20);
  expect(hours[shares.indexOf(Math.min(...shares))]).toBe(4);
  expect(litFraction(22)).toBeGreaterThan(litFraction(2));
  expect(litFraction(24)).toBeCloseTo(litFraction(0), 12);
  expect(litFraction(-1)).toBeCloseTo(litFraction(23), 12);
});

// CPU mirror of nightLights.windowEmission's on/off decision (u32 PCG, same salts and schedule).
const pcg = (v: number) => {
  const state = (Math.imul(v >>> 0, 747796405) + 2891336453) >>> 0;
  const word = Math.imul(((state >>> ((state >>> 28) + 4)) ^ state) >>> 0, 277803737) >>> 0;
  return ((word >>> 22) ^ word) >>> 0;
};
const key = (values: number[], seed: number) => values.reduce((k, v) => pcg((k ^ Math.trunc(v)) >>> 0), seed >>> 0);
const unit = (k: number) => Math.fround(k) / 4294967296;
function lit(facade: number, column: number, storey: number, hour: number) {
  const h = WINDOW_HASH;
  const window = pcg((facade ^ key([column, storey], h.cell)) >>> 0);
  const epoch = Math.floor(hour / h.epochHours + unit(pcg((window ^ h.phase) >>> 0)));
  const roll = unit(pcg((window ^ epoch) >>> 0));
  const bias = (unit(pcg((pcg((facade ^ storey) >>> 0) ^ h.storey) >>> 0)) - 0.5) * h.storeyBias;
  return litFraction(hour) + bias >= roll;
}

test('per-window pattern: the lit share matches the hour, each window is stable, and the set changes overnight', () => {
  const facades = Array.from({ length: 400 }, (_, i) => key([i * 37, i * 11, 3 * 4096, 3.2 * 4096], WINDOW_HASH.seed));
  const cells = facades.flatMap(f => Array.from({ length: 40 }, (_, c) => [f, c % 8, Math.floor(c / 8)] as const));
  const share = (hour: number) => cells.filter(([f, c, s]) => lit(f, c, s, hour)).length / cells.length;
  for (const hour of [4, 13, 20, 22, 23.5]) expect(Math.abs(share(hour) - litFraction(hour)), `${hour}:00`).toBeLessThan(0.02);
  // Same inputs, same answer: every client derives the same window from the same tile data and shared clock.
  expect(cells.every(([f, c, s]) => lit(f, c, s, 22) === lit(f, c, s, 22))).toBe(true);
  // Over two hours of night many windows switch while the share stays on its curve.
  const switched = cells.filter(([f, c, s]) => lit(f, c, s, 22) !== lit(f, c, s, 24)).length / cells.length;
  expect(switched).toBeGreaterThan(0.2);
  // Within a few minutes almost every window keeps its state.
  const flicker = cells.filter(([f, c, s]) => lit(f, c, s, 22) !== lit(f, c, s, 22.05)).length / cells.length;
  expect(flicker).toBeLessThan(0.03);
});

test('the twilight sky is black by day, deep blue in the blue hour and dark at night; dim light greys at night', () => {
  for (const e of [2, 6, 30, 90]) { expect(timecycle(e).skyZenith).toEqual([0, 0, 0]); expect(timecycle(e).skyHorizon).toEqual([0, 0, 0]); }
  const blue = timecycle(-3.5).skyZenith, night = timecycle(-20).skyZenith;
  expect(blue[2]).toBeGreaterThan(blue[0] * 2); // blue
  expect(blue[2]).toBeGreaterThan(night[2] * 2); // brighter than the night zenith
  expect(timecycle(-20).skyHorizon[0]).toBeGreaterThan(night[0]); // the city-lit horizon
  expect(MESOPIC.saturation).toBeLessThan(0.6);
  expect(MESOPIC.luminance[0]).toBeLessThan(MESOPIC.luminance[1]);
});

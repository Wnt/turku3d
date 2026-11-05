import { expect, test } from 'vitest';
import { ChassisSynth, ChassisTracker, lugHz, surfaceClass, thumpGain, TROPHY_CHASSIS } from '../../src/audio/engine/chassis';
import { Drivetrain, upshiftRpm, type EngineDrive } from '../../src/audio/engine/drivetrain';
import { TruckSoundFeed } from '../../src/audio/engine/feed';
import { IDLE_RPM, ON_LOAD_DB, REFERENCE_RPM, createV8, render, type V8Params } from '../../src/audio/engine/v8core';
import { distanceCutoff, selectVoices } from '../../src/audio/engine/voices';
import { GameAudio } from '../../src/audio';
import { GameplayAudio } from '../../src/audio/gameplay';
import type { AudioEvent } from '../../src/audio/mix';
import type { WheelState } from '../../src/physics/protocol';
import { MAT } from '../../src/shared/col/materials';

// SPEC §7 S1-S4 and S7 run offline on the pure DSP (no Web Audio). Rendering is deterministic for the seed.
const SR = 48000;
const base: V8Params = { rpm: 930, load: 0, throttle: 0, limiter: false, shifting: false, running: true, cockpit: false, partials: 32 };

function synth(seconds: number, params: (t: number) => Partial<V8Params>, seed = 19) {
  const voice = createV8(seed, SR), out = new Float32Array(Math.round(seconds * SR)), block = new Float32Array(128);
  for (let i = 0; i < out.length; i += 128) {
    render(voice, { ...base, ...params(i / SR) }, block, SR);
    out.set(block.subarray(0, Math.min(128, out.length - i)), i);
  }
  return { out, voice };
}

/** Power spectrum (Hann, averaged over 75 %-overlapped frames) via an in-place radix-2 FFT. */
function psd(x: Float32Array, nfft = 16384) {
  const P = new Float64Array(nfft / 2 + 1), re = new Float64Array(nfft), im = new Float64Array(nfft);
  let frames = 0;
  for (let start = 0; start + nfft <= x.length; start += nfft / 4, frames++) {
    for (let i = 0; i < nfft; i++) { re[i] = x[start + i] * (0.5 - 0.5 * Math.cos(2 * Math.PI * i / (nfft - 1))); im[i] = 0; }
    for (let i = 1, j = 0; i < nfft; i++) {
      let bit = nfft >> 1; for (; j & bit; bit >>= 1) j ^= bit; j ^= bit;
      if (i < j) { [re[i], re[j]] = [re[j], re[i]]; [im[i], im[j]] = [im[j], im[i]]; }
    }
    for (let len = 2; len <= nfft; len <<= 1) {
      const a = -2 * Math.PI / len, wr = Math.cos(a), wi = Math.sin(a);
      for (let i = 0; i < nfft; i += len) {
        let cr = 1, ci = 0;
        for (let k = 0; k < len / 2; k++) {
          const ur = re[i + k], ui = im[i + k], vr = re[i + k + len / 2] * cr - im[i + k + len / 2] * ci, vi = re[i + k + len / 2] * ci + im[i + k + len / 2] * cr;
          re[i + k] = ur + vr; im[i + k] = ui + vi; re[i + k + len / 2] = ur - vr; im[i + k + len / 2] = ui - vi;
          const t = cr * wr - ci * wi; ci = cr * wi + ci * wr; cr = t;
        }
      }
    }
    for (let k = 0; k <= nfft / 2; k++) P[k] += re[k] * re[k] + im[k] * im[k];
  }
  return { P, df: SR / nfft, frames };
}
const db = (v: number) => 10 * Math.log10(v + 1e-20);
function bands(x: Float32Array) {
  const { P, df } = psd(x);
  let total = 0, weighted = 0;
  const edges = [[20, 100], [100, 300], [300, 1000], [1000, 3000], [3000, 8000]], sums = edges.map(() => 0);
  for (let k = 0; k < P.length; k++) {
    const f = k * df; if (f < 20 || f >= 8000) continue;
    total += P[k]; weighted += P[k] * f;
    edges.forEach(([lo, hi], i) => { if (f >= lo && f < hi) sums[i] += P[k]; });
  }
  const loudest = Math.max(...sums);
  return { shares: sums.map(s => db(s / total)), reLoudest: sums.map(s => db(s / loudest)), centroid: weighted / total };
}
function partials(x: Float32Array, rpm: number, count = 16) {
  const { P, df } = psd(x), fh = rpm / 120;
  const peak = (k: number) => { let m = 0; for (let b = Math.floor((k - 0.3) * fh / df); b <= Math.ceil((k + 0.3) * fh / df); b++) m = Math.max(m, P[b]); return db(m); };
  const levels = Array.from({ length: count }, (_, i) => peak(i + 1)), top = Math.max(...levels);
  return levels.map(l => l - top);
}

// Verified targets (ref/REFERENCE.md §5, SPEC Corrections C5): the V2 shop idle and the V1 film pull.
const IDLE_BANDS = [0, -4, -8.5, -16, -18], PULL_BANDS = [-1, 0, -1, -10.5, -16];

test('S1 idle 930 rpm: firing line 62 ± 1.5 Hz, the verified idle band balance, and a ≥ 3 dB lumpy-cam lope at 6-8 Hz', () => {
  const drive = new Drivetrain();
  let d: EngineDrive = drive.step({ dt: 1 / 60, throttle: 0, brake: 0, forwardSpeed: 0, drivenWheelSpeed: 0, contacts: 4, reverse: false, rearSteerMoving: false });
  const { out } = synth(5, t => { if (Math.floor(t * 60) !== Math.floor((t - 128 / SR) * 60)) d = drive.step({ dt: 1 / 60, throttle: 0, brake: 0,
    forwardSpeed: 0, drivenWheelSpeed: 0, contacts: 4, reverse: false, rearSteerMoving: false }); return { rpm: d.rpm, load: d.load }; });
  expect(IDLE_RPM).toBe(930);
  const { P, df } = psd(out, 32768);
  let best = 0, bestF = 0;
  for (let k = Math.floor(40 / df); k < 90 / df; k++) if (P[k] > best) { best = P[k]; bestF = k * df; }
  expect(Math.abs(bestF - 62)).toBeLessThan(1.5);
  const { reLoudest, centroid } = bands(out.subarray(SR));
  IDLE_BANDS.forEach((target, i) => expect(Math.abs(reLoudest[i] - target), `idle band ${i}`).toBeLessThanOrEqual(4));
  expect(centroid).toBeGreaterThanOrEqual(200); expect(centroid).toBeLessThanOrEqual(260);
  // Envelope in dB over 10 ms frames; the strongest 6-8 Hz component's peak-to-trough depth.
  const hop = 480, env: number[] = [];
  for (let i = 0; i + hop <= out.length; i += hop) { let s = 0; for (let j = 0; j < hop; j++) s += out[i + j] ** 2; env.push(db(s / hop)); }
  const mean = env.reduce((a, b) => a + b, 0) / env.length;
  let depth = 0;
  for (let f = 6; f <= 8.0001; f += 0.05) {
    let re = 0, im = 0;
    env.forEach((v, i) => { re += (v - mean) * Math.cos(2 * Math.PI * f * i * hop / SR); im += (v - mean) * Math.sin(2 * Math.PI * f * i * hop / SR); });
    depth = Math.max(depth, 4 * Math.hypot(re, im) / env.length);
  }
  expect(depth).toBeGreaterThanOrEqual(3);
});

test('S2 on load at 6,060 rpm: half-order comb rpm/120 ± 1 %, k5 and k8 strongest, verified band balance and centroid', () => {
  const rpm = REFERENCE_RPM, fh = rpm / 120;
  const { out } = synth(4, () => ({ rpm, load: 1, throttle: 1 }));
  const steady = out.subarray(SR, Math.round(3.5 * SR));
  const levels = partials(steady, rpm);
  const order = levels.map((l, i) => [l, i + 1]).sort((a, b) => b[0] - a[0]).slice(0, 2).map(x => x[1]).sort((a, b) => a - b);
  expect(order).toEqual([5, 8]);
  // Regression of the design table (SPEC C5: design values, not reference) for k ≤ 12.
  for (let k = 1; k <= 12; k++) expect(Math.abs(levels[k - 1] - ON_LOAD_DB[k - 1]), `k${k}`).toBeLessThanOrEqual(2.5);
  // Comb spacing: the half-order partial frequencies k·rpm/120 fit a line of slope rpm/120 ± 1 %.
  const { P, df } = psd(steady);
  const peaks = [1, 2, 3, 4, 5, 6, 7, 8, 9, 10].map(k => {
    let m = 0, at = 0; for (let b = Math.floor((k * fh - 15) / df); b <= (k * fh + 15) / df; b++) if (P[b] > m) { m = P[b]; at = b * df; } return at; });
  const spacing = (peaks[9] - peaks[0]) / 9;
  expect(Math.abs(spacing - fh) / fh).toBeLessThanOrEqual(0.01);
  const { reLoudest, centroid } = bands(steady);
  expect(centroid).toBeGreaterThanOrEqual(300); expect(centroid).toBeLessThanOrEqual(400);
  PULL_BANDS.forEach((target, i) => expect(Math.abs(reLoudest[i] - target), `band ${i}`).toBeLessThanOrEqual(4));
});

test('S3 no whine: a 1,000→7,000 rpm sweep has no tonal line in 1-8 kHz outside the engine orders', () => {
  const seconds = 6, { out } = synth(seconds, t => ({ rpm: 1000 + 6000 * t / seconds, load: 1, throttle: 1 }));
  const nfft = 4096, df = SR / nfft;
  // 0.25 s windows: ≈ 8 averaged 4096-point frames, so noise bins do not fake a line.
  const length = SR / 4;
  for (let start = SR / 2; start + length <= out.length; start += SR / 2) {
    const rpm = 1000 + 6000 * (start + length / 2) / SR / seconds, fh = rpm / 120;
    const { P } = psd(out.subarray(start, start + length), nfft);
    // Tonality against the local spectrum (±150 Hz median), so the exhaust's spectral tilt does not count as a line.
    // The synthesised half-order ladder itself (k ≤ 32) is skipped: only a foreign line (a blower or gear whine) counts.
    const lo = Math.ceil(1000 / df), hi = Math.floor(8000 / df), span = Math.round(150 / df);
    let worst = -Infinity;
    for (let k = lo; k < hi; k++) {
      const order = k * df / fh;
      if (order <= 32.5 && Math.abs(order - Math.round(order)) * fh < 3 * df) continue;
      const local: number[] = [];
      for (let j = Math.max(lo - span, k - span); j <= k + span; j++) local.push(db(P[j]));
      local.sort((a, b) => a - b);
      worst = Math.max(worst, db(P[k]) - local[local.length >> 1]);
    }
    expect(worst, `${Math.round(rpm)} rpm`).toBeLessThanOrEqual(10);
  }
});

/** A flat straight-line run of the truck (746 kW, traction-limited at 0.9 g, 2,350 kg) through the drivetrain. */
function course(throttleAt: (t: number) => number, seconds: number, air?: [number, number]) {
  const drive = new Drivetrain(), dt = 1 / 60, rows: (EngineDrive & { t: number; v: number })[] = [];
  let v = 0;
  for (let t = 0; t < seconds; t += dt) {
    const throttle = throttleAt(t), flying = !!air && t >= air[0] && t < air[1];
    if (!flying) v = Math.max(0, v + (throttle * Math.min(20700, 746000 / Math.max(v, 1)) - 0.84 * v * v - 600) / 2350 * dt);
    const d = drive.step({ dt, throttle, brake: 0, forwardSpeed: v, drivenWheelSpeed: flying ? v + 10 : v, contacts: flying ? 0 : 4,
      reverse: false, rearSteerMoving: false });
    rows.push({ ...d, t, v });
  }
  return rows;
}

test('S4 drivetrain: full-throttle shifts, ratios, no early limiter, airborne flare and lift-off fall', () => {
  const run = course(() => 1, 20);
  const shifts = run.flatMap((r, i) => (i && r.gear > run[i - 1].gear ? [{ at: i, from: run[i - 1].rpm }] : []));
  expect(shifts.map(s => run[s.at].gear)).toEqual([2, 3]);
  for (const s of shifts) { expect(s.from).toBeGreaterThanOrEqual(6500); expect(s.from).toBeLessThanOrEqual(6700); }
  // At the end of the 0.25 s shift the revs sit at the new gear's ratio of what the old gear would read at that
  // speed (the drop the ear hears, independent of how hard the truck accelerates during the shift).
  const gearbox = new Drivetrain();
  const ratio = (s: { at: number; from: number }) => {
    const end = run[s.at + 15], old = run[s.at - 1].gear;
    return end.rpm / (gearbox.coupledRpm(end.v, old) * 1.06);
  };
  expect(ratio(shifts[0])).toBeGreaterThanOrEqual(0.58); expect(ratio(shifts[0])).toBeLessThanOrEqual(0.64);
  expect(ratio(shifts[1])).toBeGreaterThanOrEqual(0.65); expect(ratio(shifts[1])).toBeLessThanOrEqual(0.71);
  expect(run.filter(r => r.limiter && r.rpm < 7000)).toEqual([]);
  expect(Math.max(...run.map(r => r.rpm))).toBeLessThanOrEqual(7000);
  // Airborne at full throttle: the revs flare at 3,500-5,500 rpm/s.
  const jump = course(t => (t < 2 ? 0.3 : 1), 4, [2, 2.6]), a = jump.findIndex(r => r.t >= 2.05), b = jump.findIndex(r => r.t >= 2.25);
  const flare = (jump[b].rpm - jump[a].rpm) / (jump[b].t - jump[a].t);
  expect(flare).toBeGreaterThanOrEqual(3500); expect(flare).toBeLessThanOrEqual(5500);
  // Lift-off at speed: 0.70-0.80 × within 0.2-0.6 s.
  const lift = course(t => (t < 4 ? 1 : 0), 5), at = lift.findIndex(r => r.t >= 4), before = lift[at - 1].rpm;
  const window = lift.filter(r => r.t >= 4.2 && r.t <= 4.6).map(r => r.rpm / before);
  expect(window.some(x => x >= 0.7 && x <= 0.8)).toBe(true);
  expect(Math.min(...window)).toBeGreaterThanOrEqual(0.68);
  expect(lift.at(-1)!.load).toBeLessThan(0);
});

test('drivetrain mapping: idle wander, steering-load dip, converter stall, part-throttle shift points and reverse', () => {
  const idle = (rearSteerMoving: boolean, seconds = 4) => {
    const d = new Drivetrain(); const rpms: number[] = [];
    for (let t = 0; t < seconds; t += 1 / 60) rpms.push(d.step({ dt: 1 / 60, throttle: 0, brake: 0, forwardSpeed: 0, drivenWheelSpeed: 0, contacts: 4, reverse: false, rearSteerMoving }).rpm);
    return rpms;
  };
  const still = idle(false), loaded = idle(true);
  expect(Math.min(...still)).toBeGreaterThanOrEqual(930 * 0.97 - 1); expect(Math.max(...still)).toBeLessThanOrEqual(930 * 1.03 + 1);
  expect(Math.max(...still) - Math.min(...still)).toBeGreaterThan(10);
  const mean = (a: number[]) => a.reduce((s, x) => s + x, 0) / a.length;
  expect(mean(still) - mean(loaded)).toBeGreaterThan(35);
  // Flat-footed at a standstill the converter stalls at 3,200 rpm.
  const stall = new Drivetrain(); let r = stall.step({ dt: 1 / 60, throttle: 1, brake: 1, forwardSpeed: 0, drivenWheelSpeed: 0, contacts: 4, reverse: false, rearSteerMoving: false });
  for (let i = 0; i < 60; i++) r = stall.step({ dt: 1 / 60, throttle: 1, brake: 1, forwardSpeed: 0, drivenWheelSpeed: 0, contacts: 4, reverse: false, rearSteerMoving: false });
  expect(r.rpm).toBeCloseTo(3200, -1); expect(r.load).toBe(1);
  expect(upshiftRpm(1)).toBe(6600); expect(upshiftRpm(0)).toBe(2800); expect(upshiftRpm(0.5)).toBe(4700);
  const part = course(() => 0.4, 25);
  const partShift = part.findIndex((x, i) => i && x.gear > part[i - 1].gear);
  expect(part[partShift - 1].rpm).toBeLessThan(4600);
  const back = new Drivetrain(), rev = back.step({ dt: 1 / 60, throttle: 0.5, brake: 0, forwardSpeed: -1, drivenWheelSpeed: -1, contacts: 4, reverse: true, rearSteerMoving: false });
  expect(rev.gear).toBe(-1);
});

test('voice events: lift-off crackle, limiter cut only at the limit, shift dip, starter catch and shutdown', () => {
  // Lift at 6,000 rpm: a crackle episode of 0.3-0.8 s; no crackle at the same lift from 2,500 rpm.
  const lift = (rpm: number) => { const v = createV8(3), b = new Float32Array(128); let crackled = 0;
    for (let i = 0; i < 375; i++) { render(v, { ...base, rpm, load: i < 100 ? 1 : -0.8, throttle: i < 100 ? 1 : 0 }, b); if (v.last.crackle) crackled++; }
    return crackled * 128 / SR; };
  const on = lift(6000); expect(on).toBeGreaterThanOrEqual(0.25); expect(on).toBeLessThanOrEqual(0.85);
  expect(lift(2500)).toBe(0);
  const cut = (limiter: boolean, rpm: number) => { const v = createV8(4), b = new Float32Array(128); let n = 0;
    for (let i = 0; i < 375; i++) { render(v, { ...base, rpm, load: 1, throttle: 1, limiter }, b); if (v.last.cutting) n++; } return n; };
  expect(cut(true, 7000)).toBeGreaterThan(50); expect(cut(true, 6500)).toBe(0); expect(cut(false, 7000)).toBe(0);
  const loud = (shifting: boolean) => { const { out } = synth(0.5, () => ({ rpm: 5000, load: 1, throttle: 1, shifting })); return out.subarray(SR / 4).reduce((s, x) => s + x * x, 0); };
  expect(db(loud(true) / loud(false))).toBeLessThan(-3);
  // Cold start: 0.7 s of starter, then the catch flares to ≈ 1,400 rpm and settles to idle.
  const cold = createV8(5, SR, { running: false }), b = new Float32Array(128), trace: { t: number; rpm: number; mode: string }[] = [];
  for (let i = 0; i < 1125; i++) { render(cold, { ...base, running: i > 0 }, b); trace.push({ t: i * 128 / SR, rpm: cold.last.rpm, mode: cold.last.mode }); }
  expect(trace.find(x => x.t > 0.3)!.mode).toBe('crank');
  expect(Math.max(...trace.filter(x => x.t < 1.2).map(x => x.rpm))).toBeGreaterThan(1300);
  expect(trace.at(-1)!.rpm).toBeLessThan(1000);
  for (let i = 0; i < 200; i++) render(cold, { ...base, running: false }, b);
  expect(cold.last.mode).toBe('off'); expect(b.every(x => x === 0)).toBe(true);
});

const wheel = (suspension: number, contact: boolean, material: number = MAT.road): WheelState => ({ pos: { x: 0, y: 0, z: 0 }, steer: 0, rotation: 0, contact, suspension,
  tyre: { point: { x: 0, y: -0.5, z: 0 }, normal: { x: 0, y: 1, z: 0 }, material, lateral: 0, longitudinal: 0 } });

test('S7 chassis: a 1.2 m drop thumps once per wheel, louder with compression speed; lug hum follows 36·v/(2π·0.533)', () => {
  const drop = (impact: number) => {
    const tracker = new ChassisTracker(), hits: { kind: string; wheel: number; gain: number }[] = [], dt = 1 / 60;
    let L = TROPHY_CHASSIS.staticLength + 0.3, contact = false, v = 0;
    for (let t = 0; t < 2; t += dt) {
      if (t > 0.5 && !contact) { contact = true; v = impact; }
      if (contact) { L -= v * dt; v = Math.max(-1, v - 30 * dt); if (L < TROPHY_CHASSIS.staticLength - 0.3) { L = TROPHY_CHASSIS.staticLength - 0.3; v = 0; } }
      hits.push(...tracker.step({ dt, wheels: [0, 1, 2, 3].map(() => wheel(L, contact)), speed: 0, rearSlew: 0 }).hits);
    }
    return hits.filter(h => h.kind === 'thump');
  };
  const fall = Math.sqrt(2 * 9.81 * 1.2), big = drop(fall), small = drop(2.5);
  expect(big.map(h => h.wheel).sort()).toEqual([0, 1, 2, 3]);
  expect(small).toHaveLength(4);
  expect(Math.min(...big.map(h => h.gain))).toBeGreaterThan(Math.max(...small.map(h => h.gain)));
  expect(thumpGain(1.4)).toBe(0); expect(thumpGain(6)).toBe(1); expect(thumpGain(9)).toBe(1);
  expect(lugHz(20)).toBeCloseTo(36 * 20 / (2 * Math.PI * 0.533), 6);
  expect(Math.abs(lugHz(20) - 215) / 215).toBeLessThan(0.03);
  // The rendered hum's strongest line in 100-400 Hz sits at the lug-pass frequency.
  const tracker = new ChassisTracker(), synthC = new ChassisSynth(1, SR), out = new Float32Array(SR * 2), block = new Float32Array(128);
  for (let i = 0; i < out.length; i += 128) {
    if (i % 800 === 0) synthC.frame(tracker.step({ dt: 1 / 60, wheels: [0, 1, 2, 3].map(() => wheel(TROPHY_CHASSIS.staticLength, true)), speed: 20, rearSlew: 0 }));
    block.fill(0); synthC.render(block); out.set(block, i);
  }
  const { P, df } = psd(out.subarray(SR / 2));
  let best = 0, at = 0; for (let k = Math.floor(100 / df); k < 400 / df; k++) if (P[k] > best) { best = P[k]; at = k * df; }
  expect(Math.abs(at - lugHz(20)) / lugHz(20)).toBeLessThan(0.03);
});

test('chassis mapping: surface classes, bump-stop knock, top-out clack, roost by slip and speed, rear-steer whine', () => {
  expect(surfaceClass(MAT.road)).toBe('asphalt'); expect(surfaceClass(MAT.cobblestone)).toBe('asphalt');
  expect(surfaceClass(MAT.gravel)).toBe('gravel'); expect(surfaceClass(MAT.grass)).toBe('soil'); expect(surfaceClass(MAT.water)).toBe('none');
  const t = new ChassisTracker(), L0 = TROPHY_CHASSIS.staticLength;
  t.step({ dt: 1 / 60, wheels: [wheel(L0, true)], speed: 0, rearSlew: 0 });
  const knock = t.step({ dt: 1 / 60, wheels: [wheel(L0 - 0.33, true)], speed: 0, rearSlew: 0 }).hits.map(h => h.kind);
  expect(knock).toContain('knock');
  const u = new ChassisTracker();
  u.step({ dt: 1 / 60, wheels: [wheel(L0 + 0.2, false)], speed: 0, rearSlew: 0 });
  expect(u.step({ dt: 1 / 60, wheels: [wheel(L0 + 0.29, false)], speed: 0, rearSlew: 0 }).hits.map(h => h.kind)).toEqual(['clack']);
  const roost = (speed: number, lateral: number) => new ChassisTracker().step({ dt: 1 / 60, speed, rearSlew: 0,
    wheels: [0, 1, 2, 3].map(() => ({ ...wheel(L0, true, MAT.gravel), tyre: { ...wheel(L0, true, MAT.gravel).tyre!, lateral } })) });
  expect(roost(10, 3).gravel).toBeGreaterThan(roost(10, 0).gravel);
  expect(roost(20, 0).gravel).toBeGreaterThan(roost(5, 0).gravel);
  expect(roost(10, 0).hum).toBe(0);
  const asphalt = (speed: number) => new ChassisTracker().step({ dt: 1 / 60, speed, rearSlew: 0, wheels: [0, 1, 2, 3].map(() => wheel(L0, true)) }).hum;
  expect(asphalt(5)).toBe(0); expect(asphalt(30)).toBe(1); expect(asphalt(15)).toBeCloseTo(0.4, 5);
  const whine = (slew: number) => new ChassisTracker().step({ dt: 1 / 60, speed: 0, rearSlew: slew, wheels: [] }).whine;
  expect(whine(0.05)).toBe(0); expect(whine(1)).toBeGreaterThan(0.5);
});

test('voice pool: the local truck always, then the nearest three within 120 m; the third remote drops to 16 partials', () => {
  const c = (id: string, distance: number, local = false, active = false) => ({ id, local, distance, active });
  const picked = selectVoices([c('far', 125), c('a', 30), c('me', 0, true), c('b', 10), c('c', 60), c('d', 90)]);
  expect([...picked.keys()].sort()).toEqual(['a', 'b', 'c', 'me']);
  expect(picked.get('me')).toBe(32); expect(picked.get('b')).toBe(32); expect(picked.get('a')).toBe(32); expect(picked.get('c')).toBe(16);
  // An active voice survives to 130 m; a new one starts only within 120 m.
  expect(selectVoices([c('x', 125, false, true)]).has('x')).toBe(true);
  expect(selectVoices([c('x', 125)]).has('x')).toBe(false);
  expect(distanceCutoff(5)).toBeCloseTo(8000); expect(distanceCutoff(100)).toBeCloseTo(2000); expect(distanceCutoff(55)).toBeCloseTo(4000);
});

test('feed: driven-wheel speed from rotation deltas (wrapped), synced drv smoothing and the wire record', () => {
  const feed = new TruckSoundFeed(1), wheels = (rotation: number, steer = 0) => [0, 1, 2, 3].map(i => ({ ...wheel(0.417, true), rotation, steer: i >= 2 ? steer : 0 }));
  let rot = 3.0, out = feed.step(1 / 60, { forwardSpeed: 10, speed: 10, wheels: wheels(rot) }, { throttle: 0.5 });
  for (let i = 0; i < 30; i++) { rot = ((rot + 10 / 0.533 / 60 + Math.PI) % (2 * Math.PI)) - Math.PI; out = feed.step(1 / 60, { forwardSpeed: 10, speed: 10, wheels: wheels(rot, i * 0.02) }, { throttle: 0.5 }); }
  expect(out.drivenWheelSpeed).toBeCloseTo(10, 3);
  expect(out.rearSlew).toBeGreaterThan(1); expect(out.drive.gear).toBe(1);
  const remote = new TruckSoundFeed(2);
  let r = remote.step(1 / 60, { forwardSpeed: 20, speed: 20, wheels: wheels(0) }, { drv: [100, 300, 2, 0] });
  expect(r.drive.rpm).toBe(3000);
  for (let i = 0; i < 6; i++) r = remote.step(1 / 60, { forwardSpeed: 20, speed: 20, wheels: wheels(0) }, { drv: [100, 600, 2, 0] });
  // 100 ms after the jump the 60 ms filter has covered ≈ 81 % of the step; it settles on the synced value.
  expect(r.drive.rpm).toBeGreaterThan(5300); expect(r.drive.rpm).toBeLessThan(5600);
  for (let i = 0; i < 30; i++) r = remote.step(1 / 60, { forwardSpeed: 20, speed: 20, wheels: wheels(0) }, { drv: [100, 600, 2, 0] });
  expect(r.drive.rpm).toBeCloseTo(6000, -1);
  expect(r.drive.load).toBe(1);
});

const wrapPi = (a: number) => a - 2 * Math.PI * Math.round(a / (2 * Math.PI));
test('feed: no aliasing when a frame spans many physics ticks (hitches, low fps, remote poses)', () => {
  // 30 m/s at frames of 4-14 physics ticks (67-233 ms): ω·Δt reaches 13 rad, far past ±π.
  const v = 30, r = 0.533, wheels = (rotation: number) => [0, 1, 2, 3].map(() => ({ ...wheel(r, true), rotation }));
  const feed = new TruckSoundFeed(1), coupled = new Drivetrain();
  let rot = 0.4, out = feed.step(1 / 60, { forwardSpeed: v, speed: v, wheels: wheels(rot) }, { throttle: 1 });
  const errors: number[] = [];
  for (let k = 0; k < 80; k++) {
    const ticks = 4 + (k * 7) % 11, dt = ticks / 60;
    rot = wrapPi(rot + v / r * dt * 1.02);
    out = feed.step(dt, { forwardSpeed: v, speed: v, wheels: wheels(rot) }, { throttle: 1 });
    if (k > 20) errors.push(Math.abs(out.drive.rpm / Math.min(7000, coupled.coupledRpm(v * 1.02, out.drive.gear) * 1.06) - 1));
    expect(Math.abs(out.drivenWheelSpeed / (v * 1.02) - 1)).toBeLessThan(1e-6);
  }
  expect(Math.max(...errors)).toBeLessThan(0.03);
});

test('gearing: the 44.6 m/s top speed in 3rd reads at most 6,700 rpm, off the limiter', () => {
  const gearbox = new Drivetrain();
  expect(gearbox.coupledRpm(44.6, 3) * 1.06).toBeLessThanOrEqual(6700);
});

test('GameAudio: truck frames pick voices without a worklet, and GameplayAudio feeds the local truck then releases it', async () => {
  const events: AudioEvent[] = [], adapter = new GameplayAudio(e => events.push(e));
  const truck = { id: 'truck', kind: 'truck' as const, pos: { x: 0, y: 1, z: 0 }, rot: { x: 0, y: 0, z: 0, w: 1 }, linvel: { x: 0, y: 0, z: 0 },
    speed: 0, forwardSpeed: 0, wheels: [0, 1, 2, 3].map(() => wheel(0.417, true)), curbStrike: null, impact: null };
  const frame = (tick: number, mode: 'foot' | 'car') => ({ tick, time: tick / 60, paused: false, mode, connected: false, character: null,
    vehicle: truck, input: { throttle: 0, brake: 0, steer: 0, handbrake: false }, position: truck.pos, forward: { x: 0, y: 0, z: -1 }, up: { x: 0, y: 1, z: 0 } });
  adapter.update(frame(1, 'car')); adapter.update(frame(1, 'car')); adapter.update(frame(2, 'car'));
  const voice = events.filter(e => e.type === 'engine-voice');
  expect(voice).toHaveLength(2);
  expect(voice[0].type === 'engine-voice' && voice[0].state?.local && Math.round(voice[0].state.drive.rpm / 100)).toBe(9);
  expect(adapter.truckDrive?.gear).toBe(1);
  // The truck's own loop event stays silent; the car event carries the drivetrain rpm.
  const car = events.filter(e => e.type === 'car').at(-1);
  expect(car?.type === 'car' && car.rpm).toBeGreaterThan(850);
  adapter.update(frame(3, 'foot'));
  expect(events.at(-1)).toEqual({ type: 'engine-voice', id: 'truck', state: null });
  expect(adapter.truckDrive).toBeNull();

  const audio = new GameAudio(() => ({ state: 'suspended', currentTime: 0, createGain: () => ({ connect() {}, gain: { value: 0, setTargetAtTime() {} } }),
    createDynamicsCompressor: () => ({ connect() {}, threshold: {}, ratio: {} }), destination: {}, resume: async () => {}, close: async () => {},
    decodeAudioData: async () => ({}), listener: {} } as unknown as AudioContext), (async () => ({ ok: false, status: 404 })) as unknown as typeof fetch);
  audio.emit({ type: 'listener', position: truck.pos, forward: { x: 0, y: 0, z: -1 }, up: { x: 0, y: 1, z: 0 }, paused: false });
  for (const e of voice) audio.emit(e);
  const remote = (id: string, x: number) => audio.engineVoice(id, { drive: { rpm: 3000, load: 1, throttle: 1, gear: 2, shifting: false, limiter: false },
    speed: 10, position: { x, y: 1, z: 0 }, local: false, wheels: truck.wheels, rearSlew: 0 });
  remote('r1', 50); remote('r2', 200);
  expect(audio.snapshot().engines.chosen).toEqual({ truck: 32, r1: 32 });
  audio.engineVoice('r1', null);
  expect(audio.snapshot().engines.candidates).toBe(2);
  audio.dispose();
});

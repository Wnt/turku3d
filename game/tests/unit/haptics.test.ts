import { test, expect } from 'vitest';
import {
  Haptics, bumpRumble, curbRumble, curbStrength, engineRumble, impactRumble, landingRumble, STAIR_STEP,
  CONTINUOUS_PULSE_MS, CONTINUOUS_REFRESH_MS, JOLT_FELT, SILENCE, STEP_FELT, SURFACE_ROUGHNESS, roughShare,
  surfaceRumble, type HapticFrame, type Rumble,
} from '../../src/input/haptics';
import { curbResponse } from '../../src/vehicle/curbStrike';
import { CAR, SCOOTER } from '../../src/physics/constants';
import { MAT } from '../../src/shared/col/materials';
import type { VehicleState, WheelState } from '../../src/physics/protocol';

const power = (r: Rumble | null) => r ? r.strong + r.weak : 0;
const car = (patch: Partial<VehicleState> = {}): VehicleState => ({
  kind: 'car', pos: { x: 0, y: 0, z: 0 }, rot: { x: 0, y: 0, z: 0, w: 1 }, linvel: { x: 0, y: 0, z: 0 }, speed: 0,
  forwardSpeed: 0, wheels: [], curbStrike: null, impact: null, bump: null, ...patch,
});
function rig() {
  const played: Rumble[] = [];
  const haptics = new Haptics(r => played.push(r));
  let now = 0;
  const frame = (patch: Partial<HapticFrame> = {}) => {
    now += 1000 / 60;
    haptics.update({ now, active: true, vehicle: null, vehicleId: null, input: null, character: null, ...patch });
  };
  return { haptics, played, frame };
}

test('a kerb step is felt like the curb-strike module at the same speed and height; drops weaker; texture not', () => {
  const kerb = bumpRumble({ height: 0.12, jolt: 0, speed: 4 })!;
  expect(kerb).toEqual(curbRumble(curbResponse(4, 0.12, CAR.mass).rumble));
  expect(curbStrength(4, -0.12)).toBeLessThan(curbStrength(4, 0.12));
  expect(bumpRumble({ height: STEP_FELT - 0.01, jolt: 0, speed: 10 })).toBeNull();
  expect(bumpRumble({ height: 0, jolt: JOLT_FELT - 0.05, speed: 10 })).toBeNull();
  // Faster over the same kerb is stronger, up to the motors' limit.
  expect(power(bumpRumble({ height: 0.12, jolt: 0, speed: 8 }))).toBeGreaterThan(power(kerb));
  expect(bumpRumble({ height: 0.15, jolt: 0, speed: 30 })!.strong).toBe(1);
});

test('jolts map by compression speed and the stronger of step and jolt plays', () => {
  const soft = bumpRumble({ height: 0, jolt: 1.2, speed: 10 })!, hard = bumpRumble({ height: 0, jolt: 3.5, speed: 10 })!;
  expect(hard.strong).toBeGreaterThan(soft.strong);
  expect(hard.duration).toBeGreaterThan(soft.duration);
  expect(bumpRumble({ height: 0, jolt: 10, speed: 10 })!.strong).toBeCloseTo(0.8);
  const slowKerb = bumpRumble({ height: 0.1, jolt: 3.5, speed: 1 })!;
  expect(slowKerb).toEqual(hard);
});

test('collisions map by speed change, so the same impulse shakes a scooter harder than a car', () => {
  expect(impactRumble(0.4 * CAR.mass, CAR.mass)).toBeNull();
  const tap = impactRumble(1 * CAR.mass, CAR.mass)!, crash = impactRumble(6 * CAR.mass, CAR.mass)!;
  expect(crash.strong).toBeGreaterThan(tap.strong);
  expect(crash.duration).toBeGreaterThan(tap.duration);
  expect(impactRumble(1000, SCOOTER.mass)!.strong).toBeGreaterThan(impactRumble(1000, CAR.mass)!.strong);
  expect(impactRumble(100 * CAR.mass, CAR.mass)).toEqual({ duration: 350, strong: 1, weak: 1 });
});

test('landings rumble by fall speed: a jump does, a step down does not', () => {
  expect(landingRumble(1.5)).toBeNull();
  const jump = landingRumble(4.6)!, fall = landingRumble(9)!;
  expect(jump.strong).toBeGreaterThan(0.15);
  expect(fall.strong).toBeGreaterThan(jump.strong);
});

test('engine: a car under throttle pulls hardest from rest, a ridden scooter always buzzes; weak motor only', () => {
  expect(engineRumble('car', 0, 5)).toBeNull();
  const launch = engineRumble('car', 1, 0)!, cruise = engineRumble('car', 1, 20)!;
  expect(launch.weak).toBeGreaterThan(cruise.weak);
  expect(launch.strong).toBe(0);
  expect(engineRumble('scooter', 0, 0)!.weak).toBeGreaterThan(0);
  expect(engineRumble('scooter', 1, 12)!.weak).toBeGreaterThan(engineRumble('scooter', 0, 0)!.weak);
  expect(launch.weak).toBeLessThan(0.2);
});

test('each vehicle event plays once; a vehicle entered with old events never replays them', () => {
  const { haptics, played, frame } = rig();
  const old = car({ curbStrike: { id: 7, wheel: 0, height: 0.12, speed: 4, loss: 0.02, blocked: false, rumble: 0.4 },
    impact: { id: 3, impulse: 9000 }, bump: { id: 9, wheel: 0, height: 0.12, jolt: 0, speed: 4 } });
  frame({ vehicle: old, vehicleId: 'player' });
  frame({ vehicle: old, vehicleId: 'player' });
  expect(played).toEqual([]);
  frame({ vehicle: { ...old, impact: { id: 4, impulse: 6000 } }, vehicleId: 'player' });
  expect(played).toEqual([impactRumble(6000, CAR.mass)]);
  for (let i = 0; i < 30; i++) frame({ vehicle: { ...old, impact: { id: 4, impulse: 6000 } }, vehicleId: 'player' });
  expect(played).toHaveLength(1);
  expect(haptics.counts.impact).toBe(1);
  expect(haptics.log.map(r => r.cause)).toEqual(['impact']);
});

test('a stronger transient preempts a running one, a weaker one is dropped, and a later one plays', () => {
  const { haptics, played, frame } = rig();
  const at = (bump: VehicleState['bump']) => car({ bump });
  frame({ vehicle: at(null), vehicleId: 'player' });
  frame({ vehicle: at({ id: 1, wheel: 0, height: 0, jolt: 1.5, speed: 8 }), vehicleId: 'player' });
  frame({ vehicle: at({ id: 2, wheel: 0, height: 0, jolt: 1.1, speed: 8 }), vehicleId: 'player' });
  frame({ vehicle: at({ id: 3, wheel: 0, height: 0.14, jolt: 0, speed: 8 }), vehicleId: 'player' });
  expect(played.map(r => r.strong)).toEqual([bumpRumble({ height: 0, jolt: 1.5, speed: 8 })!.strong,
    bumpRumble({ height: 0.14, jolt: 0, speed: 8 })!.strong]);
  for (let i = 0; i < 12; i++) frame({ vehicle: at({ id: 3, wheel: 0, height: 0.14, jolt: 0, speed: 8 }), vehicleId: 'player' });
  frame({ vehicle: at({ id: 4, wheel: 2, height: 0, jolt: 1.1, speed: 8 }), vehicleId: 'player' });
  expect(played).toHaveLength(3);
  expect(haptics.counts.bump).toBe(3);
});

test('the engine rumble renews in overlapping pulses, steps aside for transients and stops without throttle', () => {
  const { haptics, played, frame } = rig();
  const throttle = { throttle: 0.8, steer: 0, brake: 0, handbrake: false };
  for (let i = 0; i < 60; i++) frame({ vehicle: car({ forwardSpeed: 3 }), vehicleId: 'player', input: throttle });
  const pulses = played.length;
  expect(pulses).toBeGreaterThanOrEqual(Math.floor(1000 / CONTINUOUS_REFRESH_MS) - 1);
  expect(pulses).toBeLessThanOrEqual(Math.ceil(1000 / CONTINUOUS_REFRESH_MS) + 1);
  expect(played.every(r => r.duration === CONTINUOUS_PULSE_MS && r.strong === 0 && r.weak > 0)).toBe(true);
  expect(CONTINUOUS_PULSE_MS).toBeGreaterThan(2 * CONTINUOUS_REFRESH_MS);
  frame({ vehicle: car({ forwardSpeed: 3, bump: { id: 1, wheel: 0, height: 0.12, jolt: 0, speed: 3 } }), vehicleId: 'player', input: throttle });
  const kerb = played.at(-1)!;
  expect(kerb.strong).toBeGreaterThan(0);
  expect(kerb.weak).toBeGreaterThanOrEqual(engineRumble('car', 0.8, 3)!.weak);
  const during = played.length;
  for (let i = 0; i < 5; i++) frame({ vehicle: car({ forwardSpeed: 3 }), vehicleId: 'player', input: throttle });
  expect(played).toHaveLength(during);
  const before = played.length;
  for (let i = 0; i < 30; i++) frame({ vehicle: car({ forwardSpeed: 3 }), vehicleId: 'player', input: { ...throttle, throttle: 0 } });
  expect(played).toHaveLength(before);
  expect(haptics.counts.engine).toBe(pulses + (before - during));
  // Lifting off while a pulse still plays cuts it with one zero effect; then nothing more is sent.
  for (let i = 0; i < 12; i++) frame({ vehicle: car({ forwardSpeed: 3 }), vehicleId: 'player', input: throttle });
  const lifted = played.length;
  for (let i = 0; i < 30; i++) frame({ vehicle: car({ forwardSpeed: 3 }), vehicleId: 'player', input: { ...throttle, throttle: 0 } });
  expect(played.slice(lifted)).toEqual([SILENCE]);
});

test('on foot: a landing rumbles by the fall speed before touchdown; stair footsteps tick, other footsteps do not', () => {
  const { haptics, played, frame } = rig();
  frame({ character: { grounded: true, vy: -0.5 } });
  for (const vy of [-1, -3, -4.6]) frame({ character: { grounded: false, vy } });
  frame({ character: { grounded: true, vy: -0.5 } });
  expect(played).toEqual([landingRumble(4.6)]);
  frame({ character: { grounded: false, vy: -1 } });
  frame({ character: { grounded: true, vy: -0.5 } });
  expect(played).toHaveLength(1);
  for (let i = 0; i < 20; i++) frame({ character: { grounded: true, vy: -0.5 } });
  haptics.footstep(MAT.paving_slab); frame({ character: { grounded: true, vy: -0.5 } });
  haptics.footstep(MAT.stair_slope); frame({ character: { grounded: true, vy: -0.5 } });
  for (let i = 0; i < 5; i++) frame({ character: { grounded: true, vy: -0.5 } });
  haptics.footstep(MAT.stair_tread_helper); frame({ character: { grounded: true, vy: -0.5 } });
  expect(played.slice(1)).toEqual([STAIR_STEP, STAIR_STEP]);
  expect(haptics.counts).toMatchObject({ landing: 1, stairs: 2 });
});

test('paused, menu and start-screen frames rumble nothing and never replay what happened meanwhile', () => {
  const { played, frame } = rig();
  frame({ vehicle: car(), vehicleId: 'player' });
  frame({ active: false, vehicle: car({ impact: { id: 1, impulse: 9000 } }), vehicleId: 'player', input: { throttle: 1, steer: 0, brake: 0, handbrake: false } });
  frame({ vehicle: car({ impact: { id: 1, impulse: 9000 } }), vehicleId: 'player' });
  expect(played).toEqual([]);
  frame({ active: false, character: { grounded: false, vy: -8 } });
  frame({ character: { grounded: true, vy: -0.5 } });
  expect(played).toEqual([]);
});

const wheel = (material: number | null, contact = true): WheelState => ({
  pos: { x: 0, y: 0, z: 0 }, steer: 0, rotation: 0, contact, suspension: 0.3,
  tyre: material === null ? undefined : { point: { x: 0, y: 0, z: 0 }, normal: { x: 0, y: 1, z: 0 }, material, lateral: 0, longitudinal: 0 } });
const cobbles = (n = 4, of = 4) => Array.from({ length: of }, (_, i) => wheel(i < n ? MAT.cobblestone : MAT.road));
const kmh = (v: number) => v / 3.6;

test('surface: cobbles rumble above 5 km/h, rise to full at 50 km/h and scale with the wheels on them', () => {
  expect(SURFACE_ROUGHNESS).toEqual({ [MAT.cobblestone]: 1 });
  expect(roughShare(cobbles())).toBe(1);
  expect(roughShare(cobbles(1))).toBe(0.25);
  expect(roughShare(cobbles(1, 2))).toBe(0.5);
  expect(roughShare([wheel(MAT.cobblestone, false), wheel(null), wheel(MAT.paving_slab), wheel(MAT.quay)])).toBe(0);
  expect(roughShare([])).toBe(0);
  expect(surfaceRumble(kmh(5), 1)).toBeNull();
  expect(surfaceRumble(kmh(30), 0)).toBeNull();
  const slow = surfaceRumble(kmh(6), 1)!, mid = surfaceRumble(kmh(27.5), 1)!, full = surfaceRumble(kmh(50), 1)!;
  expect(slow.weak).toBeGreaterThan(0.1);
  expect(mid.weak).toBeGreaterThan(slow.weak);
  expect(mid.weak).toBeCloseTo((0.15 + 1) / 2);
  expect(full).toEqual({ strong: 0.55, weak: 1 });
  expect(surfaceRumble(kmh(120), 1)).toEqual(full);
  expect(surfaceRumble(-kmh(50), 1)).toEqual(full);
  // The buzz of the high-frequency motor leads at every speed; one wheel of four is a quarter as strong.
  for (const r of [slow, mid, full]) expect(r.weak).toBeGreaterThan(r.strong);
  expect(surfaceRumble(kmh(50), 0.25)!.weak).toBeCloseTo(0.25);
});

test('the surface rumble plays without gaps, lets transients through on top and stops at once', () => {
  const { haptics, played, frame } = rig();
  const idle = { throttle: 0, steer: 0, brake: 0, handbrake: false };
  const on = (v: number, patch: Partial<VehicleState> = {}, wheels = cobbles()) =>
    frame({ vehicle: car({ speed: kmh(v), forwardSpeed: kmh(v), wheels, ...patch }), vehicleId: 'player', input: idle });
  // Covered time: every effect replaces the one before, so the motors run until the latest effect's end.
  let t = 0, end = -Infinity, gaps = 0;
  const step = (run: () => void) => {
    const before = played.length;
    run(); t += 1000 / 60;
    if (played.length > before) end = played.at(-1)!.strong + played.at(-1)!.weak > 0 ? t + played.at(-1)!.duration : -Infinity;
    else if (t >= end) gaps++;
  };
  step(() => on(3)); expect(played).toEqual([]); expect(haptics.surface).toBeNull();
  gaps = 0;
  for (let i = 0; i < 120; i++) step(() => on(30));
  expect(haptics.surface).toMatchObject({ share: 1, ...surfaceRumble(kmh(30), 1) });
  expect(haptics.continuous).toEqual(surfaceRumble(kmh(30), 1));
  // A kerb at full speed: the strong motor rises above the cobbles' body, the buzz stays, and the rumble resumes.
  for (let i = 0; i < 30; i++) step(() => on(50));
  step(() => on(50, { bump: { id: 1, wheel: 0, height: 0.15, jolt: 0, speed: 8 } }));
  const kerb = played.at(-1)!;
  expect(kerb.strong).toBeGreaterThan(0.55);
  expect(kerb.weak).toBe(1);
  expect(haptics.log.at(-1)!.cause).toBe('bump');
  for (let i = 0; i < 30; i++) step(() => on(50, { bump: { id: 1, wheel: 0, height: 0.15, jolt: 0, speed: 8 } }));
  expect(played.at(-1)).toEqual({ duration: CONTINUOUS_PULSE_MS, strong: 0.55, weak: 1 });
  // A crash is stronger still on the low-frequency motor.
  step(() => on(50, { bump: { id: 1, wheel: 0, height: 0.15, jolt: 0, speed: 8 }, impact: { id: 1, impulse: 8 * CAR.mass } }));
  expect(played.at(-1)).toEqual({ duration: 350, strong: 1, weak: 1 });
  const crashed = { bump: { id: 1, wheel: 0, height: 0.15, jolt: 0, speed: 8 }, impact: { id: 1, impulse: 8 * CAR.mass } };
  for (let i = 0; i < 40; i++) step(() => on(50, crashed));
  // The engine joins per motor: the louder of the two wins, here the cobbles.
  step(() => frame({ vehicle: car({ speed: kmh(50), forwardSpeed: kmh(50), wheels: cobbles(), ...crashed }), vehicleId: 'player', input: { ...idle, throttle: 1 } }));
  expect(gaps).toBe(0);
  expect(haptics.counts.surface).toBeGreaterThan(15);
  // One wheel in a cobbled gutter is weaker; leaving the cobbles cuts the pulse at once.
  for (let i = 0; i < 12; i++) on(50, crashed, cobbles(1));
  expect(played.at(-1)!.weak).toBeCloseTo(0.25);
  const cut = (run: () => void) => { for (let i = 0; i < 12; i++) on(50, crashed); const n = played.length; run(); expect(played.slice(n)).toEqual([SILENCE]); };
  cut(() => on(50, crashed, cobbles(0)));
  cut(() => on(4, crashed));
  cut(() => frame({ character: { grounded: true, vy: 0 } }));
  cut(() => frame({ active: false, vehicle: car({ speed: kmh(50), wheels: cobbles(), ...crashed }), vehicleId: 'player' }));
  const n = played.length;
  for (let i = 0; i < 20; i++) frame({ active: false, vehicle: car({ speed: kmh(50), wheels: cobbles(), ...crashed }), vehicleId: 'player' });
  expect(played).toHaveLength(n);
  expect(haptics.surface).toBeNull();
});

test('slow frames: the surface rumble still overlaps at 8 frames per second', () => {
  const played: Rumble[] = [];
  const haptics = new Haptics(r => played.push(r));
  let end = 0;
  for (let now = 0; now < 3000; now += 125) {
    if (now > 0) expect(end).toBeGreaterThanOrEqual(now);
    const before = played.length;
    haptics.update({ now, active: true, vehicle: car({ speed: 10, wheels: cobbles() }), vehicleId: 'player', input: null, character: null });
    if (played.length > before) end = now + played.at(-1)!.duration;
  }
});

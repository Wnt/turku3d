import { test, expect } from 'vitest';
import {
  Haptics, bumpRumble, curbRumble, curbStrength, engineRumble, impactRumble, landingRumble, STAIR_STEP,
  ENGINE_PULSE_MS, ENGINE_REFRESH_MS, JOLT_FELT, STEP_FELT, type HapticFrame, type Rumble,
} from '../../src/input/haptics';
import { curbResponse } from '../../src/vehicle/curbStrike';
import { CAR, SCOOTER } from '../../src/physics/constants';
import { MAT } from '../../src/shared/col/materials';
import type { VehicleState } from '../../src/physics/protocol';

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
  expect(pulses).toBeGreaterThanOrEqual(Math.floor(1000 / ENGINE_REFRESH_MS) - 1);
  expect(pulses).toBeLessThanOrEqual(Math.ceil(1000 / ENGINE_REFRESH_MS) + 1);
  expect(played.every(r => r.duration === ENGINE_PULSE_MS && r.strong === 0 && r.weak > 0)).toBe(true);
  expect(ENGINE_PULSE_MS).toBeGreaterThan(ENGINE_REFRESH_MS);
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

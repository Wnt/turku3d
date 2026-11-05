import { describe, expect, test } from 'vitest';
import { CAR, SCOOTER, TRUCK } from '../../src/physics/constants';
import { handling } from '../../src/vehicle/handling';
import { AUTO50_SHARE, effectiveWheelbase, nextRearMode, passiveRatio, REAR_STEER_CYCLE, REAR_STEER_MODES, rearSteerIdle, rearSteerStep,
  type RearSteerState } from '../../src/vehicle/rearSteer';
import { RearSteerControl } from '../../src/vehicle/rearSteerControl';
import { ackermann, ackermann4 } from '../../src/vehicle/wheelKinematics';
import { corners, damping, droopLength, schedule, springForce, staticLength } from '../../src/vehicle/suspension';
import { decodeDrv, drvOf, encodeDrv, validDrv } from '../../src/net/drv';
import { shouldSendPose, type NetPose } from '../../src/net/client';
import { vehicleFingerprint } from '../../src/net/sharedVehicles';
import { ACTIONS, DEFAULT_BINDINGS, mapActions, promptLabel, type Action } from '../../src/input/bindings';
import { heaveFollow } from '../../src/vehicle/chaseCamera';
import { HINT_CHANGE_MS, HINT_ENTER_MS, RearSteerHintTimer, rearSteerHint, rearSteerTag } from '../../src/ui/rearSteerTag';
import type { VehicleState } from '../../src/physics/protocol';

const RS = TRUCK.rearSteer, D = Math.PI / 180;
const xf = TRUCK.wheels[0].x, xr = TRUCK.wheels[2].x, track = TRUCK.wheels[1].z - TRUCK.wheels[0].z, L = xf - xr;
/** Wheel angles (FL, FR, RL, RR) for front and rear inner angles. */
const wheels4 = (front: number, rear: number) => TRUCK.wheels.map((w, i) =>
  ackermann4(front, rear, xf, xr, track, w.x, w.z, i < 2 ? TRUCK.maxSteer : RS.maxAngle));
/** Steady rear angle after holding the inputs long enough for the slew to settle. */
const settle = (s: RearSteerState, front: number, speed: number, seconds = 2) => {
  for (let k = 0; k < seconds * 60; k++) s = rearSteerStep(RS, s, front, speed, 1 / 60);
  return s;
};

describe('steering angle mapping', () => {
  test('front lock is 42° inner-wheel at rest and falls with speed (half at 11 m/s)', () => {
    const input = { throttle: 0, brake: 0, steer: -1, handbrake: false };
    expect(handling(input, 0, TRUCK).steer / D).toBeCloseTo(42, 1);
    expect(handling(input, 11, TRUCK).steer / D).toBeCloseTo(21, 1);
    expect(handling({ ...input, steer: 1 }, 0, TRUCK).steer).toBeCloseTo(-TRUCK.maxSteer, 6);
    expect(RS.maxAngle / D).toBeCloseTo(32, 1);
  });

  test('ackermann4: 42° front and −32° rear inner give one instant centre; outer wheels follow it', () => {
    const [fl, fr, rl, rr] = wheels4(TRUCK.maxSteer, -RS.maxAngle);
    expect(fl / D).toBeCloseTo(42, 3);
    expect(rl / D).toBeCloseTo(-32, 3);
    expect(Math.abs(fr)).toBeLessThan(Math.abs(fl));
    expect(Math.abs(rr)).toBeLessThan(Math.abs(rl));
    // Every wheel's axle line passes through the same point.
    const tf = Math.tan(TRUCK.maxSteer), tr = Math.tan(-RS.maxAngle);
    const xc = (xf * tr - xr * tf) / (tr - tf), zc = -track / 2 - (xf - xc) / tf;
    TRUCK.wheels.forEach((w, i) => {
      const a = [fl, fr, rl, rr][i];
      expect(Math.tan(a) * (w.z - zc)).toBeCloseTo(w.x - xc, 6);
    });
    // Mirror symmetry: a right turn is the left turn mirrored.
    const right = wheels4(-TRUCK.maxSteer, RS.maxAngle);
    expect(right[1]).toBeCloseTo(-fl, 9); expect(right[0]).toBeCloseTo(-fr, 9);
    expect(right[3]).toBeCloseTo(-rl, 9); expect(right[2]).toBeCloseTo(-rr, 9);
  });

  test('ackermann4 reduces to front-steer Ackermann at rear 0 and steers in parallel in crab', () => {
    for (const front of [0.1, 0.4, 0.733]) {
      const [fl, fr, rl, rr] = wheels4(front, 0);
      expect(fl).toBeCloseTo(front, 9);
      expect(fr).toBeCloseTo(Math.atan(L * Math.tan(front) / (L + track * Math.tan(front))), 9);
      expect(rl).toBeCloseTo(0, 9); expect(rr).toBeCloseTo(0, 9);
      // The car's centre-line formula, fed the same turn centre, gives the same outer angle.
      const centre = Math.atan(L / (L / Math.tan(front) + track / 2));
      expect(ackermann(centre, L, track / 2)).toBeCloseTo(fr, 9);
    }
    expect(wheels4(0.3, 0.3)).toEqual([0.3, 0.3, 0.3, 0.3]);
    expect(wheels4(0, 0)).toEqual([0, 0, 0, 0]);
    // Rear-only steer (front centred): the front axle line holds the centre.
    const [fl, fr, rl] = wheels4(0, -0.3);
    expect(fl).toBeCloseTo(0, 9); expect(fr).toBeCloseTo(0, 9); expect(rl).toBeCloseTo(-0.3, 9);
  });
});

describe('rear-steer ratio against speed', () => {
  test('passive ratio: full below 2.5 m/s, a smoothstep to 0 at 13.4 m/s, |v| in reverse', () => {
    expect(passiveRatio(RS, 0)).toBe(1);
    expect(passiveRatio(RS, 2.5)).toBe(1);
    expect(passiveRatio(RS, (2.5 + 13.4) / 2)).toBeCloseTo(0.5, 9);
    expect(passiveRatio(RS, 13.4)).toBe(0);
    expect(passiveRatio(RS, 30)).toBe(0);
    expect(passiveRatio(RS, -6)).toBe(passiveRatio(RS, 6));
    for (let v = 0; v < 14; v += 0.25) expect(passiveRatio(RS, v + 0.25)).toBeLessThanOrEqual(passiveRatio(RS, v));
  });

  test('AUTO turns the rear opposite to the front, scaled by the ratio; nothing at 13.4 m/s and above', () => {
    for (const v of [0, 2, 6, 10, 13.4, 20]) {
      const s = settle(rearSteerIdle(), 1, v);
      expect(s.angle).toBeCloseTo(-passiveRatio(RS, v) * RS.maxAngle, 6);
    }
    expect(settle(rearSteerIdle(), -0.5, 1).angle).toBeCloseTo(0.5 * RS.maxAngle, 6);
  });

  test('lockout: engages at 13.4 m/s, holds down to 11.6 m/s, releases below; the rear slews at 1.4 rad/s', () => {
    let s = settle(rearSteerIdle(), 1, 1);
    expect(s.angle).toBeCloseTo(-RS.maxAngle, 6);
    // A step to lockout speed: the rear returns to centre at the slew limit, never in one tick.
    const next = rearSteerStep(RS, s, 1, 13.4, 1 / 60);
    expect(next.locked).toBe(true);
    expect(next.angle - s.angle).toBeCloseTo(RS.rate / 60, 9);
    s = settle(next, 1, 13.4);
    expect(s.angle).toBe(0);
    for (const v of [13, 12, 11.7]) { s = rearSteerStep(RS, s, 1, v, 1 / 60); expect(s.locked).toBe(true); expect(s.angle).toBe(0); }
    s = rearSteerStep(RS, s, 1, 11.5, 1 / 60);
    expect(s.locked).toBe(false);
    expect(rearSteerStep(RS, rearSteerIdle(), 1, 13.39, 1 / 60).locked).toBe(false);
  });

  test('AUTO 50% is exactly half of AUTO at every speed, with the same lockout; −16° at full lock and walking pace', () => {
    expect(AUTO50_SHARE).toBe(0.5);
    expect(settle(rearSteerIdle('auto50'), 1, 1).angle / D).toBeCloseTo(-16, 6);
    for (const v of [0, 2, 6, 10, 12, 13.4, 20]) for (const front of [1, -0.5, 0.3]) {
      expect(settle(rearSteerIdle('auto50'), front, v).angle).toBeCloseTo(0.5 * settle(rearSteerIdle('auto'), front, v).angle, 9);
    }
    let s = settle(rearSteerIdle('auto50'), 1, 1);
    s = rearSteerStep(RS, s, 1, 13.4, 1 / 60);
    expect(s.locked).toBe(true);
    for (const v of [13, 11.7]) { s = settle(s, 1, v, 0.5); expect(s.locked).toBe(true); expect(s.angle).toBe(0); }
    s = rearSteerStep(RS, s, 1, 11.5, 1 / 60);
    expect(s.locked).toBe(false);
  });

  test('SIDEWAYS (crab) copies the front angle in phase up to 32°, LOCK is 0; the cycle is AUTO → 50% → LOCK → SIDEWAYS', () => {
    const frac = (20 * D) / TRUCK.maxSteer;
    expect(settle(rearSteerIdle('crab'), frac, 3).angle).toBeCloseTo(20 * D, 6);
    expect(settle(rearSteerIdle('crab'), 1, 3).angle).toBeCloseTo(RS.maxAngle, 6);
    expect(settle(rearSteerIdle('crab'), 1, 15).angle).toBe(0);
    const lock = settle(rearSteerIdle('lock'), 1, 1);
    expect(lock.angle).toBe(0); expect(lock.locked).toBe(false);
    expect(REAR_STEER_CYCLE.map(nextRearMode)).toEqual(['auto50', 'lock', 'crab', 'auto']);
    expect([...REAR_STEER_CYCLE].sort()).toEqual([...REAR_STEER_MODES].sort());
  });
});

describe('turning radius', () => {
  test('passive 4WS at walking pace shortens the turn: kinematic radius and the autopilot wheelbase', () => {
    /** Chassis-centre radius about the instant centre for front/rear inner angles. */
    const radius = (front: number, rear: number) => {
      const tf = Math.tan(front), tr = Math.tan(rear);
      const xc = (xf * tr - xr * tf) / (tr - tf), inner = (xf - xc) / tf;
      return Math.hypot(xc, inner + track / 2);
    };
    const auto = radius(TRUCK.maxSteer, -RS.maxAngle), lock = radius(TRUCK.maxSteer, 0);
    expect(2 * auto).toBeGreaterThan(6.0); expect(2 * auto).toBeLessThan(7.2);
    expect(2 * lock).toBeGreaterThan(9.2); expect(2 * lock).toBeLessThan(10.8);
    expect(auto / lock).toBeLessThan(0.70);
    const eff = effectiveWheelbase(RS, L, TRUCK.maxSteer, 1);
    expect(eff).toBeCloseTo(L / (1 + Math.tan(RS.maxAngle) / Math.tan(TRUCK.maxSteer)), 9);
    expect(effectiveWheelbase(RS, L, TRUCK.maxSteer, 20)).toBe(L);
  });
});

describe('suspension travel and force curves', () => {
  const [front, , rear] = corners(TRUCK);

  const shape = TRUCK.suspensionCurve.shape;

  test('static loads carry the mass 51/49; ride frequencies 0.76 and 0.82 Hz (trophy-truck wheel rates)', () => {
    const c = corners(TRUCK);
    expect(c.reduce((s, k) => s + k.F0, 0)).toBeCloseTo(TRUCK.mass * 9.81, 3);
    expect((c[0].F0 + c[1].F0) / (TRUCK.mass * 9.81)).toBeCloseTo(0.51, 2);
    expect(Math.sqrt(front.K1 / (front.F0 / 9.81)) / (2 * Math.PI)).toBeCloseTo(0.76, 3);
    expect(Math.sqrt(rear.K1 / (rear.F0 / 9.81)) / (2 * Math.PI)).toBeCloseTo(0.82, 3);
    expect(front.K1).toBeGreaterThan(13000); expect(front.K1).toBeLessThan(14500);
    expect(front.critical).toBeGreaterThan(5500); expect(front.critical).toBeLessThan(6000);
    expect(corners(CAR)).toEqual([]);
    expect(staticLength(TRUCK)).toBe(0.417);
    expect(staticLength(CAR)).toBeCloseTo(CAR.rest - 9.81 / (4 * CAR.suspensionStiffness), 9);
  });

  test('spring: two-stage tender droop, main rate, hydraulic bump stop at 7 F0; continuous with no force steps', () => {
    const [kx, ky] = shape.droopKnee;
    for (const c of [front, rear]) {
      expect(springForce(c, 0)).toBeCloseTo(c.F0, 6);
      expect(springForce(c, -kx * c.droop)).toBeCloseTo(ky * c.F0, 6);
      expect(springForce(c, -c.droop)).toBeCloseTo(shape.droopFloor * c.F0, 6);
      expect(springForce(c, -c.droop - 0.01)).toBe(0);
      expect(springForce(c, c.bump)).toBeCloseTo(7 * c.F0, 3);
      expect((springForce(c, 0.2 * c.bump) - springForce(c, 0.1 * c.bump)) / (0.1 * c.bump)).toBeCloseTo(c.K1, 3);
      const x = (shape.crossover + shape.stop) / 2;
      expect((springForce(c, (x + 0.01) * c.bump) - springForce(c, (x - 0.01) * c.bump)) / (0.02 * c.bump)).toBeCloseTo(shape.rate2 * c.K1, 3);
      const steepest = Math.max(shape.rate2 * c.K1, (ky - shape.droopFloor) * c.F0 / ((1 - kx) * c.droop));
      let last = springForce(c, -c.droop);
      for (let b = -c.droop + 0.001; b <= c.bump; b += 0.001) {
        const f = springForce(c, b);
        expect(f).toBeGreaterThan(last);
        expect(f - last).toBeLessThan(0.001 * steepest + 0.001 * 2 * c.stop * (1 - shape.stop) * c.bump + 1);
        last = f;
      }
    }
  });

  test('cross-axle (D8): a corner at 85 % bump plus its neighbour at full droop carry less than half the weight', () => {
    // Static warp with four wheels down: FL and RR carry the same load A, FR and RL the same B, A + B = m·g / 2, so a
    // corner can reach 85 % bump only if F(0.85 bump) + F(full droop) < m·g / 2 (necessary; the course test D8 holds it).
    // The 1.10/1.20 Hz dual-rate curve gave 19 kN at 85 % front bump against an 11.5 kN half weight.
    const half = TRUCK.mass * 9.81 / 2;
    for (const c of [front, rear]) expect(springForce(c, 0.85 * c.bump) + springForce(c, -c.droop)).toBeLessThan(0.95 * half);
  });

  test('bypass damping: position zones blended without steps, top-out rebound, bump-stop compression', () => {
    const c = front, [z0, z1, z2] = shape.compression;
    expect(damping(c, 0).compression).toBeCloseTo(z0 * c.critical, 0);
    expect(damping(c, 0.6 * c.bump).compression).toBeCloseTo(z1 * c.critical, 0);
    expect(damping(c, 0.8 * c.bump).compression).toBeCloseTo(z2 * c.critical, 0);
    expect(damping(c, 0.95 * c.bump).compression).toBeCloseTo(z2 * c.critical + shape.stopDamping, 0);
    expect(damping(c, 0).rebound).toBeCloseTo(shape.rebound * c.critical, 0);
    expect(damping(c, -0.95 * c.droop).rebound).toBeCloseTo(shape.topOut * c.critical, 0);
    let last = damping(c, -c.droop);
    for (let b = -c.droop; b <= c.bump; b += 0.002) {
      const d = damping(c, b);
      expect(Math.abs(d.compression - last.compression)).toBeLessThan(3000);
      expect(Math.abs(d.rebound - last.rebound)).toBeLessThan(400);
      last = d;
    }
  });

  test('schedule: the linear Rapier spring through rest equals F(b) at the predicted length; zero past full droop', () => {
    const M = TRUCK.mass, rest = TRUCK.rest, dt = 1 / 60;
    for (const b of [-0.25, -0.1, 0, 0.1, 0.25, 0.33]) {
      const length = front.staticLength - b, k = schedule(front, length, 0, rest, M, dt);
      const bias = damping(front, b).compression * 0.12 * 9.81 * dt;
      expect(k.stiffness * M * (rest - length)).toBeCloseTo(springForce(front, b) - bias, 3);
      expect(k.compression * M).toBeCloseTo(damping(front, b).compression, 3);
    }
    // Moving at 3 m/s into bump: the spring is taken one tick ahead.
    const moving = schedule(front, front.staticLength - 0.1, 3, rest, M, dt);
    expect(moving.compressionFrom).toBeCloseTo(0.15, 9);
    // High-speed compression: above the knee the circuit adds coef·(v − knee) of force.
    const hs = shape.highSpeed, landing = schedule(front, front.staticLength + 0.2, 6, rest, M, dt, 57000);
    expect(landing.compression * M * 6).toBeCloseTo(damping(front, landing.compressionFrom).compression * 6 + hs.coef * (6 - hs.knee), 3);
    const slow = schedule(front, front.staticLength, hs.knee * 0.9, rest, M, dt);
    expect(slow.compression * M).toBeCloseTo(damping(front, slow.compressionFrom).compression, 3);
    // Blow-off: spring plus damper stay under the force limit.
    const hard = schedule(front, front.staticLength - 0.33, 6, rest, M, dt, 57000);
    expect(springForce(front, 0.36) + hard.compression * M * 6).toBeLessThanOrEqual(57000 + 1e-6);
    const hanging = schedule(front, droopLength(TRUCK, 0) + 0.02, 0, rest, M, dt);
    expect([hanging.stiffness, hanging.compression, hanging.relaxation]).toEqual([0, 0, 0]);
  });

  test('travel limits: Rapier clamps a long-travel corner between full bump and the longest ray', () => {
    for (const [i, a] of [[0, TRUCK.suspensionCurve.axles[0]], [2, TRUCK.suspensionCurve.axles[1]]] as const) {
      expect(droopLength(TRUCK, i)).toBeCloseTo(0.417 + a.droop, 9);
      expect(TRUCK.rest - droopLength(TRUCK, i)).toBeGreaterThanOrEqual(0);
      expect(a.bump + a.droop).toBeGreaterThan(0.6);
    }
    expect(droopLength(CAR, 0)).toBe(CAR.rest);
  });
});

describe('multiplayer drv field', () => {
  test('encode/decode round-trips at its resolution and clamps out-of-range values', () => {
    const d = encodeDrv({ throttle: 0.873, rpm: 6123, gear: 2, rearMode: 'auto50' });
    expect(d).toEqual([87, 612, 2, 2]);
    expect(validDrv(d)).toBe(true);
    expect(decodeDrv(d)).toEqual({ throttle: 0.87, rpm: 6120, gear: 2, rearMode: 'auto50' });
    // Wire indices: AUTO, CRAB and LOCK keep the ones older clients decode; AUTO 50% has MANUAL's old 2, in range.
    expect(REAR_STEER_MODES).toEqual(['auto', 'crab', 'auto50', 'lock']);
    expect([0, 1, 2, 3].map(i => decodeDrv([0, 0, 0, i])?.rearMode)).toEqual(['auto', 'crab', 'auto50', 'lock']);
    expect(encodeDrv({ throttle: 3, rpm: 99999, gear: 9, rearMode: 'lock' })).toEqual([100, 800, 3, 3]);
    expect(encodeDrv({ throttle: -1, rpm: NaN, gear: -4, rearMode: 'auto' })).toEqual([0, 0, -1, 0]);
    for (const bad of [[1.5, 0, 0, 0], [0, 801, 0, 0], [0, 0, 4, 0], [0, 0, 0, 4], [0, 0, 0], 'x', null]) {
      expect(validDrv(bad)).toBe(false);
      expect(decodeDrv(bad)).toBeNull();
    }
  });

  test('drv comes from a 4WS vehicle state; a drv change sends a pose and changes the shared fingerprint', () => {
    const state = { rearMode: 'crab', throttleApplied: 0.5, wheels: [] } as unknown as VehicleState;
    expect(drvOf(state, 3200, 1)).toEqual([50, 320, 1, 1]);
    expect(drvOf({ ...state, rearMode: undefined }, 3200, 1)).toBeUndefined();
    const pose: NetPose = { t: 0, E: 0, N: 0, H: 0, yaw: 0, vE: 0, vN: 0, vH: 0, mode: 'truck', vehicle: 'truck', carrying: false, drv: [50, 320, 1, 1] };
    expect(shouldSendPose(100, pose, 0, { ...pose, t: 100 })).toBe(false);
    expect(shouldSendPose(100, pose, 0, { ...pose, t: 100, drv: [50, 330, 1, 1] })).toBe(true);
    expect(vehicleFingerprint({ ...pose, drv: [50, 330, 1, 1] })).not.toBe(vehicleFingerprint(pose));
  });
});

describe('controls', () => {
  test('rear mode: each press cycles AUTO → AUTO 50% → LOCK → SIDEWAYS → AUTO; entering adopts the truck\'s mode', () => {
    const c = new RearSteerControl();
    expect(c.update(false)).toBe('auto');
    expect([c.update(true), c.update(true), c.update(true), c.update(true)]).toEqual(['auto50', 'lock', 'crab', 'auto']);
    expect(c.update(false)).toBe('auto');
    c.sync('lock');
    expect(c.update(true)).toBe('crab');
    c.sync(undefined);
    expect(c.mode).toBe('crab');
  });

  test('bindings: sprint (A, Shift) cycles; the old X/Q mode and Z/X/right-stick rocker actions are gone', () => {
    expect(DEFAULT_BINDINGS.sprint).toEqual([{ kind: 'button', index: 0 }, { kind: 'key', code: 'ShiftLeft', scale: 1 }, { kind: 'key', code: 'ShiftRight', scale: 1 }]);
    expect(ACTIONS as readonly string[]).not.toContain('rearMode');
    expect(ACTIONS as readonly string[]).not.toContain('rearRocker');
    expect(promptLabel('sprint', DEFAULT_BINDINGS, 'xbox')).toBe('Ⓐ');
    expect(promptLabel('sprint', DEFAULT_BINDINGS, 'playstation')).toBe('✕');
    expect(promptLabel('sprint', DEFAULT_BINDINGS, 'keyboard')).toBe('Shift');
    for (const code of ['ShiftLeft', 'ShiftRight']) expect(mapActions(null, new Set([code]), DEFAULT_BINDINGS).sprint).toBe(1);
    // No driving action shares A or Shift; only `mission` shares A, and play.ts gives a press at a mission target to it.
    const vehicle: Action[] = ['throttle', 'brake', 'steer', 'handbrake', 'horn', 'lights', 'interact', 'cameraReset', 'vehicleReset', 'map', 'pause', 'mute'];
    for (const a of vehicle) for (const b of DEFAULT_BINDINGS[a]) {
      if (b.kind === 'key') expect(['ShiftLeft', 'ShiftRight']).not.toContain(b.code);
      if (b.kind === 'button') expect(b.index).not.toBe(0);
    }
    expect(DEFAULT_BINDINGS.mission.filter(b => b.kind !== 'key')).toEqual([{ kind: 'button', index: 0 }]);
    expect(DEFAULT_BINDINGS.mission.some(b => b.kind === 'key' && b.code.startsWith('Shift'))).toBe(false);
  });
});

describe('camera and HUD', () => {
  test('heave follower: critically damped, no overshoot on a step, settles in about 1 s', () => {
    const s = { y: 0, v: 0 };
    let peak = 0;
    for (let k = 0; k < 120; k++) peak = Math.max(peak, heaveFollow(s, 1, 1 / 60, 0.22));
    expect(peak).toBeLessThanOrEqual(1 + 1e-9);
    expect(s.y).toBeGreaterThan(0.99);
  });

  test('rear tag reads the mode, the lockout and the rear angle', () => {
    const state = (rearMode: VehicleState['rearMode'], rearLocked: boolean, steer: number) =>
      ({ rearMode, rearLocked, wheels: [0, 0, steer, steer].map(s => ({ steer: s })) }) as unknown as VehicleState;
    expect(rearSteerTag(state('auto', false, -0.2), RS.maxAngle)).toEqual({ mode: 'auto', text: 'REAR AUTO', tick: -0.2 / RS.maxAngle, locked: false });
    expect(rearSteerTag(state('lock', true, 0), RS.maxAngle)?.text).toBe('REAR LOCK');
    expect(rearSteerTag(state('auto50', false, 0), RS.maxAngle)?.text).toBe('REAR AUTO 50%');
    expect(rearSteerTag(state('crab', false, 0), RS.maxAngle)?.text).toBe('REAR SIDEWAYS');
    expect(rearSteerTag(state(undefined, false, 0), RS.maxAngle)).toBeNull();
  });

  test('key hint: the device binding and the mode; shows on entering and briefly on each change, then fades', () => {
    expect(rearSteerHint('Ⓐ', 'auto')).toBe('Ⓐ · Rear steer: AUTO');
    expect(rearSteerHint('Shift', 'auto50')).toBe('Shift · Rear steer: AUTO 50%');
    expect(rearSteerHint('', 'crab')).toBe('Rear steer: SIDEWAYS');
    const h = new RearSteerHintTimer();
    expect(h.update(null, 0)).toBe(false);
    expect(h.update('auto', 1000)).toBe(true); // entered
    expect(h.update('auto', 1000 + HINT_ENTER_MS - 1)).toBe(true);
    expect(h.update('auto', 1000 + HINT_ENTER_MS)).toBe(false);
    expect(h.update('auto50', 10_000)).toBe(true); // changed
    expect(h.update('auto50', 10_000 + HINT_CHANGE_MS - 1)).toBe(true);
    expect(h.update('auto50', 10_000 + HINT_CHANGE_MS)).toBe(false);
    // A change soon after entering never cuts the entry hint short; leaving and entering again shows it again.
    expect(h.update(null, 20_000)).toBe(false);
    expect(h.update('auto50', 21_000)).toBe(true);
    expect(h.update('lock', 21_500)).toBe(true);
    expect(h.update('lock', 21_000 + HINT_ENTER_MS - 1)).toBe(true);
    expect(h.update('lock', 21_000 + HINT_ENTER_MS)).toBe(false);
  });
});

test('car and scooter keep front-only steering and their own grip', () => {
  expect(CAR.front).toEqual(CAR.steered);
  expect(SCOOTER.front).toEqual(SCOOTER.steered);
  expect('rearSteer' in CAR || 'rearSteer' in SCOOTER).toBe(false);
  const h = handling({ throttle: 1, brake: 0, steer: 0, handbrake: false }, 30, CAR);
  expect(h.frontGrip).toBe(2.4 - 0.5);
  expect(h.rearGrip).toBe(2.2 - Math.min(0.4, 30 / 65));
});

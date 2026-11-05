// SPEC §7 Dynamics scenarios on the headless course; each returns the measured numbers.
import { TRUCK } from '../../src/physics/constants';
import { MAT } from '../../src/shared/col/materials';
import type { RearSteerMode } from '../../src/vehicle/rearSteer';
import { heaveFollow } from '../../src/vehicle/chaseCamera';
import { axes, box, course, cruise, deg, G, heading, idle, pitch, ramp, roll, travel } from './trophy4400-course';
import { vehicleGeometry, vehiclePose } from '../../src/gameplay/autopilot/autopilot';
import { DriveFollower } from '../../src/gameplay/autopilot/follow';
import { makePath } from '../../src/gameplay/autopilot/path';

const CURVE = TRUCK.suspensionCurve;

/** D1: static settle on flat ground. */
export function staticSettle() {
  const c = course({ settle: 3 });
  const s = c.vehicle.state();
  const hub = s.wheels.map(w => s.pos.y + w.pos.y);
  const L = s.wheels.map(w => w.suspension);
  const out = {
    origin: s.pos.y, belly: s.pos.y - TRUCK.halfExt.y,
    hub: hub.map(h => +h.toFixed(4)),
    bump: L.map((l, i) => +(l - (CURVE.staticLength - CURVE.axles[i < 2 ? 0 : 1].bump)).toFixed(4)),
    droop: L.map((l, i) => +(CURVE.staticLength + CURVE.axles[i < 2 ? 0 : 1].droop - l).toFixed(4)),
    contact: s.wheels.every(w => w.contact),
  };
  c.free();
  return out;
}

/** D2: wheel angles at v < 1 m/s, full left input. */
export function lockAngles(mode: RearSteerMode = 'auto') {
  const c = course();
  const s = c.run(1.2, { ...idle(mode), steer: -1 });
  c.free();
  return { speed: s.forwardSpeed, angles: s.wheels.map(w => deg(w.steer)), rearLocked: s.rearLocked };
}

/** D3: rear inner angle at full front input held at each speed (cruise control), plus the slew rate. */
export function passiveTaper(speeds = [2, 6, 10, 13.4, 20]) {
  const out: { target: number; speed: number; rear: number; front: number; locked: boolean }[] = [];
  let slew = 0;
  for (const target of speeds) {
    const c = course({ material: MAT.road });
    // Spin up straight, then steer: the measurement is the steady rear angle at that speed.
    c.run(12, s => cruise(s, target, 0, 'auto'), s => Math.abs(s.forwardSpeed - target) < 0.05);
    let prev = c.vehicle.state().wheels[2].steer;
    const s = c.run(1.5, st => cruise(st, target, -1, 'auto'), st => {
      slew = Math.max(slew, Math.abs(st.wheels[2].steer - prev) * 60); prev = st.wheels[2].steer;
    });
    out.push({ target, speed: s.forwardSpeed, rear: s.wheels[2].steer, front: s.wheels[0].steer, locked: !!s.rearLocked });
    c.free();
  }
  return { samples: out, slew };
}

/** D3 hysteresis: accelerate past the lockout, then slow down; the speeds where it locks and releases. */
export function lockoutHysteresis() {
  const c = course();
  let lockedAt = NaN, releasedAt = NaN, was = false;
  const watch = (s: ReturnType<typeof c.vehicle.state>) => {
    if (s.rearLocked && !was) lockedAt = s.forwardSpeed;
    if (!s.rearLocked && was) releasedAt = s.forwardSpeed;
    was = !!s.rearLocked;
  };
  c.run(10, { ...idle('auto'), throttle: 1 }, s => { watch(s); return s.forwardSpeed > 16; });
  c.run(10, { ...idle('auto'), brake: 0.25 }, s => { watch(s); return s.forwardSpeed < 8; });
  c.free();
  return { lockedAt, releasedAt };
}

/** D4: chassis-centre path diameter at 2 m/s on full left lock. */
export function turningCircle(mode: RearSteerMode) {
  const c = course();
  c.run(4, s => cruise(s, 2, -1, mode));
  const pts: { x: number; z: number }[] = [];
  c.run(14, s => cruise(s, 2, -1, mode), s => { pts.push({ x: s.pos.x, z: s.pos.z }); });
  c.free();
  // Least-squares circle fit (Kåsa).
  const n = pts.length, mx = pts.reduce((a, p) => a + p.x, 0) / n, mz = pts.reduce((a, p) => a + p.z, 0) / n;
  let suu = 0, svv = 0, suv = 0, suuu = 0, svvv = 0, suvv = 0, svuu = 0;
  for (const p of pts) {
    const u = p.x - mx, v = p.z - mz;
    suu += u * u; svv += v * v; suv += u * v; suuu += u * u * u; svvv += v * v * v; suvv += u * v * v; svuu += v * u * u;
  }
  const a = (suuu + suvv) / 2, b = (svvv + svuu) / 2, det = suu * svv - suv * suv;
  const uc = (a * svv - b * suv) / det, vc = (b * suu - a * suv) / det;
  const r = Math.sqrt(uc * uc + vc * vc + (suu + svv) / n);
  return { diameter: 2 * r };
}

/** D5: crab at 3 m/s with the front at 20°. */
export function crab() {
  const c = course();
  const lock = TRUCK.maxSteer / (1 + (3 / TRUCK.steerHalfSpeed) ** 2), steer = -(20 * Math.PI / 180) / lock;
  c.run(4, s => cruise(s, 3, 0, 'crab'));
  let yaw = 0, slip = 0, n = 0, front = 0, rear = 0;
  c.run(4, s => cruise(s, 3, steer, 'crab'), (s, t) => {
    if (t < 2) return;
    const v = s.linvel, f = axes(s.rot).forward;
    const travelDir = Math.atan2(-v.z, v.x), head = Math.atan2(-f.z, f.x);
    slip += deg(Math.atan2(Math.sin(travelDir - head), Math.cos(travelDir - head))); n++;
    front = deg(s.wheels[0].steer); rear = deg(s.wheels[2].steer);
  });
  const h0 = heading(c.vehicle.state().rot);
  c.run(1, s => cruise(s, 3, steer, 'crab'));
  yaw = Math.abs(heading(c.vehicle.state().rot) - h0);
  c.free();
  return { yawRate: yaw, travelAngle: slip / n, front, rear };
}

interface LandingMetrics { peakCompression: number; chassisContact: boolean; peakForce: number; overshoot: number; settle: number; airPitch: number; airtime: number }

function landing(c: ReturnType<typeof course>, seconds: number, input: Parameters<ReturnType<typeof course>['run']>[1], airborneFrom = -Infinity) {
  let peak = 0, peakForce = 0, air = 0, airtime = 0, airPitch = 0, landed = -1, minY = Infinity, minAt = -1;
  const ys: { t: number; y: number }[] = [];
  c.run(seconds, input, (s, t) => {
    const flying = s.wheels.every(w => !w.contact) && s.pos.x > airborneFrom;
    if (flying) { air += 1 / 60; airPitch = Math.max(airPitch, Math.abs(pitch(s.rot))); }
    else if (air > 0.1 && landed < 0) { airtime = air; landed = t; }
    for (let i = 0; i < 4; i++) {
      peak = Math.max(peak, travel(s, i));
      peakForce = Math.max(peakForce, Math.min(TRUCK.maxSuspensionForce, c.vehicle.controller.wheelSuspensionForce(i) ?? 0));
    }
    if (landed >= 0) { ys.push({ t, y: s.pos.y }); if (s.pos.y < minY) { minY = s.pos.y; minAt = t; } }
  });
  // After the first compression: the rebound peak above the final ride height, and when it stays within ±2 cm.
  const final = ys.length ? ys.at(-1)!.y : NaN;
  const after = ys.filter(p => p.t >= minAt);
  const overshoot = after.length ? Math.max(...after.map(p => p.y)) - final : NaN;
  let settledAt = minAt;
  for (const p of after) if (Math.abs(p.y - final) > 0.02) settledAt = p.t;
  return { peakCompression: peak, chassisContact: c.chassisContact, peakForce, overshoot, settle: settledAt - minAt, airPitch, airtime } satisfies LandingMetrics;
}

/** D6: a 1.2 m flat drop (static ride height + 1.2 m, at rest). */
export function flatDrop() {
  const c = course({ y: 0.98 + 1.2, settle: 0 });
  // Let the suspension reach full droop in the air before the measurement (a real drop leaves at static).
  const m = landing(c, 4, idle());
  c.free();
  return m;
}

/** D6 + D13: kicker with the chassis height trace for the camera follower. */
export function kickerTrace(speed = 60 / 3.6) {
  const c = course({ x: -60, build: w => ramp(w, 0, 1.5, 6) });
  c.run(12, s => cruise(s, speed), s => s.pos.x > -20 && Math.abs(s.forwardSpeed - speed) < 0.3);
  let peak = 0, peakForce = 0, air = 0, airPitch = 0, airtime = 0, landed = false, takeoffPitch = 0, airRotation = 0, landingPitch = 0;
  const ys: number[] = [];
  // Cruise on the ground; hands off (no throttle, brake or steer) while airborne.
  c.run(5, s => s.wheels.some(w => w.contact) ? cruise(s, speed) : idle(), s => {
    ys.push(s.pos.y);
    const flying = s.wheels.every(w => !w.contact) && s.pos.x > -0.5;
    if (flying) {
      if (air === 0) takeoffPitch = pitch(s.rot);
      air += 1 / 60;
      if (Math.abs(pitch(s.rot)) > Math.abs(airPitch)) airPitch = pitch(s.rot);
      airRotation = Math.max(airRotation, Math.abs(pitch(s.rot) - takeoffPitch));
      landingPitch = pitch(s.rot);
    } else if (air > 0.1 && !landed) { airtime = air; landed = true; }
    for (let i = 0; i < 4; i++) {
      peak = Math.max(peak, travel(s, i));
      peakForce = Math.max(peakForce, Math.min(TRUCK.maxSuspensionForce, c.vehicle.controller.wheelSuspensionForce(i) ?? 0));
    }
  });
  const end = c.vehicle.state();
  const out = { peakCompression: peak, peakForce, airPitch, takeoffPitch, landingPitch, airRotation, airtime, chassisContact: c.chassisContact, endSpeed: end.forwardSpeed,
    endTilt: Math.abs(roll(end.rot)) + Math.abs(pitch(end.rot)), ys };
  c.free();
  return out;
}

/** D13: vertical acceleration of the camera target against the chassis on a height trace (60 Hz). */
export function cameraHeave(ys: number[]) {
  const f = { y: ys[0], v: 0 }, cam = ys.map(y => heaveFollow(f, y, 1 / 60, 0.22));
  const acc = (a: number[]) => {
    let peak = 0;
    for (let i = 2; i < a.length; i++) peak = Math.max(peak, Math.abs(a[i] - 2 * a[i - 1] + a[i - 2]) * 3600);
    return peak;
  };
  // A free-flight parabola is the same −g for both; measure the landing transients only (where |a + g| is large).
  const transient = (a: number[]) => {
    let peak = 0;
    for (let i = 2; i < a.length; i++) peak = Math.max(peak, Math.abs((a[i] - 2 * a[i - 1] + a[i - 2]) * 3600 + G));
    return peak;
  };
  return { chassisPeak: acc(ys), cameraPeak: acc(cam), chassisTransient: transient(ys), cameraTransient: transient(cam) };
}

/** D7/D10: steady circle; ramps speed on a fixed lock and records roll against lateral acceleration. */
export function circle(material: number, steer = -0.35) {
  const c = course({ material });
  const rows: { v: number; ay: number; roll: number; contacts: number; frontSlip: number; rearSlip: number }[] = [];
  for (let v = 4; v <= 24; v += 1) {
    c.run(3, s => cruise(s, v, steer, 'auto'));
    let ay = 0, r = 0, n = 0, contacts = 4, fs = 0, rs = 0;
    c.run(1.5, s => cruise(s, v, steer, 'auto'), s => {
      const omega = c.vehicle.body.angvel().y;
      ay += Math.abs(omega * Math.hypot(s.linvel.x, s.linvel.z)); r += Math.abs(roll(s.rot)); n++;
      contacts = Math.min(contacts, s.wheels.filter(w => w.contact).length);
      const slip = (i: number) => s.wheels[i].tyre ? Math.abs(deg(Math.atan2(s.wheels[i].tyre!.lateral, Math.abs(s.wheels[i].tyre!.longitudinal) + 0.1))) : 0;
      fs += (slip(0) + slip(1)) / 2; rs += (slip(2) + slip(3)) / 2;
    });
    rows.push({ v, ay: ay / n / G, roll: r / n, contacts, frontSlip: fs / n, rearSlip: rs / n });
  }
  c.free();
  return rows;
}

/** D9: 0-100 km/h and the top speed; then 100-0 braking distance. */
export function acceleration(material: number) {
  const c = course({ material, x: -1200 });
  let t100 = NaN;
  const top = c.run(40, { ...idle(), throttle: 1 }, (s, t) => { if (Number.isNaN(t100) && s.forwardSpeed * 3.6 >= 100) t100 = t; });
  c.free();
  const b = course({ material, x: -1200 });
  b.run(15, { ...idle(), throttle: 1 }, s => s.forwardSpeed * 3.6 >= 100);
  const from = b.vehicle.state().pos.x;
  let stop = NaN;
  b.run(10, { ...idle(), brake: 1 }, s => { if (s.forwardSpeed < 0.3) { stop = s.pos.x - from; return true; } });
  b.free();
  return { t100, top: top.forwardSpeed, stop100: stop };
}

/** D8: a 0.6 m step under the front-left wheel path only (1:2.5 wedge, then a plateau shorter than the wheelbase),
 *  climbed at walking pace and held: the chassis twists about the FR–RL diagonal. */
export function articulation() {
  const c = course({ x: -6, build: w => { ramp(w, 0, 0.6, 1.5, -1.4, -0.95); box(w, 1.0, 0.3, -1.175, 1.0, 0.3, 0.225); } });
  c.run(14, s => cruise(s, 1.2), s => s.pos.x + TRUCK.wheels[0].x > 1.0);
  const s = c.run(3, { ...idle(), handbrake: true });
  const out = { bumpFL: travel(s, 0), droopRR: -travel(s, 3), FR: travel(s, 1), RL: travel(s, 2),
    contacts: s.wheels.filter(w => w.contact).length, chassisContact: c.chassisContact };
  c.free();
  return out;
}

/** Slalom: cones every `gap` m, alternating ±`offset` m, at `speed`; a pure-pursuit driver. */
export function slalom(speed: number, gap = 18, offset = 2.2, mode: RearSteerMode = 'auto') {
  const c = course({ x: -40 });
  c.run(12, s => cruise(s, speed, 0, mode), s => Math.abs(s.forwardSpeed - speed) < 0.3 && s.pos.x > -20);
  const target = (x: number) => x < 0 ? 0 : (Math.floor(x / gap) % 2 ? -offset : offset) * Math.sin(Math.PI * ((x % gap) / gap));
  let maxRoll = 0, maxAy = 0, maxRear = 0, maxDev = 0, minContacts = 4, yawPeak = 0;
  const s = c.run(9 * gap / speed + 1, st => {
    const look = Math.max(6, st.forwardSpeed * 0.6), aimX = st.pos.x + look, aimZ = target(aimX);
    const f = axes(st.rot).forward, h = Math.atan2(f.z, f.x), want = Math.atan2(aimZ - st.pos.z, look);
    const err = Math.atan2(Math.sin(want - h), Math.cos(want - h));
    return cruise(st, speed, Math.max(-1, Math.min(1, err * 2.5)), mode);
  }, st => {
    if (st.pos.x < 0) return;
    const omega = c.vehicle.body.angvel().y;
    maxRoll = Math.max(maxRoll, Math.abs(roll(st.rot)));
    maxAy = Math.max(maxAy, Math.abs(omega * Math.hypot(st.linvel.x, st.linvel.z)) / G);
    maxRear = Math.max(maxRear, Math.abs(deg(st.wheels[2].steer)));
    maxDev = Math.max(maxDev, Math.abs(st.pos.z - target(st.pos.x)));
    minContacts = Math.min(minContacts, st.wheels.filter(w => w.contact).length);
    yawPeak = Math.max(yawPeak, Math.abs(omega));
    return st.pos.x > 8 * gap;
  });
  c.free();
  return { speed, maxRoll, maxAy, maxRearDeg: maxRear, maxDeviation: maxDev, minContacts, yawPeak, endSpeed: s.forwardSpeed, done: s.pos.x > 8 * gap };
}

/** D10: skidpad on a 25 m radius (left), speed stepped up until the truck can no longer hold the line. */
export function skidpad(material: number, radius = 25) {
  const c = course({ material });
  const cz = -radius;
  let best = 0, v = 6;
  const drive = (st: ReturnType<typeof c.vehicle.state>, target: number) => {
    // Pure pursuit on the circle, centre (0, −R), counter-clockwise seen from above (a left turn).
    const ang = Math.atan2(st.pos.x, st.pos.z - cz), ahead = ang + Math.max(6, st.forwardSpeed * 0.7) / radius;
    const ax = Math.sin(ahead) * radius, az = cz + Math.cos(ahead) * radius;
    const f = axes(st.rot).forward, h = Math.atan2(f.z, f.x), want = Math.atan2(az - st.pos.z, ax - st.pos.x);
    const err = Math.atan2(Math.sin(want - h), Math.cos(want - h));
    return cruise(st, target, Math.max(-1, Math.min(1, err * 3)), 'auto');
  };
  const rows: { v: number; ay: number; error: number; frontSlip: number; rearSlip: number }[] = [];
  for (; v <= 26; v += 0.5) {
    let ay = 0, n = 0, error = 0, fs = 0, rs = 0;
    c.run(2.5, st => drive(st, v));
    c.run(1.5, st => drive(st, v), st => {
      const omega = c.vehicle.body.angvel().y;
      ay += Math.abs(omega * Math.hypot(st.linvel.x, st.linvel.z)); n++;
      error = Math.max(error, Math.abs(Math.hypot(st.pos.x, st.pos.z - cz) - radius));
      const slip = (i: number) => st.wheels[i].tyre ? Math.abs(deg(Math.atan2(st.wheels[i].tyre!.lateral, Math.abs(st.wheels[i].tyre!.longitudinal) + 0.1))) : 0;
      fs += (slip(0) + slip(1)) / 2; rs += (slip(2) + slip(3)) / 2;
    });
    rows.push({ v, ay: ay / n / G, error, frontSlip: fs / n, rearSlip: rs / n });
    if (error < 2) best = Math.max(best, ay / n / G);
    if (error > 4) break;
  }
  c.free();
  return { maxLateral: best, rows };
}

/** Axle slip angles (deg, magnitude) from the chassis velocity, yaw rate and each axle's mean steer. */
function axleSlip(s: ReturnType<ReturnType<typeof course>['vehicle']['state']>, yawRate: number) {
  const a = axes(s.rot), v = s.linvel;
  const long = v.x * a.forward.x + v.y * a.forward.y + v.z * a.forward.z, lat = v.x * a.right.x + v.y * a.right.y + v.z * a.right.z;
  const at = (x: number, steer: number) => Math.abs(deg(Math.atan2(lat - yawRate * x, Math.abs(long)) + steer));
  return { front: at(TRUCK.wheels[0].x, (s.wheels[0].steer + s.wheels[1].steer) / 2), rear: at(TRUCK.wheels[2].x, (s.wheels[2].steer + s.wheels[3].steer) / 2) };
}

/** D10: power-on corner exit on dirt: steady 0.45 g on a fixed lock, then full throttle; rear slip minus front slip. */
export function powerOnExit(material = MAT.soil) {
  const c = course({ material });
  c.run(6, s => cruise(s, 9, -0.45, 'auto'));
  let worst = -Infinity, front = 0, rear = 0;
  c.run(1.2, { ...idle('auto'), throttle: 1, steer: -0.45 }, s => {
    const slip = axleSlip(s, c.vehicle.body.angvel().y);
    if (slip.rear - slip.front > worst) { worst = slip.rear - slip.front; front = slip.front; rear = slip.rear; }
  });
  c.free();
  return { rearMinusFront: worst, front, rear };
}

/** D11: the autopilot follower drives the truck round a 10 m radius after a straight. */
export function autopilotCircle() {
  const pts = [{ x: 0, z: 0 }, { x: 10, z: 0 }, { x: 20, z: 0 }];
  for (let a = 7.5; a <= 270; a += 7.5) pts.push({ x: 20 + 10 * Math.sin(a * Math.PI / 180), z: -10 + 10 * Math.cos(a * Math.PI / 180) });
  const path = makePath(pts);
  const c = course({ x: 0 });
  const follower = new DriveFollower(path, vehicleGeometry('truck'), { speed: 7 });
  let maxLateral = 0, status = 'driving', top = 0;
  for (let k = 0; k < 60 * 40 && status !== 'arrived'; k++) {
    const st = c.vehicle.state();
    const step = follower.step(vehiclePose(st), 1 / 60);
    status = step.status;
    if (follower.s > 25 && follower.s < path.length - 4) maxLateral = Math.max(maxLateral, Math.abs(step.lateral));
    top = Math.max(top, st.speed);
    c.step({ ...step.command, rearMode: 'auto' });
  }
  c.free();
  return { status, maxLateral, top, recoveries: follower.recoveries };
}

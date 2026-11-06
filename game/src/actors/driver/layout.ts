import type { FleetModel } from '../fleet/catalog';
import ANCHORS from './anchors.json';

/** Where a seated driver and its steering wheel sit in a vehicle, in the fleet model frame (+Z forward, +X left,
 *  Y up, tyres on Y = 0; metres, radians). Study: ~/turku3d/research/driver-animation/study.md, "Turku3D driver rig
 *  targets". The six posture classes are its starting values; the wheel ratio and caps are measured (cars, Wreckfest 2)
 *  or design values (trucks, buses). */
export type DriverClass = 'saloon' | 'hatch' | 'suv' | 'van' | 'truck' | 'bus' | 'race';
export type V3 = [number, number, number];

export interface DriverLayout {
  cls: DriverClass;
  /** The H-point: where the character's `DEF-hips` joint sits. */
  hips: V3;
  /** Torso recline from vertical (the Driving_Loop clip is close to upright; the rig adds the rest at the spine). */
  recline: number;
  wheel: {
    centre: V3;
    /** Rim centreline radius (m). */
    radius: number;
    /** Column tilt from vertical: the wheel plane's normal points back to the driver and up by this angle. */
    tilt: number;
    /** Grip points on the rim, ± this angle from 12 o'clock. */
    grip: number;
    /** Steering-wheel degrees per degree of road-wheel angle, and the wheel's cap. */
    ratio: number; cap: number;
    /** Drawn by the rig (a model without its own articulated wheel); false: the vehicle rig turns its own. */
    drawn: boolean;
  };
}

const DEG = Math.PI / 180;

/** Per class: H-point height above the ground and the wheel centre relative to the H-point (forward, up) as the
 *  designer intent, the recline, rim diameter, column tilt, grip, ratio and cap; `axle`: the wheel centre behind the
 *  front axle as a fraction of the wheelbase (negative: ahead of it, cab-over trucks and buses); `lateral`: the
 *  driver's centreline left of the vehicle's. */
interface Preset { hip: number; up: number; ahead: number; recline: number; diameter: number; tilt: number; grip: number;
  ratio: number; cap: number; axle: number; lateral: number }
export const PRESETS: Record<DriverClass, Preset> = {
  saloon: { hip: 0.52, up: 0.34, ahead: 0.48, recline: 22, diameter: 0.37, tilt: 25, grip: 70, ratio: 6.3, cap: 200, axle: 0.34, lateral: 0.37 },
  hatch: { hip: 0.55, up: 0.36, ahead: 0.46, recline: 18, diameter: 0.37, tilt: 25, grip: 70, ratio: 6.3, cap: 200, axle: 0.32, lateral: 0.36 },
  suv: { hip: 0.70, up: 0.38, ahead: 0.45, recline: 16, diameter: 0.38, tilt: 28, grip: 70, ratio: 6.3, cap: 200, axle: 0.36, lateral: 0.38 },
  van: { hip: 0.98, up: 0.40, ahead: 0.42, recline: 12, diameter: 0.40, tilt: 35, grip: 65, ratio: 8, cap: 250, axle: 0.08, lateral: 0.47 },
  truck: { hip: 1.55, up: 0.40, ahead: 0.42, recline: 12, diameter: 0.44, tilt: 40, grip: 65, ratio: 8, cap: 250, axle: -0.1, lateral: 0.62 },
  bus: { hip: 1.05, up: 0.42, ahead: 0.40, recline: 8, diameter: 0.48, tilt: 55, grip: 60, ratio: 10, cap: 300, axle: -0.25, lateral: 0.78 },
  race: { hip: 0.42, up: 0.33, ahead: 0.42, recline: 12, diameter: 0.35, tilt: 20, grip: 70, ratio: 6.3, cap: 200, axle: 0.36, lateral: 0.37 },
};

/** The posture class of a fleet model (body type and role family). Pickups steer like trucks (study: vans, pickups
 *  and the trophy truck share the truck ratio) but sit like SUVs. */
export function driverClass(m: Pick<FleetModel, 'body' | 'family'>): DriverClass {
  if (m.family === 'bus') return 'bus';
  if (m.family === 'truck' || m.family === 'fire') return 'truck';
  if (m.body === 'van') return 'van';
  if (m.body === 'suv' || m.body === 'pickup') return 'suv';
  if (m.body === 'hatch' || m.body === 'mpv' || m.body === 'microcar') return 'hatch';
  return 'saloon';
}

/** The character in its seated base pose (Driving_Loop, game/public/assets/character/player.glb), relative to its
 *  `DEF-hips` joint, in the seat frame (x left, y up, z forward). tests/unit/driver-rig.test.ts keeps these in step
 *  with the shipped GLB. */
export const BODY = {
  /** `DEF-upper_arm.L` joint (the right one mirrors it). */
  shoulder: [0.19, 0.52, -0.1] as V3,
  /** `DEF-spine.001` joint: the pivot the rig reclines the torso about. */
  lumbar: [0, 0.138, -0.007] as V3,
  /** Top of the hair. */
  crown: [0, 0.87, -0.03] as V3,
  /** Base pose torso angle behind vertical (hips to neck). */
  recline: 4.4 * DEG,
  upperArm: 0.274, forearm: 0.273,
  /** Wrist (`DEF-hand` joint) to the palm's grip centre. */
  palm: 0.085,
};

/** Comfortable shoulder-to-wrist distance at rest (elbow about 105°) and the reach limit the crossing arm may use at
 *  full lock (the clavicle adds a few centimetres, rig.ts). */
const REST_REACH = 0.43, MAX_REACH = BODY.upperArm + BODY.forearm + 0.012;
/** Headroom between the hair and the roof's outside (roof skin, headliner). */
const HEADROOM = 0.07;

const add = (a: V3, b: V3): V3 => [a[0] + b[0], a[1] + b[1], a[2] + b[2]];
const sub = (a: V3, b: V3): V3 => [a[0] - b[0], a[1] - b[1], a[2] - b[2]];
const scale = (a: V3, s: number): V3 => [a[0] * s, a[1] * s, a[2] * s];
const norm = (a: V3) => Math.hypot(a[0], a[1], a[2]);
/** Rotation about the seat frame's lateral (+X, left) axis; positive pitches forward (up toward +Z). */
const pitch = (v: V3, a: number): V3 => [v[0], v[1] * Math.cos(a) - v[2] * Math.sin(a), v[1] * Math.sin(a) + v[2] * Math.cos(a)];

/** The wheel frame: `n` toward the driver (back and up), `u` 12 o'clock, `l` the driver's left. */
export function wheelFrame(tilt: number) {
  return { n: [0, Math.sin(tilt), -Math.cos(tilt)] as V3, u: [0, Math.cos(tilt), Math.sin(tilt)] as V3, l: [1, 0, 0] as V3 };
}

/** The grip centre on the rim at `angle` from 12 o'clock (positive toward the driver's left), and the wrist target
 *  behind and outside it (the palm wraps the rim). */
export function grip(wheel: DriverLayout['wheel'], angle: number) {
  const { n, u, l } = wheelFrame(wheel.tilt);
  const radial = add(scale(u, Math.cos(angle)), scale(l, Math.sin(angle)));
  const point = add(wheel.centre, scale(radial, wheel.radius));
  const out = add(scale(n, 0.75), scale(radial, 0.65)), length = norm(out);
  return { point, wrist: add(point, scale(out, BODY.palm / length)) };
}

/** Shoulder joints for a layout's hips and recline (x mirrored for the right side). */
export function shoulders(hips: V3, recline: number): [V3, V3] {
  const extra = -(recline - BODY.recline);
  const at = (x: number) => add(add(hips, BODY.lumbar), pitch(sub([x, BODY.shoulder[1], BODY.shoulder[2]], BODY.lumbar), extra));
  return [at(BODY.shoulder[0]), at(-BODY.shoulder[0])];
}

export function crown(hips: V3, recline: number): V3 {
  return add(add(hips, BODY.lumbar), pitch(sub(BODY.crown, BODY.lumbar), -(recline - BODY.recline)));
}

interface Anchor { wheel: V3; diameter?: number }
const MODEL_ANCHORS = ANCHORS as unknown as Record<string, Anchor>;

/** The layout for a fleet model. The wheel centre is the model's own steering wheel where its source names one
 *  (anchors.json), otherwise the class position behind the front axle; the H-point follows from the wheel the way a
 *  driver sets the seat: the class height (lowered under a low roof) and the fore-aft distance that gives the arms
 *  their rest bend while the crossing arm still reaches the rim at full lock. */
export function fleetLayout(m: Pick<FleetModel, 'id' | 'body' | 'family' | 'height' | 'width' | 'wheelbase'> & { frontAxle: number }): DriverLayout {
  const cls = driverClass(m), p = PRESETS[cls], anchor = MODEL_ANCHORS[m.id];
  const recline = p.recline * DEG, tilt = p.tilt * DEG;
  const lateral = Math.min(p.lateral, m.width / 2 - 0.42);
  // Without a measured wheel: the class H-point height under the roof, the wheel up and ahead of it.
  const roofHip = m.height - HEADROOM - (crown([0, 0, 0], recline)[1]);
  const hipY = Math.min(p.hip, roofHip);
  const centre: V3 = anchor?.wheel ? [...anchor.wheel] : [lateral, hipY + p.up, m.frontAxle - p.axle * m.wheelbase];
  const diameter = anchor?.diameter ?? p.diameter;
  const wheel = { centre, radius: diameter / 2 - 0.016, tilt, grip: p.grip * DEG, ratio: p.ratio, cap: p.cap * DEG, drawn: true };
  const hips: V3 = [centre[0], Math.min(centre[1] - p.up, roofHip), centre[2] - p.ahead];
  hips[2] = seatDistance(hips, recline, wheel);
  return { cls, hips, recline, wheel };
}

/** The seat's fore-aft position (hips z) for a wheel: the rest grip at REST_REACH from the shoulder, moved forward as
 *  far as needed for the crossing arm to reach its grip at full lock (both hands stay on the rim, study §2). */
export function seatDistance(hips: V3, recline: number, wheel: DriverLayout['wheel']): number {
  const reachAt = (z: number, angle: number, side: 0 | 1) => {
    const shoulder = shoulders([hips[0], hips[1], z], recline)[side];
    return norm(sub(grip(wheel, angle).wrist, shoulder));
  };
  // Rest: solve |wrist - shoulder| = REST_REACH along z for the left hand (the right mirrors it).
  const rest = grip(wheel, wheel.grip).wrist, [shoulder] = shoulders([hips[0], hips[1], 0], recline);
  const dx = rest[0] - shoulder[0], dy = rest[1] - shoulder[1];
  let z = rest[2] - shoulder[2] - Math.sqrt(Math.max(0.01, REST_REACH ** 2 - dx * dx - dy * dy));
  // Every wheel angle to full lock either way: the outer (crossing) hand ends on the far side; move the seat in until
  // both hands reach their grips throughout.
  const angles: number[] = [];
  for (let a = -wheel.cap; a <= wheel.cap + 1e-9; a += wheel.cap / 12) angles.push(a);
  for (let i = 0; i < 40; i++) {
    let worst = 0;
    for (const a of angles) worst = Math.max(worst, reachAt(z, wheel.grip + a, 0), reachAt(z, -wheel.grip + a, 1));
    if (worst <= MAX_REACH) break;
    z += 0.01;
  }
  return z;
}

/** The steering wheel's angle (rad, positive counter-clockwise as the driver sees it: a left turn) for a mean front
 *  road-wheel angle `steer` (rad, + left). Linear to the cap (study §2: no hard stop before full lock). */
export function steeringAngle(wheel: Pick<DriverLayout['wheel'], 'ratio' | 'cap'>, steer: number): number {
  return Math.max(-wheel.cap, Math.min(wheel.cap, wheel.ratio * steer));
}

/** The trophy truck steers like a truck (study: k 8, ±250°); its own rig turns its `steer-wheel` bone by this. */
export const TRUCK_WHEEL = { ratio: PRESETS.truck.ratio, cap: PRESETS.truck.cap * DEG };

/** The trophy truck's driver (actors/truck.ts): its model names the seat (`seat-driver`, the H-point) and an
 *  articulated `steer-wheel` node turned by its own rig about its local Y axis (toward the driver). Positions and the
 *  axis are in the model's ground frame (+X forward, +Z right); the layout is in the fleet convention. SUV posture,
 *  truck ratio. The seat moves at most 12 cm fore-aft for the arms' reach. */
export function trophyDriverLayout(seat: { x: number; y: number; z: number }, wheel: { x: number; y: number; z: number },
  axis: { x: number; y: number; z: number }): DriverLayout {
  const p = PRESETS.suv, recline = p.recline * DEG;
  const tilt = Math.atan2(axis.y, Math.max(1e-3, -axis.x));
  const w = { centre: [-wheel.z, wheel.y, wheel.x] as V3, radius: 0.185, tilt, grip: p.grip * DEG, ...TRUCK_WHEEL, drawn: false };
  const hips: V3 = [-seat.z, seat.y, seat.x];
  hips[2] = Math.max(hips[2] - 0.12, Math.min(hips[2] + 0.12, seatDistance(hips, recline, w)));
  return { cls: 'suv', hips, recline, wheel: w };
}

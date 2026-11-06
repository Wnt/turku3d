// Ride of a slip-curve car (tuning.ride): springs, dampers and anti-roll bars from targets instead of raw Rapier
// coefficients, so every car with its own mass and geometry gets the same feel (Wreckfest 2 study,
// ~/turku3d/research/wreckfest2/physics-study.md "Turku3D tuning targets"; vehicle/README.md "Tyres and handling").
// Headless: vehicle/vehicle.ts sets Rapier's per-wheel suspension from `rideSetup` and applies `antiRoll` each step.
import type { VehicleTuning } from '../physics/constants.ts';

export interface RideTuning {
  /** Ride frequency (Hz) per axle (front, rear) at its static corner load. */
  readonly frequency: readonly [number, number];
  /** Damping ratio in bump (compression) and rebound (extension) per axle. */
  readonly bump: readonly [number, number];
  readonly rebound: readonly [number, number];
  /** Roll gradient (deg/g) about the contact patches, and the front axle's share of the roll stiffness (its share of
   *  the lateral load transfer): anti-roll bars add what each axle's springs lack. */
  readonly roll: number;
  readonly rollFront: number;
}

/** Rapier's per-wheel suspension (coefficients per unit chassis mass) and the anti-roll bars of the axles. `rest` and
 *  `travel` keep the static ride height and the bump travel of the base tuning: a softer spring sags more, so its rest
 *  length grows by the extra sag. */
export interface RideSetup {
  readonly wheels: readonly { stiffness: number; compression: number; relaxation: number; rest: number; travel: number }[];
  /** Per axle: the left and right wheel (chassis −z first) and the bar rate as force per metre of compression
   *  difference (N/m); the force on each wheel is rate × (its compression − the other's). */
  readonly bars: readonly { left: number; right: number; rate: number }[];
}

const G = 9.81;

/** The static corner loads (N) of a car from its centre of mass along the wheelbase (tuning.comAhead). */
export function cornerLoads(t: VehicleTuning, mass: number): number[] {
  const front = t.front, rear = t.wheels.map((_, i) => i).filter(i => !front.includes(i));
  const xf = front.reduce((a, i) => a + t.wheels[i].x, 0) / front.length, xr = rear.reduce((a, i) => a + t.wheels[i].x, 0) / rear.length;
  const share = Math.min(1, Math.max(0, ((t.comAhead ?? 0) - xr) / (xf - xr)));
  return t.wheels.map((_, i) => front.includes(i) ? share * mass * G / front.length : (1 - share) * mass * G / rear.length);
}

/** Height of the centre of mass above flat ground at static ride (m), from the base tuning's springs. */
export function comHeight(t: VehicleTuning, mass: number): number {
  const loads = cornerLoads(t, mass), i = t.front[0];
  return -t.wheels[i].y + t.rest - loads[i] / (t.suspensionStiffness * mass) + t.wheelRadius - t.comDrop;
}

export function rideSetup(t: VehicleTuning, mass: number): RideSetup | null {
  const r = t.ride;
  if (!r) return null;
  const loads = cornerLoads(t, mass), h = comHeight(t, mass);
  const wheels = t.wheels.map((_, i) => {
    const a = t.front.includes(i) ? 0 : 1, m = loads[i] / G, k = (2 * Math.PI * r.frequency[a]) ** 2 * m, critical = 2 * Math.sqrt(k * m);
    const sag = loads[i] / k - loads[i] / (t.suspensionStiffness * mass);
    return { stiffness: k / mass, compression: r.bump[a] * critical / mass, relaxation: r.rebound[a] * critical / mass,
      rest: t.rest + sag, travel: t.suspensionTravel + sag };
  });
  // Roll stiffness the target asks for: the whole weight's moment about the patches, m g h per g of lateral, plus the
  // weight's own moment as the body rolls (m g h per radian), which the springs and bars must also carry.
  const total = mass * G * h * (1 / (r.roll * Math.PI / 180) + 1);
  const rear = t.wheels.map((_, i) => i).filter(i => !t.front.includes(i));
  const bars = [t.front, rear].map((axle, a) => {
    const [left, right] = [...axle].sort((p, q) => t.wheels[p].z - t.wheels[q].z), track = t.wheels[right].z - t.wheels[left].z;
    // Springs on an axle resist roll with k (t/2)² each; a bar of roll rate K adds K / t² per metre of difference.
    const springs = (wheels[left].stiffness + wheels[right].stiffness) * mass * track * track / 4;
    const want = (a ? 1 - r.rollFront : r.rollFront) * total;
    return { left, right, rate: Math.max(0, want - springs) / (track * track) };
  });
  return { wheels, bars };
}

/** Anti-roll bar forces of one step (N, + pushes the chassis up at that wheel and loads its tyre), from the wheels'
 *  compressions; a bar acts only with both wheels down and cannot lift a tyre's load below zero. */
export function antiRoll(setup: RideSetup, compression: (i: number) => number | null, load: (i: number) => number, out: Float64Array) {
  out.fill(0);
  for (const b of setup.bars) {
    const l = compression(b.left), r = compression(b.right);
    if (l === null || r === null || b.rate <= 0) continue;
    let f = b.rate * (l - r);
    // Positive f: the left wheel is the more compressed; the right one cannot give up more than it carries.
    f = f > 0 ? Math.min(f, load(b.right)) : Math.max(f, -load(b.left));
    out[b.left] += f; out[b.right] -= f;
  }
}

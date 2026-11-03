/** A shared-clock pedestrian trajectory sample. Presence comes from population, never the render ring. */
export interface TrafficPedestrian { id: number; x: number; y: number; z: number; vx: number; vz: number; waiting?: boolean }
export interface PedestrianFrame { time: number; people: TrafficPedestrian[] }
/** A player's vehicle (the local driven car, remote players' cars): centre, unit heading, velocity and footprint. */
export interface TrafficVehicle { id: string; x: number; y: number; z: number; hx: number; hz: number; vx: number; vz: number;
  length: number; width: number }
export interface VehicleFrame { time: number; vehicles: TrafficVehicle[] }

interface Point { x: number; z: number; hx: number; hz: number }
/** First safe stop distance along a car's actual path, including its turn. Predict crossing arrivals so braking
 * begins before a walker steps into the lane, and leave a clear walking strip in front of the bumper. */
export function pedestrianStop(s: number, speed: number, length: number, width: number, roadY: number,
  t: number, frame: PedestrianFrame | null, point: (s: number) => Point,
  crossings: readonly { s: number; signal: boolean }[] = [], roadWidth = width): number {
  if (!frame || t - frame.time > 2) return Infinity;
  const here = point(s), reach = Math.max(12, speed * speed / 7 + speed * 0.7 + 5);
  const elapsed = Math.max(0, Math.min(0.75, t - frame.time));
  const near = frame.people.filter(p => (!Number.isFinite(roadY) || !Number.isFinite(p.y) || Math.abs(p.y - roadY) < 2.2)
    && Math.hypot(p.x - here.x, p.z - here.z) < reach + 5);
  if (!near.length) return Infinity;
  let stop = Infinity;
  for (const crossing of crossings) {
    if (crossing.s < s - length / 2 || crossing.s > s + reach) continue;
    const at = point(crossing.s);
    for (const p of near) {
      const dx = p.x + p.vx * elapsed - at.x, dz = p.z + p.vz * elapsed - at.z;
      const along = dx * at.hx + dz * at.hz, across = -dx * at.hz + dz * at.hx;
      const lateral = -p.vx * at.hz + p.vz * at.hx;
      const approaching = across * lateral < -0.12;
      const waiting = p.waiting && !crossing.signal;
      if (Math.abs(along) < 2 && Math.abs(across) < roadWidth / 2 + 1.5 && (approaching || waiting)) {
        stop = Math.min(stop, crossing.s - length / 2 - 1.4);
      }
    }
  }
  for (let distance = 0; distance <= reach; distance += 0.75) {
    const at = point(s + distance), eta = Math.min(3, distance / Math.max(2, speed));
    for (const p of near) {
      const x = p.x + p.vx * (elapsed + eta), z = p.z + p.vz * (elapsed + eta);
      const dx = x - at.x, dz = z - at.z;
      const along = dx * at.hx + dz * at.hz, across = -dx * at.hz + dz * at.hx;
      if (Math.abs(along) < 0.9 && Math.abs(across) < width / 2 + 0.65) return Math.min(stop, s + distance - length / 2 - 1.4);
    }
  }
  return stop;
}

/** Unit forward (+X of a physics chassis) of a rotation, flattened onto the ground plane. */
export function chassisForward(rot: { x: number; y: number; z: number; w: number }): { hx: number; hz: number } {
  const hx = 1 - 2 * (rot.y * rot.y + rot.z * rot.z), hz = 2 * (rot.x * rot.z - rot.w * rot.y), d = Math.hypot(hx, hz) || 1;
  return { hx: hx / d, hz: hz / d };
}

/** Separating-axis overlap of two oriented car footprints; `margin` pads the first one. */
export function bodyOverlap(a: { x: number; z: number; hx: number; hz: number }, sizeA: { width: number; length: number },
  b: { x: number; z: number; hx: number; hz: number }, sizeB: { width: number; length: number }, margin = 0) {
  const dx = b.x - a.x, dz = b.z - a.z;
  const reach = Math.hypot(sizeA.length / 2 + margin, sizeA.width / 2 + margin) + Math.hypot(sizeB.length / 2, sizeB.width / 2);
  if (Math.abs(dx) > reach || Math.abs(dz) > reach) return false;
  for (const [x, z] of [[a.hx, a.hz], [-a.hz, a.hx], [b.hx, b.hz], [-b.hz, b.hx]]) {
    const ra = Math.abs(a.hx * x + a.hz * z) * (sizeA.length / 2 + margin)
      + Math.abs(-a.hz * x + a.hx * z) * (sizeA.width / 2 + margin);
    const rb = Math.abs(b.hx * x + b.hz * z) * sizeB.length / 2 + Math.abs(-b.hz * x + b.hx * z) * sizeB.width / 2;
    if (Math.abs(dx * x + dz * z) >= ra + rb) return false;
  }
  return true;
}

/** Margin around the car's footprint (m) and the gap it stops short of a player's vehicle (m). */
const VEHICLE_MARGIN = 0.35, VEHICLE_GAP = 1.5;
/** First safe stop for the car's centre behind a player's vehicle predicted to occupy its path (a car parked across
 * the lane, one cutting in, an oncoming one in this lane). Only vehicles whose centre is ahead of the car's centre
 * count: a player tailgating the car, or beside it in the next lane, is not in its way. */
export function vehicleStop(s: number, speed: number, length: number, width: number, roadY: number,
  t: number, frame: VehicleFrame | null, point: (s: number) => Point): { stop: number; id: string | null } {
  const none = { stop: Infinity, id: null };
  if (!frame || !frame.vehicles.length || t - frame.time > 2) return none;
  const here = point(s), reach = Math.max(15, speed * speed / 7 + speed * 0.7 + 8);
  const elapsed = Math.max(0, Math.min(0.75, t - frame.time));
  const near = frame.vehicles.filter(v => (!Number.isFinite(roadY) || !Number.isFinite(v.y) || Math.abs(v.y - roadY) < 3)
    && Math.hypot(v.x - here.x, v.z - here.z) < reach + Math.max(v.length, length)
    && (v.x - here.x) * here.hx + (v.z - here.z) * here.hz > 0);
  if (!near.length) return none;
  const body = { length, width }, predicted = { x: 0, z: 0, hx: 1, hz: 0 };
  for (let distance = 0; distance <= reach; distance += 0.75) {
    const at = point(s + distance), eta = Math.min(3, distance / Math.max(2, speed));
    for (const v of near) {
      predicted.x = v.x + v.vx * (elapsed + eta); predicted.z = v.z + v.vz * (elapsed + eta);
      predicted.hx = v.hx; predicted.hz = v.hz;
      if (bodyOverlap(at, body, predicted, v, VEHICLE_MARGIN)) return { stop: s + distance - 0.75 - VEHICLE_GAP, id: v.id };
    }
  }
  return none;
}

/** A shared-clock pedestrian trajectory sample. Presence comes from population, never the render ring. */
export interface TrafficPedestrian { id: number; x: number; y: number; z: number; vx: number; vz: number; waiting?: boolean }
export interface PedestrianFrame { time: number; people: TrafficPedestrian[] }

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

// Swept 2D chassis boxes. Vertical separation rejects bridge/road contacts; the
// normal impulse has no lift or torque, and each participant's delta-v is capped.
export const TUNING = { car: { mass: 1200, half: [2.1, 0.3, 0.9] },
  scooter: { mass: 190, half: [1.02, 0.3, 0.3] }, truck: { mass: 2400, half: [2.35, 0.4, 1] } };

export function contactImpulse(a, b, horizon = 0.1) {
  const pa = a.pose, pb = b.pose;
  if (Math.abs(pa.H - pb.H) > a.half.y + b.half.y + 0.25) return null;
  const axes = p => [[-Math.sin(p.yaw), Math.cos(p.yaw)], [Math.cos(p.yaw), Math.sin(p.yaw)]];
  const aa = axes(pa), ab = axes(pb), delta = [pb.E - pa.E, pb.N - pa.N];
  const velocity = [pb.vE - pa.vE, pb.vN - pa.vN];
  const dot = (x, y) => x[0] * y[0] + x[1] * y[1];
  let enter = -Infinity, leave = horizon, normal = null, minimum = Infinity;
  for (const axis of [...aa, ...ab]) {
    const extent = a.half.x * Math.abs(dot(aa[0], axis)) + a.half.z * Math.abs(dot(aa[1], axis))
      + b.half.x * Math.abs(dot(ab[0], axis)) + b.half.z * Math.abs(dot(ab[1], axis));
    const distance = dot(delta, axis), speed = dot(velocity, axis), depth = extent - Math.abs(distance);
    if (depth < minimum) { minimum = depth; if (enter <= 0) normal = axis.map(x => x * (distance < 0 ? -1 : 1)); }
    if (Math.abs(speed) < 1e-6) { if (depth < 0) return null; continue; }
    const t1 = (-extent - distance) / speed, t2 = (extent - distance) / speed;
    const first = Math.min(t1, t2), last = Math.max(t1, t2);
    if (first > enter) {
      enter = first;
      if (first > 0) normal = axis.map(x => x * (distance + speed * first < 0 ? -1 : 1));
    }
    leave = Math.min(leave, last);
    if (enter > leave) return null;
  }
  if (!normal || leave < 0 || enter > horizon) return null;
  const closing = -dot(velocity, normal);
  if (closing < 0.2) return null;
  const ma = TUNING[pa.mode].mass, mb = TUNING[pb.mode].mass;
  const magnitude = Math.min(12000, 8 * Math.min(ma, mb), 1.05 * closing / (1 / ma + 1 / mb));
  return { E: normal[0] * magnitude, N: normal[1] * magnitude, magnitude };
}

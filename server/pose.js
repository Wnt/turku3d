const finite = (n, max) => typeof n === 'number' && Number.isFinite(n) && Math.abs(n) <= max;
/** `drv` of a driven 4WS truck: [throttle % 0..100, rpm/10 0..800, gear -1..3, rear mode 0..3], integers only. */
const DRV = [[0, 100], [0, 800], [-1, 3], [0, 3]];
export const validDrv = d => Array.isArray(d) && d.length === 4 && d.every((n, i) => Number.isInteger(n) && n >= DRV[i][0] && n <= DRV[i][1]);
/** `ctl` of a car: [throttle % -100..100, brake % 0..100, steer per mille -1000..1000, flags 0..7 (1 handbrake, 2 key
 *  steering, 4 driven)], integers only (game/src/net/motion.ts). `w`: angular velocity about E, N, H (rad/s). `st`: the
 *  room-clock time (ms) of the car state. Receivers simulate the car forward from them. */
const CTL = [[-100, 100], [0, 100], [-1000, 1000], [0, 7]];
export const validCtl = c => Array.isArray(c) && c.length === 4 && c.every((n, i) => Number.isInteger(n) && n >= CTL[i][0] && n <= CTL[i][1]);
export const validSpin = w => Array.isArray(w) && w.length === 3 && w.every(n => finite(n, 100));
const validQuat = q => !!q && ['x', 'y', 'z', 'w'].every(k => finite(q[k], 1)) && Math.abs(Math.hypot(q.x, q.y, q.z, q.w) - 1) <= 0.01;
/** `lead`: up to 4 other players' cars this player is the authority of in a contact (game/src/physics/world.ts
 *  REMOTE_SIM), each a shared vehicle id with its car state at room-clock time `st` (the units of a car pose). */
export const validLead = l => Array.isArray(l) && l.length <= 4 && l.every(e => e && typeof e === 'object'
  && typeof e.id === 'string' && e.id.length > 0 && e.id.length <= 128 && finite(e.st, Number.MAX_SAFE_INTEGER) && e.st >= 0
  && finite(e.E, 100_000) && finite(e.N, 100_000) && finite(e.H, 10_000) && validQuat(e.rot)
  && finite(e.vE, 200) && finite(e.vN, 200) && finite(e.vH, 200) && validSpin(e.w));
/** `damage` of a damaged car: the owner's compact body, mechanical, part, glass and surface state
 *  (game/src/vehicle/damage.ts encodeDamage, 516 bytes as 688 base64 characters; its decoder checks the version). The
 *  server only relays it, so it checks the form and a bound, not the layout; its shallow pose patches send it when it
 *  changes. */
export const validDamage = d => typeof d === 'string' && d.length <= 1024 && d.length % 4 === 0 && /^[A-Za-z0-9+/]+=?=?$/.test(d);
export function validPose(m) {
  if (!m || typeof m !== 'object' || Array.isArray(m)) return false;
  return !(!finite(m.t, Number.MAX_SAFE_INTEGER) || m.t < 0 || !finite(m.E, 100_000) || !finite(m.N, 100_000)
      || !finite(m.H, 10_000) || !finite(m.yaw, Math.PI * 2)
      || (m.rot !== undefined && (!m.rot || !['x', 'y', 'z', 'w'].every(k => finite(m.rot[k], 1))
        || Math.abs(Math.hypot(m.rot.x, m.rot.y, m.rot.z, m.rot.w) - 1) > 0.01))
      || (m.wheels !== undefined && (!Array.isArray(m.wheels) || m.wheels.length > 4 || m.wheels.some(w =>
        !w || !w.pos || !['x', 'y', 'z'].every(k => finite(w.pos[k], 10)) || !finite(w.steer, Math.PI * 2)
        || !finite(w.rotation, Number.MAX_SAFE_INTEGER) || !finite(w.suspension, 10) || typeof w.contact !== 'boolean')))
      || (m.lights !== undefined && ![0, 1, 2].includes(m.lights))
      || (m.horn !== undefined && typeof m.horn !== 'boolean')
      || (m.drv !== undefined && !validDrv(m.drv))
      || (m.ctl !== undefined && !validCtl(m.ctl))
      || (m.w !== undefined && !validSpin(m.w))
      || (m.st !== undefined && (!finite(m.st, Number.MAX_SAFE_INTEGER) || m.st < 0))
      || (m.lead !== undefined && !validLead(m.lead))
      || (m.damage !== undefined && !validDamage(m.damage))
      || !['vE', 'vN', 'vH'].every(k => finite(m[k], 200))
      || !['foot', 'car', 'scooter', 'truck'].includes(m.mode) || typeof m.carrying !== 'boolean'
      || !(m.mode === 'foot' ? m.vehicle === null : m.mode === 'scooter' || m.mode === 'truck' ? m.vehicle === m.mode
        : m.vehicle === 'player' || (typeof m.vehicle === 'string' && m.vehicle.length <= 64
          && /^[a-z0-9-]+\/[a-z0-9-]+$/.test(m.vehicle))));
}
// Millimetre chassis/wheel precision and sub-milliradian orientation keep JSON
// traffic bounded; arbitrary client object fields never enter room snapshots.
const rounded = n => Math.round(n * 1000) / 1000;
const vector = v => ({ x: rounded(v.x), y: rounded(v.y), z: rounded(v.z) });
export function cleanPose(m) {
  const { t, E, N, H, yaw, vE, vN, vH, mode, vehicle, carrying, rot, wheels } = m;
  return { t: Math.round(t), E: rounded(E), N: rounded(N), H: rounded(H), yaw: rounded(yaw),
    vE: rounded(vE), vN: rounded(vN), vH: rounded(vH), mode, vehicle, carrying, lights: m.lights ?? 0, horn: m.horn === true,
    ...(rot ? { rot: { ...vector(rot), w: rounded(rot.w) } } : {}),
    ...(validDrv(m.drv) ? { drv: [...m.drv] } : {}),
    ...(validCtl(m.ctl) ? { ctl: [...m.ctl] } : {}),
    ...(validSpin(m.w) ? { w: m.w.map(rounded) } : {}),
    ...(typeof m.st === 'number' && Number.isFinite(m.st) && m.st >= 0 ? { st: Math.round(m.st) } : {}),
    ...(validLead(m.lead) && m.lead.length ? { lead: m.lead.map(e => ({ id: e.id, st: Math.round(e.st), E: rounded(e.E), N: rounded(e.N), H: rounded(e.H),
      rot: { ...vector(e.rot), w: rounded(e.rot.w) }, vE: rounded(e.vE), vN: rounded(e.vN), vH: rounded(e.vH), w: e.w.map(rounded) })) } : {}),
    ...(validDamage(m.damage) ? { damage: m.damage } : {}),
    ...(wheels ? { wheels: wheels.map(({ pos, steer, rotation, contact, suspension, tyre }) => ({
      pos: vector(pos), steer: rounded(steer), rotation: rounded(rotation), contact, suspension: rounded(suspension),
      ...(tyre && ['x', 'y', 'z'].every(k => finite(tyre.point?.[k], 10) && finite(tyre.normal?.[k], 1))
        && finite(tyre.material, 255) && finite(tyre.lateral, 200) && finite(tyre.longitudinal, 200)
        ? { tyre: { point: vector(tyre.point), normal: vector(tyre.normal), material: tyre.material,
          lateral: rounded(tyre.lateral), longitudinal: rounded(tyre.longitudinal) } } : {}),
    })) } : {}) };
}

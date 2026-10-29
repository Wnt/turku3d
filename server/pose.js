const finite = (n, max) => typeof n === 'number' && Number.isFinite(n) && Math.abs(n) <= max;
export function validPose(m) {
  if (!m || typeof m !== 'object' || Array.isArray(m)) return false;
  return !(!finite(m.t, Number.MAX_SAFE_INTEGER) || m.t < 0 || !finite(m.E, 100_000) || !finite(m.N, 100_000)
      || !finite(m.H, 10_000) || !finite(m.yaw, Math.PI * 2)
      || (m.rot !== undefined && (!m.rot || !['x', 'y', 'z', 'w'].every(k => finite(m.rot[k], 1))
        || Math.abs(Math.hypot(m.rot.x, m.rot.y, m.rot.z, m.rot.w) - 1) > 0.01))
      || (m.wheels !== undefined && (!Array.isArray(m.wheels) || m.wheels.length > 4 || m.wheels.some(w =>
        !w || !w.pos || !['x', 'y', 'z'].every(k => finite(w.pos[k], 10)) || !finite(w.steer, Math.PI * 2)
        || !finite(w.rotation, Number.MAX_SAFE_INTEGER) || !finite(w.suspension, 10) || typeof w.contact !== 'boolean')))
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
    vE: rounded(vE), vN: rounded(vN), vH: rounded(vH), mode, vehicle, carrying,
    ...(rot ? { rot: { ...vector(rot), w: rounded(rot.w) } } : {}),
    ...(wheels ? { wheels: wheels.map(({ pos, steer, rotation, contact, suspension, tyre }) => ({
      pos: vector(pos), steer: rounded(steer), rotation: rounded(rotation), contact, suspension: rounded(suspension),
      ...(tyre && ['x', 'y', 'z'].every(k => finite(tyre.point?.[k], 10) && finite(tyre.normal?.[k], 1))
        && finite(tyre.material, 255) && finite(tyre.lateral, 200) && finite(tyre.longitudinal, 200)
        ? { tyre: { point: vector(tyre.point), normal: vector(tyre.normal), material: tyre.material,
          lateral: rounded(tyre.lateral), longitudinal: rounded(tyre.longitudinal) } } : {}),
    })) } : {}) };
}

const finite = (n, max) => typeof n === 'number' && Number.isFinite(n) && Math.abs(n) <= max;
export function validPose(m) {
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
export function cleanPose(m) {
  const { t, E, N, H, yaw, vE, vN, vH, mode, vehicle, carrying, rot, wheels } = m;
  return { t, E, N, H, yaw, vE, vN, vH, mode, vehicle, carrying,
    ...(rot ? { rot } : {}), ...(wheels ? { wheels } : {}) };
}

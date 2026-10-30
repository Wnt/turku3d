// Physical throttle/steering only during measurement. The harness places each car once on its initial street.
export function installCarDrive({ target, seconds = 75 }) {
  const g = window.__game, original = g.input.update.bind(g.input);
  const result = window.__carDrive = { samples: [], crossings: [], done: false, startedAt: Date.now(),
    travelled: 0, missingDetailMs: 0, missingVisualMs: 0, missingCollisionMs: 0, resets: 0, peakSpeed: 0 };
  g.router.mode = 'car';
  const route = g.router.route({ x: g.state().feet[0], z: g.state().feet[2] }, target);
  if (!route || route.length < 2) throw new Error('No driving route');
  result.route = route;
  const textureCounts = new WeakMap();
  let previous, previousTime, lastTile, sampleAt = 0, firstSegment = 0;
  const start = performance.now();
  g.input.update = dt => {
    original(dt);
    for (const key of Object.keys(g.input.actions)) g.input.actions[key] = 0;
    g.input.lookX = g.input.lookY = g.input.zoom = 0;
    const s = g.state(), [x, , z] = s.feet, t = performance.now();
    if (s.mode !== 'car') throw new Error('Physical drive left the car');
    let nearest = Infinity, position, index = firstSegment;
    for (let i = firstSegment; i + 1 < route.length; i++) {
      const a = route[i], b = route[i + 1], dx = b.x - a.x, dz = b.z - a.z;
      const fraction = Math.max(0, Math.min(1, ((x - a.x) * dx + (z - a.z) * dz) / (dx * dx + dz * dz || 1)));
      const point = { x: a.x + dx * fraction, z: a.z + dz * fraction }, d = Math.hypot(point.x - x, point.z - z);
      if (d < nearest) { nearest = d; position = point; index = i; }
    }
    firstSegment = index;
    const remaining = [position, ...route.slice(index + 1)];
    const speed = Math.abs(s.vehicle.forwardSpeed), q = s.vehicle.rot;
    let distance = 6 + speed * .5, goal = remaining.at(-1);
    for (let i = 1; i < remaining.length; i++) {
      const a = remaining[i - 1], b = remaining[i], length = Math.hypot(b.x - a.x, b.z - a.z);
      if (distance <= length) { goal = { x: a.x + (b.x - a.x) * distance / length, z: a.z + (b.z - a.z) * distance / length }; break; }
      distance -= length;
    }
    const yaw = Math.atan2(2 * (q.w * q.y + q.x * q.z), 1 - 2 * (q.y * q.y + q.z * q.z));
    const desired = Math.atan2(-(goal.z - z), goal.x - x);
    const error = Math.atan2(Math.sin(desired - yaw), Math.cos(desired - yaw));
    let corner = false, reach = 0;
    for (let i = 1; i + 1 < remaining.length; i++) {
      reach += Math.hypot(remaining[i].x - remaining[i - 1].x, remaining[i].z - remaining[i - 1].z);
      if (reach > 25) break;
      const a = Math.atan2(remaining[i].z - remaining[i - 1].z, remaining[i].x - remaining[i - 1].x);
      const b = Math.atan2(remaining[i + 1].z - remaining[i].z, remaining[i + 1].x - remaining[i].x);
      if (Math.abs(Math.atan2(Math.sin(b - a), Math.cos(b - a))) > .5) corner = true;
    }
    const targetSpeed = Math.abs(error) > .6 ? 4 : corner || Math.abs(error) > .2 ? 7 : 14;
    g.input.actions.steer = Math.max(-1, Math.min(1, -error * 2.5));
    g.input.actions.throttle = speed < targetSpeed ? .8 : 0;
    g.input.actions.brake = speed > targetSpeed + 1 ? .4 : 0;
    const elapsed = previousTime === undefined ? 0 : t - previousTime;
    const id = `t0_${Math.floor(x / 250)}_${Math.floor(z / 250)}`, entry = g.coarse.entries.get(id);
    const visual = !!entry?.model?.visible, collision = g.streamer.supports(x, z, 2);
    if (entry?.model && !textureCounts.has(entry.model)) {
      const textures = new Set();
      entry.model.traverse(object => {
        for (const material of Array.isArray(object.material) ? object.material : [object.material]) {
          for (const value of Object.values(material ?? {})) if (value?.isTexture) textures.add(value);
        }
      });
      textureCounts.set(entry.model, textures.size);
    }
    const textures = entry?.model ? textureCounts.get(entry.model) : 0;
    const detailed = visual && !entry.preview && entry.pass === 'full' && textures > 0;
    if (!visual) result.missingVisualMs += elapsed;
    if (!detailed) result.missingDetailMs += elapsed;
    if (!collision) result.missingCollisionMs += elapsed;
    if (previous) {
      const moved = Math.hypot(x - previous[0], z - previous[2]);
      if (moved > 20) result.resets++; else result.travelled += moved;
    }
    previous = s.feet; previousTime = t; result.peakSpeed = Math.max(result.peakSpeed, speed);
    const sample = { at: Date.now(), feet: s.feet, speed, id, visual, detailed, collision, offRoute: nearest,
      preview: !!entry?.preview, pass: entry?.pass, textures, frameMs: s.frameMs, multiplayer: s.multiplayer?.players, recovery: s.recovery };
    if (id !== lastTile) { result.crossings.push(sample); lastTile = id; }
    if (t >= sampleAt) { result.samples.push(sample); sampleAt = t + 250; }
    if (t - start >= seconds * 1000 || Math.hypot(route.at(-1).x - x, route.at(-1).z - z) < 10) {
      result.done = true; result.elapsedMs = t - start;
      result.summary = { travelled: result.travelled, elapsedMs: result.elapsedMs, peakKmh: result.peakSpeed * 3.6,
        averageKmh: result.travelled / (result.elapsedMs / 1000) * 3.6, crossings: Math.max(0, result.crossings.length - 1),
        missingDetailMs: result.missingDetailMs, missingVisualMs: result.missingVisualMs,
        missingCollisionMs: result.missingCollisionMs, resets: result.resets };
      g.input.update = original;
      g.input.actions.throttle = 0; g.input.actions.brake = 1;
    }
  };
  return { route, target, seconds };
}

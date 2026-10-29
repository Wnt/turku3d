// CDP-only measurement hooks: drive the real developer flight inputs and normal streamers.
export function installFlight(route) {
  const g = window.__game, start = g.state().feet;
  const f = window.__flight = { start, at: Date.now(), phase: 'climb', events: [], frames: [], samples: [], work: [], target: 0, seen: {}, done: false };
  const now = () => performance.timeOrigin + performance.now();
  const event = (kind, data = {}) => f.events.push({ at: now(), kind, ...data });
  const instrument = (owner, method, label, id = () => null) => {
    const original = owner[method];
    if (!original) return;
    owner[method] = function (...args) {
      const at = now(), key = id(args);
      const finish = () => f.work.push({ at, ms: now() - at, label, key });
      try { const value = original.apply(this, args); if (value?.then) return value.finally(finish); finish(); return value; }
      catch (error) { finish(); throw error; }
    };
  };
  for (const method of ['load', 'applyFragment', 'prepareModel', 'sendDemand']) instrument(g.coarse, method, method, args => typeof args[0] === 'string' ? args[0] : args[0]?.urgent);
  for (const method of ['loadTile', 'checkTile']) instrument(g.physics, method, method, args => typeof args[0] === 'string' ? args[0] : args[0]?.byteLength);
  instrument(window.__render.renderer, 'initTexture', 'texture-upload');
  const input = g.input.update.bind(g.input);
  let tap = 0, lastTap = -Infinity, last = performance.now(), sampleAt = 0, turnAt = 0;
  const { targets, dx, dz, edge, sign, axis } = route;
  const distance = feet => sign * (feet[axis] - start[axis]);
  const firstEdge = sign * (edge - start[axis]);
  const ready = tile => {
    const e = g.coarse.entries.get(tile);
    return !!e?.model && e.collision.size === 16;
  };
  g.input.update = dt => {
    input(dt);
    for (const key of Object.keys(g.input.actions)) g.input.actions[key] = 0;
    g.input.lookX = g.input.lookY = g.input.zoom = 0;
    const state = g.state(), [x, y, z] = state.feet, t = performance.now();
    if (tap < 2 && t - lastTap > 120) { g.input.edges.add('jump'); tap++; lastTap = t; }
    g.orbit.yaw = Math.atan2(-dx, -dz) + (f.phase === 'return' ? Math.PI : 0); g.orbit.pitch = -0.22;
    if (!state.fly) return;
    // FlyMode.speed is measured from the actual implementation: 48 * SPEED.walk = 72 m/s.
    g.input.actions.flyUp = Math.max(0, Math.min(0.3, (70 - y) / 72));
    g.input.actions.flyDown = Math.max(0, Math.min(0.3, (y - 70) / 72));
    if (f.phase === 'climb' && y >= 69) { f.phase = 'outbound'; event('outbound', { feet: state.feet }); }
    let speed = 0;
    if (f.phase === 'outbound') {
      const crossing = firstEdge + f.target * 250, travelled = distance(state.feet);
      speed = travelled < crossing - 100 ? 100 / 3.6 : 50 / 3.6;
      const ahead = { x: x + dx * 6, z: z + dz * 6 };
      if (travelled >= crossing - 4 && (!ready(targets[f.target]) || !g.streamer.supports(ahead.x, ahead.z))) speed = 0;
      if (travelled >= crossing && f.target === 0) { event('crossing', { tile: targets[0], feet: state.feet }); f.target = 1; }
      if (travelled >= firstEdge + 430) { f.phase = 'settle'; event('crossing', { tile: targets[1], feet: state.feet }); speed = 0; turnAt = t; }
    } else if (f.phase === 'settle') {
      if (targets.every(id => g.coarse.entries.get(id)?.pass === 'full') || t - turnAt > 90000) {
        f.phase = 'return'; event('turn', { feet: state.feet, full: targets.map(id => g.coarse.entries.get(id)?.pass) });
      }
    } else if (f.phase === 'return') {
      speed = 100 / 3.6;
      if (distance(state.feet) <= 2) { f.phase = 'returned'; speed = 0; event('returned', { feet: state.feet }); turnAt = t; }
    } else if (f.phase === 'returned' && t - turnAt > 12000) { f.done = true; f.phase = 'done'; event('done'); }
    g.input.actions.moveY = speed / 72;
    f.speedKmh = speed * 3.6;
    if (Math.abs((x - start[0]) * dz - (z - start[2]) * dx) > 5) event('lateral-drift', { x, z });
  };
  const tick = t => {
    f.frames.push([now(), t - last]); last = t;
    if (t - sampleAt > 500) {
      const state = g.state();
      const entries = [...g.coarse.entries].map(([id, e]) => ({ id, state: e.state, pass: e.pass, model: !!e.model, collision: e.collision.size, hash: e.visualHash, stale: e.stale, error: e.error }));
      for (const e of entries) {
        const key = `${e.id}:${e.pass}:${e.hash}`;
        if (e.model && !f.seen[key]) { f.seen[key] = now(); event('visual-collision', e); }
      }
      f.samples.push({ at: now(), phase: f.phase, speedKmh: f.speedKmh, feet: state.feet, frameMs: state.frameMs,
        entries, loaded: [...g.physics.loaded.keys()], known: [...g.streamer.known], pending: g.streamer.pending,
        velocity: g.coarse.velocity, physics: { tick: g.physics.latest?.tick, stepMs: g.physics.latest?.stepMs,
          age: now() - (g.physics.latest?.publishedAt ?? now()) }, models: state.visModels, tris: state.visTris,
        heap: performance.memory?.usedJSHeapSize, errors: state.errors }); sampleAt = t;
    }
    if (!f.done) requestAnimationFrame(tick);
  };
  requestAnimationFrame(tick); event('installed', { start, targets });
  return { start, targets };
}

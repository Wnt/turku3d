// Measure actual maximum-speed flight without stopping at missing terrain or changing server caches.
export function installWarmFlight({ dx, dz, metres, speed }) {
  const g = window.__game, origin = g.state().feet;
  const result = window.__warmFlight = { phase: 'climb', samples: [], crossings: [], work: [], done: false };
  const epoch = () => performance.timeOrigin + performance.now();
  const input = g.input.update.bind(g.input);
  let taps = g.state().fly ? 2 : 0, lastTap = -Infinity, start, lastTile, sampleAt = 0;
  let previousAt, missingVisualMs = 0, missingCollisionMs = 0, totalMs = 0;
  const restores = [];
  result.decode = [];
  const workers = new WeakSet(), jobs = new Map(), post = Worker.prototype.postMessage;
  Worker.prototype.postMessage = function (data, ...rest) {
    if (data?.buffer instanceof ArrayBuffer && typeof data.id === 'number') {
      const job = { id: data.id, at: epoch(), bytes: data.buffer.byteLength, rows: 0, replies: 0 };
      jobs.set(data.id, job); result.decode.push(job);
      if (!workers.has(this)) {
        workers.add(this);
        this.addEventListener('message', ({ data }) => {
          const job = jobs.get(data?.id);
          if (!job || !data.rows) return;
          job.rows += data.rows.length; job.replies++; job.ms = epoch() - job.at; job.done = !!data.done;
        });
      }
    }
    return post.call(this, data, ...rest);
  };
  restores.push(() => { Worker.prototype.postMessage = post; });
  for (const method of ['style', 'styled']) {
    const original = g.coarse.host[method];
    if (!original) continue;
    g.coarse.host[method] = async (...args) => {
      const at = epoch();
      try { return await original(...args); }
      finally { result.work.push({ method, at, ms: epoch() - at }); }
    };
    restores.push(() => { g.coarse.host[method] = original; });
  }
  for (const method of ['poll', 'load', 'prepareModel', 'applyFragment', 'sendDemand']) {
    const original = g.coarse[method];
    if (!original) continue;
    g.coarse[method] = function (...args) {
      const at = epoch();
      const finish = () => result.work.push({ method, id: typeof args[0] === 'string' ? args[0] : null, at, ms: epoch() - at });
      try { const value = original.apply(this, args); if (value?.then) return value.finally(finish); finish(); return value; }
      catch (e) { finish(); throw e; }
    };
    restores.push(() => { g.coarse[method] = original; });
  }
  g.input.update = dt => {
    input(dt);
    for (const key of Object.keys(g.input.actions)) g.input.actions[key] = 0;
    g.input.lookX = g.input.lookY = g.input.zoom = 0;
    const state = g.state(), [x, y, z] = state.feet, t = performance.now();
    if (taps < 2 && t - lastTap > 120) { g.input.edges.add('jump'); taps++; lastTap = t; }
    g.orbit.yaw = Math.atan2(-dx, -dz) + (result.phase === 'return' ? Math.PI : 0);
    g.orbit.pitch = -0.35;
    if (!state.fly) return;
    if (result.phase === 'climb') {
      g.input.actions.flyUp = Math.max(0, Math.min(1, (90 - y) / (speed / 4)));
      if (y < 89) return;
      start = [x, y, z]; result.start = start; result.startedAt = epoch(); result.phase = 'outbound';
    }
    const distance = (x - start[0]) * dx + (z - start[2]) * dz;
    if (result.phase === 'outbound' && distance >= metres) result.phase = 'return';
    else if (result.phase === 'return' && distance <= 0) {
      result.done = true; result.phase = 'done';
      result.summary = { totalMs, missingVisualMs, missingCollisionMs,
        crossings: result.crossings.length, missingCrossings: result.crossings.filter(c => !c.visual).length };
      g.input.update = input; restores.forEach(restore => restore());
      return;
    }
    g.input.actions.moveY = 1;
    g.input.actions.sprint = 1;
    const id = `t0_${Math.floor(x / 250)}_${Math.floor(z / 250)}`, entry = g.coarse.entries.get(id);
    const visual = !!entry?.model && entry.model.visible, collision = g.streamer.supports(x, z);
    const elapsed = previousAt === undefined ? 0 : t - previousAt;
    totalMs += elapsed; if (!visual) missingVisualMs += elapsed; if (!collision) missingCollisionMs += elapsed;
    previousAt = t;
    const sample = { at: epoch(), phase: result.phase, feet: state.feet, id, visual, collision,
      predictedVelocity: { ...g.coarse.velocity }, frameMs: state.frameMs,
      entries: [...g.coarse.entries].map(([id, e]) => ({ id, state: e.state, pass: e.pass, model: !!e.model, error: e.error })),
      pendingCollision: g.streamer.pending, loadedCollision: g.physics.loaded.size };
    if (id !== lastTile) { result.crossings.push(sample); lastTile = id; }
    if (t >= sampleAt) { result.samples.push(sample); sampleAt = t + 100; }
  };
  return { origin, speed, metres };
}

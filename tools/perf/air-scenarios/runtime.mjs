// Injected through CDP only. No gameplay import, query flag or production entry references this module.
export function installScenario({ flightSeconds = 75 } = {}) {
  const game = window.__game, camera = window.__render.camera;
  const scene = game.tiles.models.values().next().value.parent;
  const previousRender = scene.onBeforeRender, previousInput = game.input.update.bind(game.input);
  const initialCar = game.state().vehicles.find(vehicle => vehicle.id === 'player');
  const points = [[180, 8, 270], [20, 8, 340], [-100, 8, 410], [-230, 8, 485], [-390, 8, 550],
    [-330, 8, 630], [-190, 8, 550], [-50, 8, 475], [70, 8, 400], [180, 8, 270]];
  const lengths = [0];
  for (let i = 1; i < points.length; i++) lengths.push(lengths[i - 1] + Math.hypot(points[i][0] - points[i - 1][0], points[i][2] - points[i - 1][2]));
  function at(u) {
    const distance = Math.min(1, Math.max(0, u)) * lengths.at(-1);
    let i = 1; while (i < points.length - 1 && lengths[i] < distance) i++;
    const t = (distance - lengths[i - 1]) / (lengths[i] - lengths[i - 1]);
    return points[i - 1].map((v, axis) => v + (points[i][axis] - v) * t);
  }
  const state = window.__air = { phase: flightSeconds > 0 ? 'flight' : 'prepare-drive', started: performance.now(), frames: [], poses: [], points,
    flightSeconds, work: [], physics: [], materialKeys: 0, shadowKeys: 0, inFrame: false, builders: [], enterPending: false, target: 1, route: [], active: true, driveStart: null, driveEnd: null };
  const perf = window.__render.perf, renderer = window.__render.renderer;
  const beforeFrame = perf.begin.bind(perf), afterFrame = perf.end.bind(perf);
  let lastPhysics = performance.now();
  const onPhysics = event => {
    if (event.data?.type !== 'state') return;
    const now = performance.now(), message = event.data, probe = window.__prof;
    lastPhysics = now;
    const sent = probe?.sent.get(message.seq);
    state.physics.push([now, sent === undefined ? null : now - sent, message.stepMs, state.phase]);
    if (sent !== undefined) { probe.rtt.push(now - sent); probe.stepMs.push(message.stepMs); probe.sent.delete(message.seq); }
  };
  game.physics.worker.addEventListener('message', onPhysics);
  const previousBuilder = renderer.debug.onNodeBuilderCreated;
  const restoreWork = [];
  function instrument(owner, name, kind, restore = true) {
    const original = owner[name];
    owner[name] = function (...args) {
      const start = performance.now();
      try { return original.apply(this, args); }
      finally { state.work.push({ start, ms: performance.now() - start, kind, phase: state.phase }); }
    };
    if (restore) restoreWork.push(() => { owner[name] = original; });
  }
  instrument(renderer, 'initTexture', 'texture-upload');
  instrument(game.tiles, 'trackModel', 'shadow-batch-and-publication');
  const sample = renderer._objects._renderObjects.values().next().value;
  const renderObjectPrototype = Object.getPrototypeOf(sample);
  const materialKey = renderObjectPrototype.getMaterialCacheKey;
  renderObjectPrototype.getMaterialCacheKey = function () {
    state.materialKeys++; if (this.material.isShadowPassMaterial) state.shadowKeys++;
    return materialKey.call(this);
  };
  perf.begin = () => { state.inFrame = true; beforeFrame(); };
  perf.end = () => { afterFrame(); state.inFrame = false; };
  renderer.debug.onNodeBuilderCreated = (builder, object) => {
    previousBuilder?.(builder, object);
    instrument(builder, 'build', 'synchronous-node-build', false);
    state.builders.push({ now: performance.now(), inFrame: state.inFrame, phase: state.phase,
      object: object.object.name, material: object.material.name, receiveShadow: object.object.receiveShadow,
      target: renderer.getRenderTarget()?.uuid, mrt: renderer.getMRT()?.uuid,
      reflection: object.camera.layers.mask === 4 && !object.material.isShadowPassMaterial });
  };
  let pose = at(0), aim = at(0.025), lastTeleport = -Infinity, previous = null, lastPose = 0, lastEnter = -Infinity;
  scene.onBeforeRender = function (...args) {
    previousRender.apply(this, args);
    if ((state.phase !== 'flight' && state.phase !== 'inspection') || args[2] !== camera) return;
    camera.position.set(...pose); camera.lookAt(aim[0], 4, aim[2]); camera.updateMatrixWorld();
  };
  game.input.update = dt => {
    previousInput(dt);
    for (const key of Object.keys(game.input.actions)) game.input.actions[key] = 0;
    game.input.lookX = game.input.lookY = game.input.zoom = 0;
    if (state.phase === 'prepare-drive' && state.tryEntering && performance.now() - lastEnter > 2000) {
      const current = game.state();
      if (current.mode === 'foot' && current.grounded && !game.recovery.lost) { state.enterPending = true; lastEnter = performance.now(); }
    }
    if (state.enterPending) { game.input.edges.add('interact'); game.input.actions.interact = 1; state.enterPending = false; }
    if (state.phase !== 'drive') return;
    const car = game.physics.latest?.vehicles.find(vehicle => vehicle.id === 'player');
    if (!car || !state.route.length) return;
    while (state.target < state.route.length - 1 && Math.hypot(state.route[state.target].x - car.pos.x, state.route[state.target].z - car.pos.z) < 8) state.target++;
    const goal = state.route[state.target], q = car.rot;
    const desired = Math.atan2(-(goal.z - car.pos.z), goal.x - car.pos.x);
    const heading = Math.atan2(-2 * (q.x * q.z - q.w * q.y), 1 - 2 * (q.y * q.y + q.z * q.z));
    const error = Math.atan2(Math.sin(desired - heading), Math.cos(desired - heading));
    game.input.actions.throttle = car.speed < 7 ? 0.4 : 0;
    game.input.actions.brake = car.speed > 8 ? 0.2 : 0;
    game.input.actions.steer = Math.max(-0.45, Math.min(0.45, -error * 1.7));
  };
  function tick(now) {
    if (!state.active) return;
    const latest = game.physics.latest;
    const publishedAge = latest?.publishedAt === undefined ? null : Math.max(0, performance.timeOrigin + performance.now() - latest.publishedAt);
    if (previous !== null) state.frames.push([now, now - previous, state.phase, Math.max(0, performance.now() - lastPhysics), publishedAge, latest?.tick]);
    previous = now;
    if (state.phase === 'flight') {
      const u = Math.min(1, (now - state.started) / (flightSeconds * 1000));
      pose = at(u); aim = at(Math.min(1, u + 0.025));
      if (now - lastTeleport >= 300) { game.physics.setCharacterEnabled(true, { x: pose[0], y: 8, z: pose[2] }); lastTeleport = now; }
      if (u === 1) state.phase = 'prepare-drive';
    }
    if (now - lastPose >= 1000) { state.poses.push({ now, phase: state.phase, heap: performance.memory?.usedJSHeapSize,
      materialKeys: state.materialKeys, shadowKeys: state.shadowKeys, scale: game.resolution?.scale, canvas: [camera.aspect, window.__render.renderer.domElement.width, window.__render.renderer.domElement.height], state: game.state() }); lastPose = now; }
    requestAnimationFrame(tick);
  }
  requestAnimationFrame(tick);
  state.prepareDrive = async () => {
    state.phase = 'prepare-drive';
    const pos = initialCar.pos;
    game.physics.setCharacterEnabled(true, { x: pos[0] - 2, y: pos[1] - 0.7, z: pos[2] });
    const cell = `c_${Math.floor(pos[0] / 62.5)}_${Math.floor(pos[2] / 62.5)}`;
    const deadline = performance.now() + 30000;
    while (!game.physics.loaded.has(cell)) {
      if (performance.now() > deadline) throw new Error(`Drive start collision unavailable: ${cell}`);
      await new Promise(resolve => setTimeout(resolve, 100));
    }
    game.physics.resetVehicle({ x: pos[0], y: pos[1] + 0.1, z: pos[2] }, 2 * Math.atan2(initialCar.rot.y, initialCar.rot.w));
    await game.physics.stats();
    state.tryEntering = true;
  };
  state.beginDrive = () => {
    const car = game.state().vehicles.find(vehicle => vehicle.id === 'player');
    game.router.mode = 'car';
    let from = { x: car.pos[0], z: car.pos[2] };
    for (const [x, z] of [[-25, 350], [-390, 550], [-330, 630], [70, 400]]) {
      const part = game.router.route(from, { x, z });
      if (part?.length) { state.route.push(...part); from = part.at(-1); }
    }
    if (!state.route.length) throw new Error('No car route in the loaded street graph');
    const heading = state.route.find(point => Math.hypot(point.x - car.pos[0], point.z - car.pos[2]) > 5) ?? state.route[1];
    const yaw = Math.atan2(-(heading.z - car.pos[2]), heading.x - car.pos[0]);
    game.physics.resetVehicle({ x: car.pos[0], y: car.pos[1] + 0.1, z: car.pos[2] }, yaw);
    state.driveStart = performance.now(); state.phase = 'drive';
    return { route: state.route, state: game.state() };
  };
  state.stop = () => { state.driveEnd = performance.now(); state.active = false; for (const restore of restoreWork) restore(); scene.onBeforeRender = previousRender; game.input.update = previousInput; perf.begin = beforeFrame; perf.end = afterFrame; renderer.debug.onNodeBuilderCreated = previousBuilder; renderObjectPrototype.getMaterialCacheKey = materialKey; game.physics.worker.removeEventListener('message', onPhysics); };
  return { points, initialCar, flightSeconds, started: state.started };
}

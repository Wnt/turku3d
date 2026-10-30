// Inject after __game.ready with page.evaluate(installPreparation). No imported browser dependencies.
// Save window.__preparation.snapshot(); call dispose() only after the measured interval.
export function installPreparation(options = {}) {
  if (window.__preparation?.active) return window.__preparation.capabilities;
  const game = window.__game, renderer = window.__render?.renderer;
  if (!game?.coarse || !renderer) throw new Error('Preparation timing requires a ready play session');
  const maxEvents = Math.min(12000, Math.max(0, options.maxEvents ?? 4000));
  const events = [], aggregates = Object.create(null), restores = [], missing = [];
  const models = new WeakMap(), phases = new WeakMap(), builders = new WeakSet();
  const activeBuilders = new Set(), compiles = new Map();
  let enabled = true, sequence = 0, dropped = 0;
  const now = () => performance.now();
  const epoch = time => performance.timeOrigin + time;
  const keyFor = object => {
    for (let parent = object; parent; parent = parent.parent) if (models.has(parent)) return models.get(parent);
    return object?.name || null;
  };
  const phaseFor = model => {
    let phase = phases.get(model);
    if (!phase) { phase = {}; phases.set(model, phase); }
    return phase;
  };
  function record(label, start, end, detail = {}, keep = true) {
    if (!enabled) return;
    const ms = end - start;
    const aggregate = aggregates[label] ??= { count: 0, totalMs: 0, maxMs: 0, syncMs: 0, activeMs: 0, yieldMs: 0 };
    aggregate.count++; aggregate.totalMs += ms; aggregate.maxMs = Math.max(aggregate.maxMs, ms);
    for (const field of ['syncMs', 'activeMs', 'yieldMs']) aggregate[field] += detail[field] ?? 0;
    if (keep) {
      if (events.length < maxEvents) events.push({ at: epoch(start), ms, label, ...detail });
      else dropped++;
    }
  }
  function measure(label, key, invoke, detail = {}, after, keep = true) {
    const start = now();
    let value, syncMs;
    const finish = status => {
      const end = now();
      after?.(end);
      record(label, start, end, { key, syncMs, ...detail, status }, keep);
    };
    try { value = invoke(); syncMs = now() - start; }
    catch (error) { syncMs = now() - start; finish('rejected'); throw error; }
    if (value?.then) return value.then(result => { finish('fulfilled'); return result; }, error => { finish('rejected'); throw error; });
    finish('fulfilled'); return value;
  }
  function wrap(owner, method, make) {
    if (typeof owner?.[method] !== 'function') { missing.push(method); return false; }
    const original = owner[method], replacement = make(original);
    try { owner[method] = replacement; }
    catch { missing.push(method); return false; }
    if (owner[method] !== replacement) { missing.push(method); return false; }
    restores.push(() => { if (owner[method] === replacement) owner[method] = original; });
    return true;
  }
  const capabilities = {
    staticShadowSplit: false,
    outerGraphicsQueue: 'vegetation-and-queue upper bound; closed-over callbacks are not separately accessible',
    frameBudgetWait: 'resolve-only rAF callbacks identify frame-budget waits; other identical resolver callbacks can be included',
    activeBuilderTime: 'estimated CPU between scheduler yields, including instrumentation/microtask overhead',
  };
  wrap(game.coarse, 'prepareModel', original => function (id, entry, model) {
    models.set(model, id);
    return measure('model', id, () => original.call(this, id, entry, model));
  });
  wrap(game.coarse.host, 'style', original => function (model) {
    return measure('style', keyFor(model), () => original.call(this, model));
  });
  wrap(game.coarse.host, 'styled', original => function (model) {
    const phase = phaseFor(model);
    phase.styledAt = now();
    return measure('styled', keyFor(model), () => original.call(this, model), {}, end => {
      if (phase.graphicsAt !== undefined) record('graphics-execution', phase.graphicsAt, end, { key: keyFor(model) });
    });
  });
  capabilities.geometry = wrap(game.tiles, 'prepareModelAsync', original => function (model) {
    return measure('static-and-shadow', keyFor(model), () => original.call(this, model), {}, end => { phaseFor(model).geometryEnd = end; });
  });
  wrap(game.materials, 'applyAsync', original => function (model) {
    return measure('program-packing-and-style', keyFor(model), () => original.call(this, model));
  });
  wrap(game.materials, 'apply', original => function (model) {
    return measure('material-style-sync', keyFor(model), () => original.call(this, model));
  });
  capabilities.graphics = wrap(game.materials, 'prepare', original => function (model, ...args) {
    const start = now(), key = keyFor(model), phase = phaseFor(model);
    phase.graphicsAt = start;
    if (phase.geometryEnd !== undefined) record('vegetation-and-graphics-queue', phase.geometryEnd, start, { key });
    let admitted = start;
    // Observe the existing tail before prepare() appends its callback; do not replace admission.
    this.preparing?.then(() => { admitted = now(); record('material-queue', start, admitted, { key }); }, () => {
      admitted = now(); record('material-queue', start, admitted, { key, status: 'rejected-tail' });
    });
    return measure('material-prepare', key, () => original.call(this, model, ...args), {}, end => {
      record('textures-and-reflections', admitted, end, { key });
    });
  });
  capabilities.compile = wrap(renderer, 'compileAsync', original => function (object, camera, ...args) {
    const id = ++sequence, key = keyFor(object);
    compiles.set(id, { object, camera });
    const target = this.getRenderTarget();
    return measure('compile', key, () => original.call(this, object, camera, ...args), {
      id, object: object?.uuid, camera: camera?.uuid, target: target?.uuid ?? null,
      mainCamera: camera === window.__render.camera,
    }, () => compiles.delete(id));
  });
  wrap(renderer._nodes, 'getForRenderAsync', original => function (draw) {
    return measure('node-build-or-cache', keyFor(draw.object), () => original.call(this, draw), {}, undefined, false);
  });
  for (const [owner, label] of [[renderer._geometries, 'geometry-upload-sync'], [renderer._bindings, 'bindings-upload-sync']]) {
    wrap(owner, 'updateForRender', original => function (draw, ...args) {
      // Exclude normal frame draws: compileAsync alone owns this synchronous section.
      if (!renderer._isPreCompiling) return original.call(this, draw, ...args);
      return measure(label, keyFor(draw.object), () => original.call(this, draw, ...args), {}, undefined, false);
    });
  }
  wrap(renderer, 'initTexture', original => function (texture) {
    return measure('texture-upload-sync', null, () => original.call(this, texture), {}, undefined, false);
  });
  capabilities.pipeline = wrap(renderer.backend?.device, 'createRenderPipelineAsync', original => function (...args) {
    return measure('gpu-pipeline', null, () => original.apply(this, args), { compileIds: [...compiles.keys()] });
  });
  capabilities.builders = wrap(renderer._nodes, '_createNodeBuilder', original => function (draw, ...args) {
    const builder = original.call(this, draw, ...args);
    if (builders.has(builder)) return builder;
    builders.add(builder);
    const build = builder.buildAsync;
    builder.buildAsync = function (...buildArgs) {
      if (!enabled) return build.apply(this, buildArgs);
      const start = now(), state = { cpuStart: start, activeMs: 0, yieldMs: 0, yields: 0 };
      activeBuilders.add(state);
      return measure('node-builder', keyFor(draw.object), () => build.apply(this, buildArgs), {}, end => {
        if (state.cpuStart !== null) state.activeMs += end - state.cpuStart;
        activeBuilders.delete(state);
        record('node-builder-active-estimate', start, end, { key: keyFor(draw.object),
          activeMs: state.activeMs, yieldMs: state.yieldMs, yields: state.yields });
      });
    };
    return builder;
  });
  capabilities.scheduler = wrap(globalThis.scheduler, 'yield', original => function (...args) {
    if (!enabled) return original.apply(this, args);
    const start = now(), paused = [];
    for (const state of activeBuilders) if (state.cpuStart !== null) {
      state.activeMs += start - state.cpuStart; state.cpuStart = null; state.yields++; paused.push(state);
    }
    return measure('scheduler-yield', null, () => original.apply(this, args), {}, end => {
      for (const state of paused) { state.yieldMs += end - start; state.cpuStart = end; }
    }, false);
  });
  const frameResolvers = new WeakMap();
  capabilities.frameResolvers = wrap(globalThis, 'requestAnimationFrame', original => function (callback) {
    if (!enabled || callback.length !== 0) return original.call(this, callback);
    let resolver = frameResolvers.get(callback);
    if (resolver === undefined) {
      // PreparationBudget resolves its promise in exactly this callback shape, in dev and Vite output.
      resolver = /^\(\s*\)\s*=>\s*(?:\{\s*)?[A-Za-z_$][\w$]*\(\s*\)\s*;?\s*(?:\})?$/.test(callback.toString());
      frameResolvers.set(callback, resolver);
    }
    if (!resolver) return original.call(this, callback);
    const start = now();
    return original.call(this, function (...args) {
      record('resolve-only-frame-wait', start, now(), {}, false);
      return callback.apply(this, args);
    });
  });
  window.__preparation = {
    active: true, capabilities,
    snapshot: () => ({ capabilities, missing: [...missing], maxEvents, dropped, events: [...events],
      aggregates: Object.fromEntries(Object.entries(aggregates).map(([label, value]) => [label, { ...value }])),
      note: 'Nested/concurrent spans overlap. Wall time and active estimates are not GPU duration. Vegetation and outer graphics queue remain combined.' }),
    dispose() {
      enabled = false; this.active = false;
      for (const restore of restores.reverse()) restore();
    },
  };
  return capabilities;
}

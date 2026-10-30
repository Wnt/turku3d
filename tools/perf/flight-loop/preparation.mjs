// Inject after __game.ready with page.evaluate(installPreparation). No imported browser dependencies.
// Save window.__preparation.snapshot(); call dispose() only after the measured interval.
export function installPreparation(options = {}) {
  if (window.__preparation?.active) return window.__preparation.capabilities;
  const game = window.__game, renderer = window.__render?.renderer;
  if (!game?.coarse || !renderer) throw new Error('Preparation timing requires a ready play session');
  const maxEvents = Math.floor(Math.min(12000, Math.max(0, options.maxEvents ?? 4000)));
  const events = [], aggregates = Object.create(null), restores = [], missing = [];
  // Compilation visits every draw/pass; its samples must never displace model phases.
  const diagnosticLabels = new Set(['compile', 'node-builder', 'node-builder-active-estimate']);
  const diagnostics = Object.create(null);
  const models = new WeakMap(), phases = new WeakMap(), builders = new WeakSet();
  const activeBuilders = new Set(), compiles = new Map(), preparingModels = new Map();
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
    if (!keep) return;
    if (diagnosticLabels.has(label)) {
      const sample = diagnostics[label] ??= { first: [], slowest: [], omitted: 0 };
      if (sample.first.length < 4) sample.first.push({ at: epoch(start), ms, label, ...detail });
      else if (sample.slowest.length < 28) sample.slowest.push({ at: epoch(start), ms, label, ...detail });
      else {
        let least = 0;
        for (let i = 1; i < sample.slowest.length; i++) if (sample.slowest[i].ms < sample.slowest[least].ms) least = i;
        if (ms > sample.slowest[least].ms) sample.slowest[least] = { at: epoch(start), ms, label, ...detail };
        sample.omitted++;
      }
    } else if (maxEvents) {
      if (events.length === maxEvents) { events.shift(); dropped++; }
      events.push({ at: epoch(start), ms, label, ...detail });
    } else dropped++;
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
    eventRetention: 'latest model/phase events; first 4 and slowest 28 compile/builder events per label, with complete aggregates',
    memoryInventory: 'whole CPU ArrayBuffers reachable through scene/model/source geometry and material textures; not GPU allocation or private idle caches',
  };
  function memoryInventory(label) {
    const start = now(), buffers = new Map(), categories = new Map(), roles = new Map(), owners = new Map();
    const roots = new Map(), materials = new Map(), textures = new Set(), geometries = new Set();
    const warnings = new Set();
    const addRoot = (root, key) => { if (root && !roots.has(root)) roots.set(root, String(key || keyFor(root) || root.uuid || 'unnamed-model')); };
    for (const [id, entry] of game.coarse.entries ?? []) {
      addRoot(entry.model, id); addRoot(entry.comparison, `${id}:comparison`);
    }
    for (const model of game.tiles?.models ?? []) addRoot(model);
    for (const model of game.materials?.owned?.keys() ?? []) addRoot(model);
    for (const [model, id] of preparingModels) addRoot(model, `${id}:preparing`);
    function add(value, category, role, owner) {
      const buffer = ArrayBuffer.isView(value) ? value.buffer : value;
      if (!(buffer instanceof ArrayBuffer) && !(typeof SharedArrayBuffer !== 'undefined' && buffer instanceof SharedArrayBuffer)) return;
      let row = buffers.get(buffer);
      if (!row) { row = { bytes: buffer.byteLength, categories: new Set(), roles: new Set(), owners: new Set() }; buffers.set(buffer, row); }
      row.categories.add(category); row.roles.add(role); row.owners.add(owner);
      for (const [map, key] of [[categories, category], [roles, role], [owners, owner]]) {
        let set = map.get(key); if (!set) { set = new Set(); map.set(key, set); } set.add(buffer);
      }
    }
    function textureData(value, category, owner, seen = new Set()) {
      if (!value || typeof value !== 'object' || seen.has(value)) return;
      seen.add(value);
      if (ArrayBuffer.isView(value) || value instanceof ArrayBuffer || (typeof SharedArrayBuffer !== 'undefined' && value instanceof SharedArrayBuffer)) {
        add(value, category, 'texture', owner); return;
      }
      if (Array.isArray(value)) { for (const child of value) textureData(child, category, owner, seen); return; }
      // Read only known image/mipmap containers; never walk DOM images or arbitrary object graphs.
      for (const field of ['data', 'mipmaps', 'image']) if (value[field]) textureData(value[field], category, owner, seen);
    }
    function texture(texture, owner) {
      if (!texture?.isTexture) return;
      textures.add(texture);
      textureData(texture.source?.data, 'texture', owner);
      textureData(texture.image, 'texture', owner);
      textureData(texture.mipmaps, 'texture', owner);
    }
    function material(material, owner) {
      if (Array.isArray(material)) { for (const part of material) visitMaterial(part, owner); }
      else visitMaterial(material, owner);
    }
    function visitMaterial(material, owner) {
      if (!material) return;
      let maps = materials.get(material);
      if (!maps) {
        maps = new Set(); materials.set(material, maps);
        const pending = [], nodes = new Set();
        for (const value of Object.values(material)) {
          if (value?.isTexture) maps.add(value);
          else if (value?.isNode) pending.push(value);
        }
        // TSL is a shared DAG; Node.traverse can revisit exponentially many paths.
        while (pending.length) {
          const node = pending.pop();
          if (nodes.has(node)) continue;
          nodes.add(node);
          if (node.value?.isTexture) maps.add(node.value);
          try { for (const child of node.getChildren?.() ?? []) if (!nodes.has(child)) pending.push(child); }
          catch { warnings.add('A TSL node did not expose its children'); }
        }
      }
      for (const map of maps) texture(map, owner);
    }
    function draw(object, category, owner) {
      const geometry = object.geometry;
      if (geometry) {
        geometries.add(geometry);
        const attribute = (value, name) => add(value?.array ?? value?.data?.array, category, name.startsWith('_kit_') ? 'program' : name, owner);
        for (const [name, value] of Object.entries(geometry.attributes ?? {})) attribute(value, name);
        attribute(geometry.index, 'index');
        attribute(geometry.indirect, 'indirect');
        for (const [name, values] of Object.entries(geometry.morphAttributes ?? {})) for (const value of values) attribute(value, `morph:${name}`);
      }
      for (const field of ['instanceMatrix', 'instanceColor']) add(object[field]?.array, 'instance-and-skeleton', field, owner);
      add(object.skeleton?.boneMatrices, 'instance-and-skeleton', 'boneMatrices', owner);
      texture(object.skeleton?.boneTexture, owner); texture(object.morphTexture, owner);
      material(object.material, owner);
    }
    function walk(root, owner, skipModels = false) {
      const pending = [root], visited = new Set();
      while (pending.length) {
        const object = pending.pop();
        if (!object || visited.has(object) || (skipModels && roots.has(object))) continue;
        visited.add(object);
        const category = object.userData?.shadowBatch ? 'shadow-batch' : object.name === 'static-tile-batch' ? 'static-batch' : 'scene-geometry';
        draw(object, category, owner);
        for (const child of object.children ?? []) pending.push(child);
      }
    }
    for (const [model, owner] of roots) walk(model, owner);
    const scene = game.coarse.host?.scene;
    if (scene) { walk(scene, 'other-scene', true); texture(scene.background, 'other-scene'); texture(scene.environment, 'other-scene'); }
    for (const [model, owned] of game.materials?.owned ?? []) {
      const owner = roots.get(model);
      // Static batching detaches these originals but retains them for restoration on release.
      for (const [mesh, sourceMaterial] of owned.sources ?? []) { draw(mesh, 'source-geometry', owner); material(sourceMaterial, owner); }
    }
    for (const [model, shadow] of game.tiles?.shadows ?? []) walk(shadow, roots.get(model) ?? 'untracked-shadow');
    texture(game.materials?.detail, 'shared-styler');
    for (const entry of game.materials?.shared?.entries?.values() ?? []) material(entry.material, 'shared-styler');
    const rows = map => [...map].map(([name, set]) => ({ name, buffers: set.size, bytes: [...set].reduce((sum, buffer) => sum + buffer.byteLength, 0),
      exclusiveBytes: [...set].reduce((sum, buffer) => sum + (buffers.get(buffer).categories.size === 1 ? buffer.byteLength : 0), 0) })).sort((a, b) => b.bytes - a.bytes);
    const totalBytes = [...buffers.values()].reduce((sum, row) => sum + row.bytes, 0);
    const result = { label, at: epoch(start), totalBytes, buffers: buffers.size,
      models: roots.size, geometries: geometries.size, materials: materials.size, textures: textures.size,
      categories: rows(categories).map(row => ({ ...row, roles: [...roles].map(([name, set]) => ({ name,
        bytes: [...set].reduce((sum, buffer) => sum + (buffers.get(buffer).categories.has(row.name) ? buffer.byteLength : 0), 0),
      })).filter(role => role.bytes).sort((a, b) => b.bytes - a.bytes) })),
      roles: rows(roles).map(({ exclusiveBytes, ...row }) => row),
      owners: rows(owners).map(({ exclusiveBytes, ...row }) => row),
      largestBuffers: [...buffers.values()].sort((a, b) => b.bytes - a.bytes).slice(0, 24).map(row => ({ bytes: row.bytes,
        categories: [...row.categories], roles: [...row.roles], owners: [...row.owners].slice(0, 12), ownerCount: row.owners.size })),
      warnings: [...warnings],
      note: 'Deduplicated by ArrayBuffer identity; entire backing buffers counted, including unused view ranges. Category/role/model totals overlap. exclusiveBytes means one category only. Excludes GPU memory, native decoded images, workers, private module caches and retired resources held only by closures.' };
    result.durationMs = now() - start;
    return result;
  }
  // Inventories retain only numbers/strings, never references to game buffers or objects.
  const inventories = [];
  function sampleMemory(label) {
    let inventory;
    try { inventory = memoryInventory(label); }
    catch (error) { inventory = { label, at: epoch(now()), error: String(error) }; }
    inventories.push(inventory);
    if (inventories.length > 8) inventories.splice(1, 1); // Preserve start and the latest seven samples.
    return inventory;
  }
  sampleMemory('start');
  wrap(game.coarse, 'prepareModel', original => function (id, entry, model) {
    models.set(model, id);
    preparingModels.set(model, id);
    return measure('model', id, () => original.call(this, id, entry, model), {}, () => preparingModels.delete(model));
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
    sampleMemory,
    snapshot: ({ memory = true, label = 'snapshot' } = {}) => {
      if (memory) sampleMemory(label);
      return { capabilities, missing: [...missing], maxEvents, dropped, events: [...events],
        diagnosticSamples: Object.fromEntries(Object.entries(diagnostics).map(([label, sample]) => [label, {
          omitted: sample.omitted, events: [...sample.first, ...sample.slowest].sort((a, b) => a.at - b.at),
        }])), memoryInventories: [...inventories],
        aggregates: Object.fromEntries(Object.entries(aggregates).map(([label, value]) => [label, { ...value }])),
        note: 'Nested/concurrent spans overlap. Wall time and active estimates are not GPU duration. Vegetation and outer graphics queue remain combined.' };
    },
    dispose() {
      enabled = false; this.active = false;
      preparingModels.clear();
      for (const restore of restores.reverse()) restore();
    },
  };
  return capabilities;
}

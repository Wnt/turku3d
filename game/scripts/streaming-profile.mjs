// Capture-only hooks installed before the game entry; never imported by production.
export function installStreamingProfile() {
  const rows = window.__streamingProfile = [];
  let active = null, render;
  const describe = mesh => {
    const ancestry = [];
    for (let node = mesh; node; node = node.parent) ancestry.push(node.name || node.uuid);
    return { mesh: mesh?.name, ancestry };
  };
  function wrap(owner, key, kind, identify = () => active) {
    const original = owner?.[key];
    if (!original) return;
    owner[key] = function (...args) {
      const start = performance.now(), model = identify(args);
      let result;
      try { result = original.apply(this, args); return result; }
      finally {
        const row = { kind, start, ms: performance.now() - start, ...model };
        rows.push(row);
        if (result?.then) result.then(() => { row.settledMs = performance.now() - start; }, () => { row.failed = true; });
      }
    };
  }
  Object.defineProperty(window, '__render', { configurable: true, get: () => render, set(value) {
    render = value;
    const renderer = value.renderer, backend = renderer.backend;
    const draw = renderer.renderObject;
    renderer.renderObject = function (...args) {
      const before = active; active = describe(args[0]);
      try { return draw.apply(this, args); } finally { active = before; }
    };
    const builder = renderer.debug.onNodeBuilderCreated;
    renderer.debug.onNodeBuilderCreated = (node, object) => {
      builder?.(node, object);
      const identity = { ...describe(object.object), material: object.material.name, context: object.context.id };
      wrap(node, 'build', 'node-build-sync', () => identity);
      wrap(node, 'buildAsync', 'node-build-async', () => identity);
    };
    wrap(renderer, 'compileAsync', 'compile-model', args => describe(args[0]));
    wrap(backend, 'createRenderPipeline', 'pipeline', args => ({ ...describe(args[0].object), async: !!args[1] }));
    for (const key of ['createAttribute', 'createIndexAttribute', 'updateAttribute'])
      wrap(backend, key, 'geometry-upload', args => ({ ...active, bytes: (args[0].array ?? args[0].data?.array)?.byteLength }));
    wrap(backend, 'updateTexture', 'texture-upload', args => ({ ...active, texture: args[0].name || args[0].uuid }));
    wrap(backend.device, 'createRenderPipeline', 'webgpu-pipeline-sync');
    wrap(backend.device, 'createRenderPipelineAsync', 'webgpu-pipeline-async');
  } });
}

import { expect, test } from 'vitest';
import { asyncFirstUsePipelines } from '../../src/render/asyncPipelines';

test('live-frame draws request first-use pipelines asynchronously; quads and other renders stay synchronous', async () => {
  const calls: (unknown[] | null | undefined)[] = [];
  let finish: (() => void) | undefined;
  const pipelines = {
    getForRender(_draw: object, promises: Promise<void>[] | null = null) {
      calls.push(promises);
      promises?.push(new Promise<void>(resolve => { finish = resolve; }));
    },
    updateForRender(draw: object) { this.getForRender(draw); },
  };
  const original = pipelines.updateForRender;
  let live = false;
  const { release, settled } = asyncFirstUsePipelines(pipelines, () => live);
  const mesh = { object: {} }, quad = { object: { isQuadMesh: true } };
  pipelines.updateForRender(mesh);
  live = true;
  pipelines.updateForRender(mesh); pipelines.updateForRender(quad);
  expect(calls[0]).toBeNull();          // outside the live frame (IBL bake, compileAsync): r186's synchronous path
  expect(Array.isArray(calls[1])).toBe(true); // live draw: createRenderPipelineAsync, drawn once isReady
  expect(calls[2]).toBeNull();          // fullscreen effect quads keep their first frame
  let done = false; const waiting = settled().then(() => { done = true; });
  await Promise.resolve(); expect(done).toBe(false); // the loading warmup waits for pending compiles
  finish!(); await waiting; expect(done).toBe(true);
  release();
  expect(pipelines.updateForRender).toBe(original);
});

test('r186 draws only once a pipeline isReady, right after updateForRender (the contract the wrapper relies on)', async () => {
  const fs = await import('node:fs');
  const source = fs.readFileSync(new URL('../../node_modules/three/src/renderers/common/Renderer.js', import.meta.url), 'utf8');
  expect(source).toMatch(/this\._pipelines\.updateForRender\( renderObject \);\s*\/\/\s*if \( this\._pipelines\.isReady\( renderObject \) \)/);
});

test('live first-use draws take new shader modules from a wall-time budget; the rest wait for a later frame', async () => {
  const { ShaderModuleBudget } = await import('../../src/render/shaderModules');
  let now = 0;
  const budget = new ShaderModuleBudget(() => now, 10, 3); // 10 ms per module, at most 3 saved up
  const device = { createShaderModule: (descriptor: unknown) => ({ descriptor }) };
  budget.wrap(device);
  const programs = { vertex: new Map<string, unknown>(), fragment: new Map<string, unknown>() };
  const data = new Map<object, { pipeline?: unknown }>();
  const requested: string[] = [];
  const pipelines = {
    programs,
    get(draw: object) { let d = data.get(draw); if (!d) data.set(draw, d = {}); return d; },
    getForRender(draw: { name: string; getNodeBuilderState(): { vertexShader: string; fragmentShader: string } }, promises: Promise<void>[] | null = null) {
      if (this.get(draw).pipeline) return;
      const state = draw.getNodeBuilderState();
      for (const [stage, code] of [['vertex', state.vertexShader], ['fragment', state.fragmentShader]] as const)
        if (!programs[stage].has(code)) { programs[stage].set(code, device.createShaderModule({ code })); }
      this.get(draw).pipeline = {}; requested.push(draw.name); promises?.push(Promise.resolve());
    },
    updateForRender(draw: object) { void draw; },
  };
  asyncFirstUsePipelines(pipelines as never, () => true, budget);
  const draw = (name: string, vs: string, fs: string) => ({ name, getNodeBuilderState: () => ({ vertexShader: vs, fragmentShader: fs }) });
  const a = draw('a', 'va', 'fa'), b = draw('b', 'vb', 'fb'), c = draw('c', 'va', 'fa'), d = draw('d', 'vd', 'fd');
  const frame = () => { for (const x of [a, b, c, d]) pipelines.updateForRender(x); };
  frame();
  // a spends two of the three saved modules; b needs two more and waits; c reuses a's program; d waits too.
  expect(requested).toEqual(['a', 'c']);
  expect(budget.takeDeferred()).toBe(2);
  now += 10; frame(); // one more module's worth: b goes, d still waits
  expect(requested).toEqual(['a', 'c', 'b']);
  now += 5; frame();  // half a module: d waits
  expect(requested).toEqual(['a', 'c', 'b']);
  now += 200; frame(); // a slow frame saved up the maximum
  expect(requested).toEqual(['a', 'c', 'b', 'd']);
  expect(budget.created).toBe(6);
});

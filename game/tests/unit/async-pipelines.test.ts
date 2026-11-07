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

test('live first-use draws take new shader modules from a budget of estimated translation time; the rest wait', async () => {
  const { ShaderModuleBudget } = await import('../../src/render/shaderModules');
  let now = 0;
  const budget = new ShaderModuleBudget(0.01, () => now, 0.5, 40); // 1,000 characters: 10 ms; half the wall time, 40 ms saved
  const device = { createShaderModule: (descriptor: { code?: string }) => ({ descriptor }) };
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
  const code = (tag: string, chars: number) => tag.padEnd(chars, '.');
  const draw = (name: string, vs: string, fs: string) => ({ name, getNodeBuilderState: () => ({ vertexShader: vs, fragmentShader: fs }) });
  // a: 5 ms + 25 ms; b: a big lit fragment (40 ms); c reuses a's program; d: two small modules (2 + 2 ms).
  const a = draw('a', code('va', 500), code('fa', 2500)), b = draw('b', code('vb', 500), code('fb', 4000));
  const c = draw('c', code('va', 500), code('fa', 2500)), d = draw('d', code('vd', 200), code('fd', 200));
  const frame = () => { for (const x of [a, b, c, d]) pipelines.updateForRender(x); };
  frame();
  // A full budget (40 ms) takes a (30 ms); b needs 45 and waits; c reuses a's modules; d's 4 ms fit in the 10 left.
  expect(requested).toEqual(['a', 'c', 'd']);
  expect(budget.takeDeferred()).toBe(1);
  now += 40; frame(); // 6 + 20 = 26 ms saved: b's 45 ms still waits
  expect(requested).toEqual(['a', 'c', 'd']);
  now += 60; frame(); // a full budget lets one program through whatever its size; it then owes the rest
  expect(requested).toEqual(['a', 'c', 'd', 'b']);
  expect(budget.room(1000)).toBe(false);
  expect(budget.created).toBe(6);
});


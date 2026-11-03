import { expect, test } from 'vitest';
import { asyncFirstUsePipelines } from '../../src/render/asyncPipelines';

test('live-frame draws request first-use pipelines asynchronously; quads and other renders stay synchronous', () => {
  const calls: (unknown[] | null | undefined)[] = [];
  const pipelines = {
    getForRender(_draw: object, promises: unknown[] | null = null) { calls.push(promises); },
    updateForRender(draw: object) { this.getForRender(draw); },
  };
  const original = pipelines.updateForRender;
  let live = false;
  const release = asyncFirstUsePipelines(pipelines, () => live);
  const mesh = { object: {} }, quad = { object: { isQuadMesh: true } };
  pipelines.updateForRender(mesh);
  live = true;
  pipelines.updateForRender(mesh); pipelines.updateForRender(quad);
  expect(calls[0]).toBeNull();          // outside the live frame (IBL bake, compileAsync): r186's synchronous path
  expect(Array.isArray(calls[1])).toBe(true); // live draw: createRenderPipelineAsync, drawn once isReady
  expect(calls[2]).toBeNull();          // fullscreen effect quads keep their first frame
  release();
  expect(pipelines.updateForRender).toBe(original);
});

test('r186 draws only once a pipeline isReady, right after updateForRender (the contract the wrapper relies on)', async () => {
  const fs = await import('node:fs');
  const source = fs.readFileSync(new URL('../../node_modules/three/src/renderers/common/Renderer.js', import.meta.url), 'utf8');
  expect(source).toMatch(/this\._pipelines\.updateForRender\( renderObject \);\s*\/\/\s*if \( this\._pipelines\.isReady\( renderObject \) \)/);
});

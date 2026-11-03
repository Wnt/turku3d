import { expect, test, vi } from 'vitest';
import * as THREE from 'three/webgpu';

const compiled = vi.hoisted(() => [] as { model: THREE.Object3D; castersOnly: boolean }[]);
vi.mock('../../src/render/compileModel', async original => ({
  ...await original<typeof import('../../src/render/compileModel')>(),
  compileModel: vi.fn(async (_renderer: unknown, model: THREE.Object3D, ...rest: unknown[]) => {
    compiled.push({ model, castersOnly: rest[rest.length - 1] === true });
  }),
}));
import { PostProcessing } from '../../src/render/post';
import { GameRenderer } from '../../src/render/shadowMaterials';

test('models prepared before the first shadow pass are handed back once, to compile their casters', async () => {
  const renderer = Object.create(GameRenderer.prototype) as GameRenderer;
  Object.assign(renderer, { shadowPasses: new Map() });
  const post = Object.create(PostProcessing.prototype) as PostProcessing;
  Object.assign(post, { renderer, pre: null, scene: new THREE.Scene(), camera: new THREE.PerspectiveCamera(),
    beauty: { renderTarget: null, getMRT: () => null }, beautyContext: null, beautyDepth: 2, shadowless: new Set() });
  const early = new THREE.Group(), late = new THREE.Group();
  await post.prepare(early);
  expect(compiled.filter(c => c.castersOnly)).toHaveLength(0);
  // No shadow pass yet: nothing to hand back.
  expect(post.takeShadowless()).toEqual([]);
  const override = new THREE.MeshBasicMaterial();
  renderer.shadowPasses.set(override, { override, camera: new THREE.OrthographicCamera(), target: null, mrt: null as never, depth: 3,
    context: renderer.contextNode });
  expect(post.takeShadowless()).toEqual([early]);
  expect(post.takeShadowless()).toEqual([]);
  // Prepared again (or first prepared) once a cascade has drawn: its casters compile and it is not queued again.
  await post.prepare(early); await post.prepare(late);
  expect(compiled.filter(c => c.castersOnly).map(c => c.model)).toEqual([early, late]);
  expect(post.takeShadowless()).toEqual([]);
});

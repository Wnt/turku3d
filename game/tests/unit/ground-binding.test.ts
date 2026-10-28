import { beforeEach, expect, it, vi } from 'vitest';
import * as THREE from 'three/webgpu';
import { GroundBindings } from '../../src/render/ground/binding';

const mocks = vi.hoisted(() => ({ library: vi.fn(), tile: vi.fn(), upload: vi.fn() }));
vi.mock('../../src/render/textureUploads', () => ({ textureUploads: () => ({ enqueue: mocks.upload }) }));
vi.mock('../../src/render/ground/material', () => ({ loadGroundLibrary: mocks.library, loadGroundTile: mocks.tile }));
vi.mock('three/addons/loaders/KTX2Loader.js', () => ({ KTX2Loader: class {
  setTranscoderPath() { return this; }
  detectSupport() { return this; }
  dispose() {}
} }));
const fixture = () => {
  const scene = new THREE.Group();
  scene.userData.ground_binding = { spec: 'ground/t0/material.json', library: 'ground/library/' };
  const ground = new THREE.Mesh(); ground.userData.layer = 'ground';
  const building = new THREE.Mesh(); building.userData.layer = 'buildings';
  const water = new THREE.Mesh(); water.userData = { layer: 'ground', material_key: 'water' };
  scene.add(ground, building, water);
  return { scene, ground, building, water };
};
beforeEach(() => { mocks.library.mockReset(); mocks.tile.mockReset(); mocks.upload.mockReset().mockResolvedValue(undefined); });
it('uses assembler URLs, shares library, and changes only ground meshes', async () => {
  const library = { albedo: new THREE.Texture(), normal: new THREE.Texture(), orm: new THREE.Texture() };
  mocks.library.mockResolvedValue(library);
  const material = new THREE.MeshStandardNodeMaterial(), dispose = vi.fn();
  mocks.tile.mockResolvedValue({ material, maps: { ortho: new THREE.Texture(), splat0: new THREE.Texture(), splat1: new THREE.Texture() }, dispose });
  const binding = new GroundBindings({} as THREE.WebGPURenderer, 'https://tiles.test/tileset.json');
  const a = fixture(), b = fixture(), original = a.building.material, originalWater = a.water.material;
  await Promise.all([binding.apply(a.scene), binding.apply(b.scene)]);
  expect(mocks.library).toHaveBeenCalledTimes(1);
  expect(mocks.upload).toHaveBeenCalledTimes(12);
  expect(mocks.tile.mock.calls[0][0]).toBe('https://tiles.test/ground/t0/material.json');
  expect(a.ground.material).toBe(material);
  expect(a.building.material).toBe(original);
  expect(a.water.material).toBe(originalWater);
  expect(binding.pending).toBe(0);
  binding.release(a.scene);
  expect(dispose).toHaveBeenCalledTimes(1);
  binding.dispose();
  expect(dispose).toHaveBeenCalledTimes(2);
});
it('disposes a late load after unload without mutating the removed mesh', async () => {
  mocks.library.mockResolvedValue({});
  let complete!: (value: unknown) => void;
  mocks.tile.mockImplementation(() => new Promise(resolve => { complete = resolve; }));
  const binding = new GroundBindings({} as THREE.WebGPURenderer, 'https://tiles.test/tileset.json');
  const { scene, ground } = fixture(), original = ground.material;
  const loading = binding.apply(scene);
  await Promise.resolve();
  expect(binding.pending).toBe(1);
  binding.release(scene);
  const dispose = vi.fn();
  complete({ material: new THREE.MeshStandardNodeMaterial(), dispose });
  await loading;
  expect(dispose).toHaveBeenCalledOnce();
  expect(ground.material).toBe(original);
  expect(binding.pending).toBe(0);
});
it('binds one material through batched parent metadata and preserves object ranges', async () => {
  mocks.library.mockResolvedValue({});
  const material = new THREE.MeshStandardNodeMaterial();
  mocks.tile.mockResolvedValue({ material, maps: {}, dispose: vi.fn() });
  const { scene } = fixture(), batch = new THREE.Group();
  const objects = [{ object_id: 'street', start: 0, count: 3 }, { object_id: 'sidewalk', start: 3, count: 3 }];
  batch.userData = { layer: 'ground', material_key: 'asphalt', objects };
  const a = new THREE.Mesh(), b = new THREE.Mesh();
  b.material = a.material;
  batch.add(a, b); scene.add(batch);
  const binding = new GroundBindings({} as THREE.WebGPURenderer, 'https://tiles.test/tileset.json');
  await binding.apply(scene);
  expect(a.material).toBe(material);
  expect(b.material).toBe(material);
  expect(batch.userData.objects).toBe(objects);
  expect(mocks.tile).toHaveBeenCalledOnce();
  binding.dispose();
});

it('keeps the original ground visible until its maps finish uploading', async () => {
  mocks.library.mockResolvedValue({});
  const material = new THREE.MeshStandardNodeMaterial();
  const ortho = new THREE.Texture();
  mocks.tile.mockResolvedValue({ material, maps: { ortho }, dispose: vi.fn() });
  let complete!: () => void;
  mocks.upload.mockImplementation(() => new Promise<void>(resolve => { complete = resolve; }));
  const binding = new GroundBindings({} as THREE.WebGPURenderer, 'https://tiles.test/tileset.json');
  const { scene, ground } = fixture(), original = ground.material;
  const loading = binding.apply(scene);
  await vi.waitFor(() => expect(mocks.upload).toHaveBeenCalledWith(ortho));
  expect(ground.material).toBe(original);
  expect(binding.pending).toBe(1);
  complete(); await loading;
  expect(ground.material).toBe(material);
  expect(binding.pending).toBe(0);
  binding.dispose();
});

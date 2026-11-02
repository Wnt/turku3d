import { describe, expect, it, vi } from 'vitest';
import * as THREE from 'three/webgpu';
import { VegetationLod } from '../../src/world/vegetationLod';
import { GrassField } from '../../src/world/nature/grass';
import { natureAsset, natureScale, natureTint } from '../../src/world/nature/species';
import { batchTileShadows } from '../../src/world/shadowBatch';
import { natureFixture } from './natureFixture';
// @ts-expect-error Internal r186 upload manager has no declaration; exercise the renderer's actual upload policy.
import Attributes from 'three/src/renderers/common/Attributes.js';
import { AttributeType } from 'three/src/renderers/common/Constants.js';

const library = natureFixture();

/** Tile vegetation in the generator's contract: one prototype per variant, measured (crown, height, crown) scale. */
function tile(count = 60, variant = 'tree_detailed_fall', at = (i: number) => new THREE.Vector3(i * 10 - 300, 0, -100), height = 15) {
  const model = new THREE.Group();
  const mesh = new THREE.InstancedMesh(new THREE.SphereGeometry(1, 8, 6), new THREE.MeshStandardMaterial(), count);
  mesh.userData = { layer: 'vegetation', material_key: 'tree_foliage', variant, instance_object_ids: Array.from({ length: count }, (_, i) => `osm:${i}`) };
  for (let i = 0; i < count; i++) mesh.setMatrixAt(i, new THREE.Matrix4().compose(at(i), new THREE.Quaternion(), new THREE.Vector3(4, height, 4)));
  model.add(mesh);
  return { model, mesh };
}
const generated = (model: THREE.Object3D, source: THREE.Object3D) => {
  const batches: THREE.InstancedMesh[] = [];
  model.traverse(o => { if ((o as THREE.InstancedMesh).isInstancedMesh && o !== source) batches.push(o as THREE.InstancedMesh); });
  return batches;
};
const drawn = (batches: THREE.InstancedMesh[]) => batches.filter(b => b.visible && !b.userData.shadowProxy && b.material !== library.materials.bark).reduce((n, b) => n + b.count, 0);

describe('species mapping', () => {
  it('uses registered species ahead of riverside and shape guesses', () => {
    expect(natureAsset('tree_pineDefaultA', 'puut:1', 18, true, 'Tilia cordata')).toBe('tree_linden');
    expect(natureAsset('tree_default_fall', 'puut:2', 18, true, 'Acer platanoides')).toBe('tree_maple');
    expect(natureAsset('tree_default_fall', 'puut:3', 18, false, 'Picea abies')).toBe('tree_spruce');
  });
  it('maps tile variants and inventory ids to Turku species deterministically', () => {
    expect(natureAsset('tree_pineDefaultA', 'osm:1', 15)).toMatch(/^tree_(spruce|pine)$/);
    expect(natureAsset('tree_oak_fall', 'osm:1', 12)).toMatch(/^tree_(oak|linden)$/);
    expect(natureAsset('tree_default_fall', 'bush:area:1', 1.2)).toMatch(/^shrub_/);
    expect(natureAsset('tree_default_fall', 'osm:7', 4)).toMatch(/^tree_(rowan|birch)$/);
    for (let i = 0; i < 200; i++) expect(natureAsset('tree_detailed_fall', `osm:${i}`, 15)).toBe(natureAsset('tree_detailed_fall', `osm:${i}`, 15));
    const spread = new Set(Array.from({ length: 200 }, (_, i) => natureAsset('tree_detailed_fall', `osm:${i}`, 15)));
    expect(spread.size).toBeGreaterThanOrEqual(3);
    expect(spread.has('tree_willow')).toBe(false);
    expect(natureTint('osm:3')).toEqual(natureTint('osm:3'));
  });

  it('spreads one species over its seeded variants, stably per tree', () => {
    const picks = Array.from({ length: 200 }, (_, i) => library.variant('tree_birch', `osm:${i}`).name);
    expect(new Set(picks)).toEqual(new Set(['tree_birch', 'tree_birch_v2']));
    expect(library.variant('tree_birch', 'osm:7').name).toBe(picks[7]);
    expect(library.variant('tree_oak', 'osm:7').name).toBe('tree_oak');
  });

  it('preserves measured broad crowns and falls back only for invalid dimensions', () => {
    expect(natureScale(10, 3, 20, 5)).toEqual({ y: 0.5, xz: 0.6 });
    expect(natureScale(10, 6, 16, 4.6)).toEqual({ y: .625, xz: 6 / 4.6 });
    expect(natureScale(10, .1, 20, 5).xz).toBeCloseTo(.02);
    expect(natureScale(10, NaN, 20, 5)).toEqual({ y: .5, xz: .5 });
    expect(natureScale(Infinity, 3, 20, 0)).toEqual({ y: 1, xz: 1 });
  });
});

describe('vegetation LOD', () => {
  it('demotes a partly admitted cell without dropping or duplicating its remaining trees', () => {
    const { model, mesh } = tile(900, 'tree_detailed_fall', () => new THREE.Vector3(0, 0, -20));
    mesh.userData.instance_species = Array(900).fill('Tilia cordata');
    const scene = new THREE.Scene(); scene.add(model);
    const camera = new THREE.PerspectiveCamera(65, 1, .1, 1000);
    const lod = new VegetationLod('mobile', library); lod.add(model); lod.update(camera);
    const foliage = generated(model, mesh).filter(b => b.visible && !b.userData.shadowProxy && b.material !== library.materials.bark);
    const ids = foliage.flatMap(b => b.userData.instance_object_ids);
    expect(ids).toHaveLength(900); expect(new Set(ids).size).toBe(900);
    expect(foliage.filter(b => b.userData.level === 0).reduce((n, b) => n + b.count, 0)).toBe(64);
    expect(foliage.filter(b => b.userData.level === 1).reduce((n, b) => n + b.count, 0)).toBe(700);
    expect(foliage.filter(b => b.userData.level === 2).reduce((n, b) => n + b.count, 0)).toBe(136);
    lod.remove(model);
  });

  it('culls cells, packs one draw per library part and LOD, and releases generated resources', () => {
    const { model, mesh } = tile();
    const scene = new THREE.Scene(); scene.add(model);
    const camera = new THREE.PerspectiveCamera(65, 1, 0.1, 1000);
    const lod = new VegetationLod('medium', library);
    lod.add(model); lod.add(model); lod.update(camera);
    const batches = generated(model, mesh);
    expect(mesh.visible).toBe(false);
    const species = new Set(batches.map(b => b.userData.nature));
    // Per species: bark + leaves at LOD 0 and 1, LOD 1's bark + leaves as LOD 0's shadow proxy, the impostor at LOD 2.
    expect(batches).toHaveLength(species.size * 7);
    const source = (b: THREE.InstancedMesh) => library.asset(b.userData.nature).lods[b.userData.shadowProxy ? 1 : b.userData.level];
    expect(batches.every(b => source(b).parts.some(p => p.geometry === b.geometry && p.material === b.material))).toBe(true);
    const visible = batches.filter(m => m.visible);
    expect(visible.length).toBeGreaterThan(0);
    expect(drawn(batches)).toBeLessThan(mesh.count);
    // Only the shadow-layer proxies cast shadows; they never draw in colour passes.
    expect(batches.filter(b => b.castShadow).every(b => b.userData.shadowProxy && b.layers.mask === 2)).toBe(true);
    // Parts of one LOD share one matrix and one tint buffer.
    for (const b of batches) {
      const sibling = batches.find(o => o !== b && o.userData.nature === b.userData.nature && o.userData.level === b.userData.level && o.parent === b.parent);
      if (sibling) { expect(sibling.instanceMatrix).toBe(b.instanceMatrix); expect(sibling.instanceColor).toBe(b.instanceColor); }
    }
    const versions = batches.map(m => m.instanceMatrix.version);
    lod.update(camera);
    expect(batches.map(m => m.instanceMatrix.version)).toEqual(versions);
    camera.position.set(0, 0, 2000); lod.update(camera);
    expect(batches.every(m => !m.visible)).toBe(true);
    lod.remove(model);
    expect(model.children).toEqual([mesh]); expect(mesh.visible).toBe(true);
  });

  it('uses smaller near-instance budgets at low quality', () => {
    const { model, mesh } = tile(600, 'tree_detailed_fall', () => new THREE.Vector3(0, 0, -20));
    const scene = new THREE.Scene(); scene.add(model);
    const camera = new THREE.PerspectiveCamera(65, 1, 0.1, 1000);
    const nearCount = (tier: 'low' | 'medium') => {
      const lod = new VegetationLod(tier, library); lod.add(model); lod.update(camera);
      const near = generated(model, mesh).filter(b => b.visible && b.userData.level === 0 && b.material === library.materials.leaves);
      const count = near.filter(b => !b.userData.shadowProxy).reduce((n, b) => n + b.count, 0);
      // Every near tree also casts its shadow through the proxy.
      expect(near.filter(b => b.userData.shadowProxy).reduce((n, b) => n + b.count, 0)).toBe(count);
      lod.remove(model);
      return count;
    };
    expect(nearCount('low')).toBe(128);
    expect(nearCount('medium')).toBe(300);
  });

  it('draws far trees as impostors without shadows, and drops shrubs beyond their last LOD', () => {
    const scene = new THREE.Scene(), trees = tile(2, 'tree_detailed_fall', i => new THREE.Vector3(0, 0, i ? -400 : -100));
    scene.add(trees.model);
    const lod = new VegetationLod('medium', library);
    lod.add(trees.model);
    lod.update(new THREE.PerspectiveCamera(65, 1, 0.1, 1000));
    const visible = generated(trees.model, trees.mesh).filter(b => b.visible);
    const far = visible.filter(b => b.material === library.materials.impostor);
    expect(far.reduce((n, b) => n + b.count, 0)).toBe(1);
    expect(far.every(b => !b.castShadow && !b.receiveShadow)).toBe(true);
    lod.remove(trees.model);
    const bushes = tile(1, 'tree_default_fall', () => new THREE.Vector3(0, 0, -400), 1.2);
    bushes.mesh.userData.instance_object_ids = ['bush:1'];
    scene.add(bushes.model); lod.add(bushes.model); lod.update(new THREE.PerspectiveCamera(65, 1, 0.1, 1000));
    expect(generated(bushes.model, bushes.mesh).some(b => b.visible)).toBe(false);
    lod.remove(bushes.model);
  });

  it('shares library geometry and materials across tiles and never disposes them on unload', () => {
    const lod = new VegetationLod('medium', library), scene = new THREE.Scene();
    const a = tile(4), b = tile(4);
    scene.add(a.model, b.model); lod.add(a.model); lod.add(b.model);
    const geometryOf = (model: THREE.Object3D, source: THREE.Object3D) => new Set(generated(model, source).map(m => m.geometry));
    expect([...geometryOf(a.model, a.mesh)].every(g => geometryOf(b.model, b.mesh).has(g))).toBe(true);
    const disposed = vi.fn();
    library.materials.leaves.addEventListener('dispose', disposed);
    lod.remove(a.model); lod.remove(b.model);
    expect(disposed).not.toHaveBeenCalled();
  });

  it('never expands vegetation instances into a static tile shadow mesh', () => {
    const { model, mesh } = tile();
    mesh.castShadow = false;
    expect(batchTileShadows(model)).toBeNull();
  });
});

it('moving past equal-distance cells preserves packed matrices and object identities', () => {
  const scene = new THREE.Scene();
  const { model, mesh } = tile(2, 'tree_oak_fall', i => new THREE.Vector3(i ? 10 : -10, 0, -100));
  mesh.userData.instance_object_ids = ['west', 'east'];
  scene.add(model);
  const lod = new VegetationLod('medium', library), camera = new THREE.PerspectiveCamera(65, 1, 0.1, 1000);
  lod.add(model); camera.position.x = -1; lod.update(camera);
  const batches = generated(model, mesh);
  const versions = batches.map(batch => batch.instanceMatrix.version);
  const ids = batches.filter(b => b.visible).flatMap(b => b.userData.instance_object_ids as string[]);
  expect(new Set(ids)).toEqual(new Set(['west', 'east']));
  camera.position.x = 1; lod.update(camera);
  expect(batches.map(batch => batch.instanceMatrix.version)).toEqual(versions);
  lod.remove(model);
});

it('spreads streamed instance preparation across frames while preserving all placements', async () => {
  let time = 0, frames = 0;
  const now = vi.spyOn(performance, 'now').mockImplementation(() => time += 3);
  vi.stubGlobal('requestAnimationFrame', (callback: FrameRequestCallback) => {
    frames++; return setTimeout(() => callback(time), 0);
  });
  try {
    const scene = new THREE.Scene();
    const { model, mesh } = tile(600, 'tree_detailed_fall', () => new THREE.Vector3(0, 0, -100));
    const lod = new VegetationLod('high', Promise.resolve(library));
    await lod.addAsync(model);
    expect(frames).toBeGreaterThan(3);
    scene.add(model); lod.update(new THREE.PerspectiveCamera(65, 1, .1, 1000));
    expect(drawn(generated(model, mesh))).toBe(600);
    lod.remove(model); expect(model.children).toEqual([mesh]);
  } finally { now.mockRestore(); vi.unstubAllGlobals(); }
});

it('screenshot distance admits distant foliage and Normal restores tier culling', () => {
  const scene = new THREE.Scene();
  const { model, mesh } = tile(1, 'tree_detailed_fall', () => new THREE.Vector3(0, 0, -2000));
  scene.add(model);
  const lod = new VegetationLod('medium', library); lod.add(model);
  const camera = new THREE.PerspectiveCamera(65, 1, .1, 6000);
  const count = () => drawn(generated(model, mesh));
  lod.update(camera); expect(count()).toBe(0);
  lod.screenshotDistance = 3000; lod.update(camera); expect(count()).toBe(1);
  lod.screenshotDistance = 0; lod.update(camera); expect(count()).toBe(0);
  lod.remove(model);
});

it('plants willows only by the tile water', () => {
  const scene = new THREE.Scene(), lod = new VegetationLod('medium', library);
  const wet = tile(100, 'tree_detailed_fall', i => new THREE.Vector3(5, 0, i * 3));
  const river = new THREE.Mesh(new THREE.PlaneGeometry(40, 400).rotateX(-Math.PI / 2).translate(-20, 0, 150), new THREE.MeshStandardMaterial());
  river.userData = { layer: 'ground', material_key: 'water' };
  wet.model.add(river); scene.add(wet.model); lod.add(wet.model);
  expect(generated(wet.model, wet.mesh).some(b => b.userData.nature === 'tree_willow')).toBe(true);
  lod.remove(wet.model);
  const dry = tile(100, 'tree_detailed_fall', i => new THREE.Vector3(200, 0, i * 3));
  dry.model.add(river.clone()); scene.add(dry.model); lod.add(dry.model);
  expect(generated(dry.model, dry.mesh).some(b => b.userData.nature === 'tree_willow')).toBe(false);
  lod.remove(dry.model);
});

it('draws tile barriers with the fence kit in 2 m modules and stacked granite courses, without shadows', () => {
  const scene = new THREE.Scene(), model = new THREE.Group(), lod = new VegetationLod('medium', library);
  const barrier = (key: string, scale: THREE.Vector3, z: number) => {
    const mesh = new THREE.InstancedMesh(new THREE.BoxGeometry(), new THREE.MeshStandardMaterial(), 1);
    mesh.userData = { layer: 'structures', material_key: key };
    mesh.setMatrixAt(0, new THREE.Matrix4().compose(new THREE.Vector3(0, 0, z), new THREE.Quaternion(), scale));
    model.add(mesh);
    return mesh;
  };
  const wood = barrier('draft_wood', new THREE.Vector3(6, 1, 0.12), -10);
  const stone = barrier('draft_stone', new THREE.Vector3(4, 1.8, 0.3), -14);
  const brick = barrier('draft_brick', new THREE.Vector3(4, 1.6, 0.08), -18);
  const chain = barrier('draft_chain', new THREE.Vector3(4, 1.6, 0.08), -22), panel = barrier('draft_chain_panel', new THREE.Vector3(4, 1.6, 0.08), -22);
  scene.add(model); lod.add(model);
  lod.update(new THREE.PerspectiveCamera(65, 1, 0.1, 1000));
  expect(wood.visible).toBe(false); expect(stone.visible).toBe(false); expect(brick.visible).toBe(true);
  expect(chain.visible).toBe(false); expect(panel.visible).toBe(false);
  const sources: THREE.Object3D[] = [stone, brick, chain, panel];
  const kit = generated(model, wood).filter(b => !sources.includes(b));
  const drawn = (name: string) => kit.filter(b => b.visible && b.userData.nature === name).reduce((n, b) => n + b.count, 0);
  expect(drawn('picket_fence')).toBe(3);
  expect(drawn('granite_wall')).toBe(6);
  expect(drawn('chain_fence')).toBe(2);
  expect(kit.some(b => b.castShadow)).toBe(false);
  lod.remove(model);
  expect(wood.visible).toBe(true);
});

it('grows instanced grass on tile grass surfaces near the camera only', () => {
  const scene = new THREE.Scene(), model = new THREE.Group();
  const lawn = new THREE.Mesh(new THREE.PlaneGeometry(30, 30).rotateX(-Math.PI / 2).translate(0, 0, -20), new THREE.MeshStandardMaterial());
  lawn.userData = { layer: 'ground', material_key: 'draft_grass' };
  model.add(lawn); scene.add(model);
  const lod = new VegetationLod('medium', library, scene), camera = new THREE.PerspectiveCamera(65, 1, 0.1, 1000);
  lod.add(model); lod.update(camera);
  const grass = scene.getObjectByName('nature-grass')!;
  const count = () => grass.children.reduce((n, m) => n + ((m as THREE.InstancedMesh).visible ? (m as THREE.InstancedMesh).count : 0), 0);
  // About 2.5 clumps per m² at medium quality.
  expect(count()).toBeGreaterThan(1900);
  expect(count()).toBeLessThan(2600);
  camera.position.set(500, 0, 0); lod.update(camera);
  expect(count()).toBe(0);
  camera.position.set(0, 0, 0); lod.remove(model); lod.update(camera);
  expect(count()).toBe(0);
});

it('keeps far barriers visible on their last mesh without taking tree budgets', () => {
  const { model, mesh } = tile(900, 'tree_detailed_fall', () => new THREE.Vector3(0, 0, -20));
  mesh.userData.instance_species = Array(900).fill('Tilia cordata');
  const fence = new THREE.InstancedMesh(new THREE.BoxGeometry(), new THREE.MeshStandardMaterial(), 1500);
  fence.userData = { layer: 'structures', material_key: 'draft_wood' };
  for (let i = 0; i < fence.count; i++) fence.setMatrixAt(i, new THREE.Matrix4().compose(
    new THREE.Vector3((i % 10) - 5, 0, -230), new THREE.Quaternion(), new THREE.Vector3(2, 1, .12)));
  model.add(fence); const scene = new THREE.Scene(); scene.add(model);
  const lod = new VegetationLod('medium', library); lod.add(model); lod.update(new THREE.PerspectiveCamera(65, 1, .1, 1000));
  const visible = generated(model, mesh).filter(b => b.visible && !b.userData.shadowProxy);
  expect(visible.filter(b => b.userData.nature === 'picket_fence').reduce((n, b) => n + b.count, 0)).toBe(1500);
  expect(visible.filter(b => b.userData.nature === 'tree_linden' && b.material === library.materials.leaves).reduce((n, b) => n + b.count, 0)).toBe(900);
  lod.dispose();
});

it('does not attach grass or trees after disposal while the library is pending', async () => {
  let resolve!: (value: typeof library) => void;
  const promise = new Promise<typeof library>(r => { resolve = r; });
  const scene = new THREE.Scene(), { model, mesh } = tile(); scene.add(model);
  const lod = new VegetationLod('medium', promise, scene), pending = lod.addAsync(model);
  expect(lod.loadedModels()).toEqual([model]);
  lod.dispose(); resolve(library); await pending;
  expect(scene.getObjectByName('nature-grass')).toBeUndefined();
  expect(generated(model, mesh)).toHaveLength(0); expect(mesh.visible).toBe(true);
});

it('cancels yielded preparation when its tile is removed', async () => {
  const scene = new THREE.Scene(), { model, mesh } = tile(); scene.add(model);
  const lod = new VegetationLod('medium', library, scene);
  let resume!: () => void;
  (lod as unknown as { budget: { checkpoint(): Promise<void> } }).budget.checkpoint = () => new Promise<void>(r => { resume = r; });
  const pending = lod.addAsync(model); await Promise.resolve();
  expect(resume).toBeDefined();
  lod.remove(model); resume(); await pending;
  expect(generated(model, mesh)).toHaveLength(0); expect(mesh.visible).toBe(true);
  expect(lod.loadedModels()).toHaveLength(0); lod.dispose();
});


it('does not upload stationary trees or grass again through the actual WebGPU attribute manager', () => {
  const scene = new THREE.Scene(), { model, mesh } = tile(2, 'tree_oak_fall', i => new THREE.Vector3(i ? 0 : 500, 0, -30));
  scene.add(model); mesh.userData.instance_species = ['Tilia cordata', 'Tilia cordata'];
  const lod = new VegetationLod('medium', library), camera = new THREE.PerspectiveCamera(65, 1, .1, 1000);
  lod.add(model); lod.update(camera);
  const lawn = new THREE.Mesh(new THREE.PlaneGeometry(20, 20).rotateX(-Math.PI / 2), new THREE.MeshStandardMaterial());
  lawn.userData = { layer: 'ground', material_key: 'draft_grass' }; model.add(lawn);
  const grass = new GrassField(library, 'medium');
  const steps = grass.addSteps(model); while (!steps.next().done) { /* fixture */ }
  grass.update(camera);
  const updateAttribute = vi.fn();
  const manager = new Attributes({ createAttribute() {}, updateAttribute } as never, { createAttribute() {} } as never);
  const upload = () => {
    const batches = [...generated(model, mesh), ...grass.root.children as THREE.InstancedMesh[]];
    const attributes = new Set(batches.filter(m => m.visible).flatMap(m => [m.instanceMatrix, m.instanceColor!]));
    for (const attribute of attributes) manager.update(attribute, AttributeType.VERTEX);
  };
  upload(); lod.update(camera); grass.update(camera); upload(); upload();
  expect(updateAttribute).not.toHaveBeenCalled();
  camera.position.x = 500; lod.update(camera); upload();
  expect(updateAttribute).toHaveBeenCalled();
  for (const batch of generated(model, mesh).filter(m => m.visible)) {
    expect(batch.instanceMatrix.updateRanges).toEqual([{ start: 0, count: batch.count * 16 }]);
  }
  lod.dispose(); grass.dispose();
});

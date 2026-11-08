import { describe, expect, it, vi } from 'vitest';
import { readFileSync } from 'node:fs';
import * as THREE from 'three/webgpu';
import { VegetationLod, fadeReach, leanTree, shapeTree, vegetationLevels } from '../../src/world/vegetationLod';
import { VEGETATION_FADE, scaleVegetationFade } from '../../src/world/nature/materials';
import { GrassField } from '../../src/world/nature/grass';
import { natureAsset, natureScale, natureTint } from '../../src/world/nature/species';
import { batchTileShadows } from '../../src/world/shadowBatch';
import { natureFixture } from './natureFixture';
import { QUALITY } from '../../src/render/quality';
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
/** The generated draws under `model`; the leaves' depth-prepass twins only with `twins`. */
const generated = (model: THREE.Object3D, source: THREE.Object3D, twins = false) => {
  const batches: THREE.InstancedMesh[] = [];
  model.traverse(o => {
    if ((o as THREE.InstancedMesh).isInstancedMesh && o !== source && (twins || !o.userData.depthPrepass)) batches.push(o as THREE.InstancedMesh);
  });
  return batches;
};
/** The library material a draw uses, through its LOD cross-fade variant (nature/materials.ts lodVariant). */
const base = (b: THREE.InstancedMesh) => library.materials.baseOf(b.material as THREE.Material);
const drawn = (batches: THREE.InstancedMesh[]) => batches.filter(b => b.visible && !b.userData.shadowProxy && base(b) !== library.materials.bark).reduce((n, b) => n + b.count, 0);
/** Several selections with the camera nudged past the still-camera gate each time (budgets act per update). */
const settle = (lod: VegetationLod, camera: THREE.PerspectiveCamera, updates = 40) => {
  for (let i = 0; i < updates; i++) { camera.position.x += i % 2 ? -0.1 : 0.1; camera.updateMatrixWorld(); lod.update(camera); }
};

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
  it('a full near budget moves the near boundary in until the trees draw their reduced mesh: never impostors, never dropped', () => {
    const { model, mesh } = tile(900, 'tree_detailed_fall', () => new THREE.Vector3(0, 0, -10));
    mesh.userData.instance_species = Array(900).fill('Tilia cordata');
    const scene = new THREE.Scene(); scene.add(model);
    const camera = new THREE.PerspectiveCamera(65, 1, .1, 1000);
    const lod = new VegetationLod('mobile', library); lod.add(model);
    const foliage = () => generated(model, mesh).filter(b => b.visible && !b.userData.shadowProxy && base(b) !== library.materials.bark);
    const count = (level: number) => foliage().filter(b => b.userData.level === level).reduce((n, b) => n + b.count, 0);
    for (let i = 0; i < 20; i++) {
      settle(lod, camera, 1);
      // Every update draws every tree, at one level or (inside the fade band) two, and never as an impostor.
      const ids = foliage().flatMap(b => b.userData.instance_object_ids);
      expect(new Set(ids).size).toBe(900); expect(ids.length).toBeLessThanOrEqual(1800);
      expect(count(2)).toBe(0);
    }
    // 900 trees 10 m away against 64 full meshes: the boundary settles inside them, so they all draw reduced.
    expect([count(0), count(1)]).toEqual([0, 900]);
    lod.remove(model);
  });

  it('selects LOD from each tree distance even inside one cell', () => {
    const {model,mesh}=tile(2,'tree_detailed_fall',i=>new THREE.Vector3(0,0,-(30+i*35)));
    mesh.userData.instance_species=['Tilia cordata','Tilia cordata'];
    const scene=new THREE.Scene();scene.add(model);
    const lod=new VegetationLod('medium',library);lod.add(model);lod.update(new THREE.PerspectiveCamera(65,1,.1,1000));
    const foliage=generated(model,mesh).filter(b=>b.visible&&!b.userData.shadowProxy&&base(b)!==library.materials.bark);
    expect(foliage.filter(b=>b.userData.level===0).flatMap(b=>b.userData.instance_object_ids)).toEqual(['osm:0']);
    expect(foliage.filter(b=>b.userData.level===1).flatMap(b=>b.userData.instance_object_ids)).toEqual(['osm:1']);
    lod.dispose();
  });

  it('includes camera height and keeps screenshot range out of near-detail selection', () => {
    const {model,mesh}=tile(1,'tree_detailed_fall',()=>new THREE.Vector3(0,0,-20));
    const scene=new THREE.Scene();scene.add(model);
    const lod=new VegetationLod('medium',library);lod.screenshotDistance=4000;lod.add(model);
    const camera=new THREE.PerspectiveCamera(65,1,.1,5000);
    for(const [height,level]of [[120,1],[400,2]]) {
      camera.position.set(0,height,0);camera.lookAt(0,0,-20);lod.update(camera);
      const foliage=generated(model,mesh).filter(b=>b.visible&&!b.userData.shadowProxy&&base(b)!==library.materials.bark);
      expect(foliage).toHaveLength(1);expect(foliage[0].userData.level).toBe(level);
    }
    lod.dispose();
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
    expect(batches.every(b => source(b).parts.some(p => p.geometry === b.geometry && p.material === base(b)))).toBe(true);
    // Drawn levels use their cross-fade variant; shadow proxies cast with the plain library material.
    expect(batches.every(b => b.userData.shadowProxy ? b.material === base(b) : /@L(\d)(\+clear)?$/.exec((b.material as THREE.Material).name)?.[1] === String(b.userData.level))).toBe(true);
    const visible = batches.filter(m => m.visible);
    expect(visible.length).toBeGreaterThan(0);
    expect(drawn(batches)).toBeLessThan(mesh.count);
    // Only the shadow-layer proxies cast shadows; they never draw in colour passes.
    expect(batches.filter(b => b.castShadow).every(b => b.userData.shadowProxy && b.layers.mask === 2)).toBe(true);
    // Parts of one LOD share one matrix and one tint buffer.
    for (const b of batches) {
      const sibling = batches.find(o => o !== b && o.userData.nature === b.userData.nature && o.userData.level === b.userData.level
        && o.userData.shadowProxy === b.userData.shadowProxy && o.parent === b.parent);
      if (sibling) { expect(sibling.instanceMatrix).toBe(b.instanceMatrix); expect(sibling.instanceColor).toBe(b.instanceColor); }
    }
    // The leaf cards of the drawn mesh levels (not impostors, not shadow proxies) lay their depth first: a colour-less
    // twin on the same instance buffers, drawn before them, with the same wind, cut and fade mask; the lit leaves then
    // test against it without writing depth, so each visible leaf pixel is shaded about once.
    const twins = generated(model, mesh, true).filter(b => b.userData.depthPrepass);
    expect(twins).toHaveLength(species.size * 2);
    for (const twin of twins) {
      const lit = batches.find(b => b.instanceMatrix === twin.instanceMatrix && b.geometry === twin.geometry)!;
      expect(base(lit)).toBe(library.materials.leaves); expect(lit.userData.level).toBe(twin.userData.level);
      const depth = twin.material as THREE.MeshBasicNodeMaterial, leaves = lit.material as THREE.MeshStandardNodeMaterial;
      expect([depth.colorWrite, depth.depthWrite, depth.polygonOffset, depth.alphaTest, depth.side, depth.positionNode, depth.maskNode])
        .toEqual([false, true, true, leaves.alphaTest, leaves.side, leaves.positionNode, leaves.maskNode]);
      expect(leaves.depthWrite).toBe(false);
      expect(twin.renderOrder).toBeLessThan(lit.renderOrder); expect(twin.castShadow || twin.receiveShadow).toBe(false);
      expect([twin.count, twin.visible]).toEqual([lit.count, lit.visible]);
    }
    const versions = batches.map(m => m.instanceMatrix.version);
    lod.update(camera);
    expect(batches.map(m => m.instanceMatrix.version)).toEqual(versions);
    camera.position.set(0, 0, 2000); lod.update(camera);
    expect(batches.every(m => !m.visible)).toBe(true);
    lod.remove(model);
    expect(model.children).toEqual([mesh]); expect(mesh.visible).toBe(true);
  });

  it('uses smaller near-instance budgets at low quality: the near boundary settles where the budget fits', () => {
    // 600 trees along the view, 10 per metre from 5 to 65 m.
    const { model, mesh } = tile(600, 'tree_detailed_fall', i => new THREE.Vector3(0, 0, -(5 + i * 0.1)));
    const scene = new THREE.Scene(); scene.add(model);
    const camera = new THREE.PerspectiveCamera(65, 1, 0.1, 1000);
    const nearCount = (tier: 'low' | 'medium') => {
      const lod = new VegetationLod(tier, library); lod.add(model); settle(lod, camera);
      const near = generated(model, mesh).filter(b => b.visible && b.userData.level === 0 && base(b) === library.materials.leaves);
      const count = near.filter(b => !b.userData.shadowProxy).reduce((n, b) => n + b.count, 0);
      // Every near tree also casts its shadow through the proxy; casters keep the full near band while the budget
      // pulls the drawn boundary in (a shadow must not follow it).
      const casters = near.filter(b => b.userData.shadowProxy).reduce((n, b) => n + b.count, 0);
      expect(casters).toBeGreaterThanOrEqual(count);
      expect(casters).toBe(Math.floor((QUALITY[tier].vegetation.near - 5) * 10) + 1);
      lod.remove(model);
      return count;
    };
    const low = nearCount('low'), medium = nearCount('medium');
    expect(low).toBeGreaterThan(128 * 0.85 - 15); expect(low).toBeLessThanOrEqual(128 + 15);
    expect(medium).toBeGreaterThan(300 * 0.85 - 15); expect(medium).toBeLessThanOrEqual(300 + 15);
  });

  it('draws far trees as impostors without shadows, and drops shrubs beyond their last LOD', () => {
    const scene = new THREE.Scene(), trees = tile(2, 'tree_detailed_fall', i => new THREE.Vector3(0, 0, i ? -400 : -100));
    scene.add(trees.model);
    const lod = new VegetationLod('medium', library);
    lod.add(trees.model);
    lod.update(new THREE.PerspectiveCamera(65, 1, 0.1, 1000));
    const visible = generated(trees.model, trees.mesh).filter(b => b.visible);
    const far = visible.filter(b => base(b) === library.materials.impostor);
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

  it('cross-fades each boundary over a band: a tree inside it draws both levels, outside it one', () => {
    const bands = [50, 180, 650] as const;
    // Full mesh, both, reduced, both, impostor, nothing; shrubs (two levels) end at mid, grass (one) at near. Each band
    // lies inside its boundary: full meshes never draw past 50 m, reduced past 180 m, impostors past 650 m.
    expect([30, 40, 50, 51, 130, 150, 180, 181, 600, 651].map(d => vegetationLevels(d, bands, 3))).toEqual([1, 3, 3, 2, 2, 6, 6, 4, 4, 0]);
    expect([40, 150, 181].map(d => vegetationLevels(d, bands, 2))).toEqual([3, 2, 0]);
    expect([40, 51].map(d => vegetationLevels(d, bands, 1))).toEqual([1, 0]);
    // The selection window covers every distance where the shader's fade (half width plus per-tree stagger) can
    // still keep a pixel of either level: the CPU mirror of nature/materials.ts lodVariant's mask.
    const smooth = (a: number, b: number, x: number) => { const t = Math.min(1, Math.max(0, (x - a) / (b - a))); return t * t * (3 - 2 * t); };
    const fade = (j: number, d: number, seed: number) => {
      const end = bands[j] * (1 - VEGETATION_FADE.stagger[j] * seed);
      return smooth(end - 2 * bands[j] * VEGETATION_FADE.width[j], end, d);
    };
    const coverage = (k: number, d: number, seed: number) => (k > 0 ? fade(k - 1, d, seed) : 1) - fade(k, d, seed);
    for (let d = 0; d < 760; d += 0.5) for (const seed of [0, 0.25, 0.5, 0.75, 0.999]) {
      const mask = vegetationLevels(d, bands, 3);
      let total = 0;
      for (let k = 0; k < 3; k++) {
        const c = coverage(k, d, seed); total += c;
        if (c > 1e-6) expect(mask & (1 << k)).toBeTruthy();
      }
      // Levels split each pixel: their coverages add up to the tree's (1 until the far fade-out).
      if (d < bands[2] - fadeReach(bands[2], 2)) expect(total).toBeCloseTo(1, 6);
    }
    // A tree at the near boundary is in both draws.
    const { model, mesh } = tile(1, 'tree_detailed_fall', () => new THREE.Vector3(0, 0, -50));
    mesh.userData.instance_species = ['Tilia cordata'];
    const scene = new THREE.Scene(); scene.add(model);
    const lod = new VegetationLod('medium', library); lod.add(model); lod.update(new THREE.PerspectiveCamera(65, 1, .1, 1000));
    const levels = generated(model, mesh).filter(b => b.visible && !b.userData.shadowProxy && base(b) === library.materials.leaves).map(b => b.userData.level);
    expect(levels.sort()).toEqual([0, 1]);
    lod.dispose();
  });

  it('LOD variants are cached per material and level, fade only colour and depth, and go with the library', () => {
    const m = library.materials;
    const a = m.lodVariant(m.leaves, 0), b = m.lodVariant(m.leaves, 1), c = m.lodVariant(m.impostor, 2);
    expect(m.lodVariant(m.leaves, 0)).toBe(a); expect(a).not.toBe(b);
    expect([a.name, b.name, c.name]).toEqual(['nature-leaves@L0', 'nature-leaves@L1', 'nature-impostor@L2']);
    expect([m.baseOf(a), m.baseOf(c), m.baseOf(m.bark)]).toEqual([m.leaves, m.impostor, m.bark]);
    for (const v of [a, b, c]) { expect(v.maskNode).toBeTruthy(); expect(v.maskShadowNode).toBeTruthy(); expect(v.maskShadowNode).not.toBe(v.maskNode); }
    // Same surface as the base: alpha test, side, textures and wind.
    expect([b.alphaTest, b.side, b.colorNode, b.positionNode]).toEqual([m.leaves.alphaTest, m.leaves.side, m.leaves.colorNode, m.leaves.positionNode]);
  });

  it('clears the view through tree crowns only: trunks, fences, hedges, shrubs and impostors never fade', () => {
    const m = library.materials;
    const kinds = new Set<string>();
    for (const asset of library.assets.values()) {
      asset.lods.forEach((lod, level) => {
        const impostor = asset.kind === 'tree' && level === asset.lods.length - 1;
        for (const part of lod.parts) {
          kinds.add(asset.kind);
          expect(m.clearsView(asset.kind, part.material, impostor), `${asset.name} L${level} ${part.material.name}`)
            .toBe(asset.kind === 'tree' && !impostor && part.material === m.leaves);
        }
      });
    }
    expect([...kinds]).toEqual(expect.arrayContaining(['tree', 'fence', 'hedge']));
    // Drawn tree batches: leaf cards take the clearing variant, bark keeps the plain cross-fade.
    const { model, mesh } = tile(4, 'tree_detailed_fall', i => new THREE.Vector3(i * 3, 0, -20));
    const lod = new VegetationLod('medium', library); lod.add(model); lod.update(new THREE.PerspectiveCamera(65, 1, .1, 1000));
    const drawn = generated(model, mesh).filter(b => !b.userData.shadowProxy);
    const clearing = (b: THREE.InstancedMesh) => (b.material as THREE.Material).name.endsWith('+clear');
    expect(drawn.some(b => base(b) === m.leaves && clearing(b))).toBe(true);
    expect(drawn.filter(b => base(b) !== m.leaves).every(b => !clearing(b))).toBe(true);
    expect(m.lodVariant(m.leaves, 0, true)).not.toBe(m.lodVariant(m.leaves, 0));
    lod.dispose();
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
  expect(visible.filter(b => b.userData.nature === 'tree_linden' && base(b) === library.materials.leaves).reduce((n, b) => n + b.count, 0)).toBe(900);
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

describe('city-wide vegetation pools', () => {
  it('draws each asset LOD once for all tiles, in world space, and repacks only on selection changes', () => {
    const scene = new THREE.Scene();
    const a = tile(40, 'tree_detailed_fall', i => new THREE.Vector3(i * 6 - 120, 0, -60));
    const b = tile(40, 'tree_detailed_fall', i => new THREE.Vector3(i * 6 - 120, 0, -60));
    b.model.position.set(30, 0, -20); a.model.updateMatrixWorld(); b.model.updateMatrixWorld();
    scene.add(a.model, b.model);
    const camera = new THREE.PerspectiveCamera(65, 1, .1, 2000); camera.position.set(0, 2, 40); camera.updateMatrixWorld();
    const lod = new VegetationLod('medium', library, scene); lod.add(a.model); lod.add(b.model); lod.update(camera);
    const parts = [...generated(a.model, a.mesh, true), ...generated(b.model, b.mesh, true)];
    const pools = scene.getObjectByName('vegetation-pools')!.children as THREE.InstancedMesh[];
    expect(parts.every(p => p.userData.pooledVegetation === true)).toBe(true);
    // Tile draws and pools opt into shared instanced programs as still instances (render/instancePrograms.ts).
    for (const mesh of [...parts, ...pools]) { expect(mesh.userData.sharedInstanceProgram).toBe(true); expect(mesh.userData.staticInstances).toBe(true); }
    const selected = parts.filter(p => p.visible), pooled = pools.filter(p => p.visible);
    expect(pooled.length).toBeGreaterThan(0);
    // Two tiles of the same species share each pool (one tile alone where only it reaches a fade band).
    expect(selected.length).toBeGreaterThan(pooled.length); expect(selected.length).toBeLessThanOrEqual(pooled.length * 2);
    // Every pool holds exactly the selected instances of its asset LOD part across both tiles.
    for (const pool of pooled) {
      const sources = selected.filter(p => p.userData.nature === pool.userData.nature && p.userData.level === pool.userData.level
        && p.material === pool.material && p.geometry === pool.geometry && !!p.userData.shadowProxy === !!pool.userData.shadowProxy);
      expect(pool.count).toBe(sources.reduce((n, p) => n + p.count, 0));
      const world = new THREE.Matrix4(), expected = new THREE.Matrix4(), got = new THREE.Matrix4();
      let offset = 0;
      for (const source of sources) {
        source.updateWorldMatrix(true, false);
        for (let i = 0; i < source.count; i++) {
          source.getMatrixAt(i, expected); expected.premultiply(world.copy(source.matrixWorld));
          pool.getMatrixAt(offset + i, got);
          expect(got.toArray().map(v => +v.toFixed(3))).toEqual(expected.toArray().map(v => +v.toFixed(3)));
        }
        offset += source.count;
      }
      expect(pool.castShadow).toBe(sources[0].castShadow); expect(pool.layers.mask).toBe(sources[0].layers.mask);
    }
    const versions = pools.map(p => p.instanceMatrix.version);
    lod.update(camera);
    expect(pools.map(p => p.instanceMatrix.version)).toEqual(versions); // a stationary view writes nothing
    const before = pooled.reduce((n, p) => n + p.count, 0);
    lod.remove(b.model); lod.update(camera);
    expect(pools.filter(p => p.visible).reduce((n, p) => n + p.count, 0)).toBeLessThan(before);
    lod.dispose();
    expect(scene.getObjectByName('vegetation-pools')).toBeUndefined();
  });
});

it('a still camera over unchanged tiles does not select again; moving, turning or hiding a tile does', () => {
  const { model } = tile(), scene = new THREE.Scene(); scene.add(model);
  const lod = new VegetationLod('medium', library), camera = new THREE.PerspectiveCamera(65, 1, 0.1, 1000);
  lod.add(model);
  const frustum = vi.spyOn((lod as unknown as { frustum: THREE.Frustum }).frustum, 'setFromProjectionMatrix');
  lod.update(camera);
  const batches = generated(model, model.children[0]), count = drawn(batches);
  expect(count).toBeGreaterThan(0);
  expect(frustum).toHaveBeenCalledTimes(1);
  lod.update(camera); camera.position.x += 0.01; camera.updateMatrixWorld(); lod.update(camera);
  expect(frustum).toHaveBeenCalledTimes(1);
  expect(drawn(batches)).toBe(count);
  camera.position.x += 1; lod.update(camera); expect(frustum).toHaveBeenCalledTimes(2);
  camera.rotation.y += 0.01; lod.update(camera); expect(frustum).toHaveBeenCalledTimes(3);
  model.visible = false; lod.update(camera); expect(frustum).toHaveBeenCalledTimes(4); expect(drawn(batches)).toBe(0);
  model.visible = true; lod.update(camera); expect(drawn(batches)).toBe(count);
  lod.remove(model); lod.update(camera); expect(frustum).toHaveBeenCalledTimes(6);
});

it('cross-fades cost a few per cent of vegetation triangles at the riverside flight pose, no extra full meshes', () => {
  const manifest = JSON.parse(readFileSync(new URL('../../public/assets/nature/nature.json', import.meta.url), 'utf8')) as
    { assets: Record<string, { lods: { triangles: number }[] }> };
  // Jonni's recording pose: 45 m above a dense riverside forest (one tree per 64 m^2 out to 700 m), heading 193 deg,
  // pitch -13.6 deg. Draws and triangles of the colour passes, with the bands and with instant swaps (treefade=0).
  const at = (i: number) => new THREE.Vector3((i % 176) * 8 - 700, 0, Math.floor(i / 176) * 8 - 700);
  const camera = new THREE.PerspectiveCamera(60, 16 / 10, .1, 2000);
  camera.position.set(0, 45, 0); camera.rotation.order = 'YXZ';
  camera.rotation.set(-13.6 * Math.PI / 180, (180 - 193) * Math.PI / 180, 0); camera.updateMatrixWorld();
  const measure = () => {
    const { model, mesh } = tile(176 * 176, 'tree_detailed_fall', at);
    const scene = new THREE.Scene(); scene.add(model);
    const lod = new VegetationLod('medium', library); lod.add(model); settle(lod, camera, 12);
    const visible = generated(model, mesh).filter(b => b.visible && !b.userData.shadowProxy);
    // Real per-LOD triangles of the shipped library (the fixture's geometry is a stand-in), split over the parts.
    const real = (b: THREE.InstancedMesh) => manifest.assets[b.userData.nature]?.lods[b.userData.level]?.triangles ?? 0;
    const triangles = (b: THREE.InstancedMesh) => base(b) === library.materials.bark ? 0 : b.count * real(b);
    const byLevel = [0, 1, 2].map(level => visible.filter(b => b.userData.level === level && base(b) !== library.materials.bark).reduce((n, b) => n + b.count, 0));
    const result = { draws: visible.length, triangles: visible.reduce((n, b) => n + triangles(b), 0), byLevel };
    lod.dispose();
    return result;
  };
  const saved = { width: [...VEGETATION_FADE.width], stagger: [...VEGETATION_FADE.stagger] };
  for (const [height, pitch] of [[45, -13.6], [2, 0]]) {
    camera.position.y = height; camera.rotation.x = pitch * Math.PI / 180; camera.updateMatrixWorld();
    const faded = measure();
    let instant: ReturnType<typeof measure>;
    try { scaleVegetationFade(0); instant = measure(); } finally { Object.assign(VEGETATION_FADE, { width: [...saved.width], stagger: [...saved.stagger] }); }
    process.stderr.write(JSON.stringify({ height, faded, instant }) + '\n');
    expect(faded.byLevel[0]).toBeLessThanOrEqual(instant.byLevel[0]); // full meshes never reach farther
    expect(faded.triangles).toBeLessThan(instant.triangles * 1.06);
    expect(faded.draws).toBeLessThanOrEqual(instant.draws * 1.1);
  }
});

describe('object editor previews', () => {
  it('lists trees by id at their original matrix, moves, hides and restores one through a delta, also after a reload', () => {
    const { model } = tile(3, 'tree_detailed_fall', i => new THREE.Vector3(i * 10, 0, -30));
    const scene = new THREE.Scene(); scene.add(model);
    const camera = new THREE.PerspectiveCamera(65, 1, .1, 1000);
    const lod = new VegetationLod('medium', library); lod.add(model); lod.update(camera);
    const trees = () => { const out = new Map<string, THREE.Vector3>(); lod.forEachTree(t => out.set(t.id, new THREE.Vector3().setFromMatrixPosition(t.matrix))); return out; };
    expect([...trees().keys()].sort()).toEqual(['osm:0', 'osm:1', 'osm:2']); // grouped by species asset
    const drawnAt = () => {
      const out: THREE.Vector3[] = [];
      model.traverse(o => {
        const m = o as THREE.InstancedMesh;
        if (!m.isInstancedMesh || !m.visible || !m.userData.vegetationLod) return;
        for (let i = 0; i < m.count; i++) {
          const w = new THREE.Matrix4(); m.getMatrixAt(i, w); w.premultiply(m.matrixWorld);
          if (w.elements[0] !== 0 || w.elements[5] !== 0) out.push(new THREE.Vector3().setFromMatrixPosition(w));
        }
      });
      return out;
    };
    lod.setEdit('osm:1', new THREE.Matrix4().makeTranslation(3, 0, 0));
    lod.update(camera);
    expect(drawnAt().some(p => Math.abs(p.x - 13) < 1e-3)).toBe(true);
    expect(drawnAt().some(p => Math.abs(p.x - 10) < 1e-3)).toBe(false);
    expect(trees().get('osm:1')!.x).toBeCloseTo(10); // picking sees the original; the editor applies its preview
    lod.setEdit('osm:2', null); lod.update(camera);
    expect(drawnAt().some(p => Math.abs(p.x - 20) < 1e-3)).toBe(false);
    lod.setEdit('osm:2', undefined); lod.update(camera);
    expect(drawnAt().some(p => Math.abs(p.x - 20) < 1e-3)).toBe(true);
    // The tile reloads: the open preview applies to the new buckets.
    lod.remove(model); lod.add(model); lod.update(camera);
    expect(drawnAt().some(p => Math.abs(p.x - 13) < 1e-3)).toBe(true);
    lod.setEdit('osm:1', undefined); lod.update(camera);
    expect(drawnAt().some(p => Math.abs(p.x - 10) < 1e-3)).toBe(true);
  });
});

it('pools never keep a dissolve twin or a coverage: pooled draws sit out tile dissolves, growth takes the stable material', async () => {
  const { LodFades, dissolveTwin, isDissolveTwin, precompileDissolve } = await import('../../src/render/lodFade');
  const scene = new THREE.Scene();
  const a = tile(10, 'tree_detailed_fall', i => new THREE.Vector3(i * 6 - 30, 0, -100));
  scene.add(a.model);
  const camera = new THREE.PerspectiveCamera(65, 1, .1, 2000); camera.updateMatrixWorld();
  const lod = new VegetationLod('medium', library, scene); lod.add(a.model); lod.update(camera);
  const pooled = generated(a.model, a.mesh);
  expect(pooled.every(d => d.userData.pooledVegetation)).toBe(true);
  // A full-tile dissolve of the tile leaves its pooled (hidden) draws alone: the pools draw those trees.
  await precompileDissolve([a.model], [0], () => {});
  const fades = new LodFades();
  fades.start(a.model, 0, 1, undefined, 0, 0.6);
  expect(pooled.some(d => isDissolveTwin(d.material as THREE.Material))).toBe(false);
  fades.cancel(a.model);
  // Even if a draw held its twin mid-fade when its pool grew, the pool takes the stable material and no coverage.
  const draw = pooled.find(d => d.visible)!;
  const real = draw.material as THREE.Material;
  draw.material = dissolveTwin(real); draw.userData.lodCoverage = 0.4;
  const b = tile(400, 'tree_detailed_fall', i => new THREE.Vector3((i % 20) * 6 - 60, 0, -100 - Math.floor(i / 20) * 6));
  scene.add(b.model); lod.add(b.model); camera.position.x += 1; camera.updateMatrixWorld(); lod.update(camera);
  const pools = scene.getObjectByName('vegetation-pools')!.children as THREE.InstancedMesh[];
  expect(pools.length).toBeGreaterThan(0);
  expect(pools.some(p => isDissolveTwin(p.material as THREE.Material))).toBe(false);
  expect(pools.every(p => p.userData.lodCoverage === undefined)).toBe(true);
  draw.material = real; delete draw.userData.lodCoverage;
  lod.dispose();
});

it('a dense forest hands its reduced meshes over to impostors earlier, within the mid budget', () => {
  const budget = QUALITY.medium.vegetation.midInstances, q = QUALITY.medium.vegetation;
  // 3x the budget between 55 and 175 m straight ahead, inside the view.
  const count = budget * 3, at = (i: number) => {
    const d = 55 + ((i * 7919) % count) / count * 120, side = (((i * 104729) % 1000) / 1000 - 0.5) * 0.5 * d;
    return new THREE.Vector3(side, 0, -d);
  };
  const forest = tile(count, 'tree_detailed_fall', at), scene = new THREE.Scene(); scene.add(forest.model);
  const lod = new VegetationLod('medium', library), camera = new THREE.PerspectiveCamera(65, 16 / 9, 0.1, 2000);
  lod.add(forest.model);
  settle(lod, camera, 300);
  const mid = library.materials.lodBands.value.y as number;
  expect(mid).toBeLessThan(q.mid); expect(mid).toBeGreaterThanOrEqual(q.mid * 0.5);
  expect(lod.drawn.mid).toBeLessThanOrEqual(budget * 1.1);
  lod.dispose();
  // A light one keeps the full band.
  const park = tile(40, 'tree_detailed_fall', i => new THREE.Vector3(i * 4 - 80, 0, -100)), open = new THREE.Scene(); open.add(park.model);
  const light = new VegetationLod('medium', library);
  light.add(park.model); settle(light, camera, 40);
  expect(library.materials.lodBands.value.y).toBe(q.mid);
  light.dispose();
});

it('a still camera settles the tree bands to a step (no dither), and they widen again when it moves', () => {
  let time = 1000;
  const now = vi.spyOn(performance, 'now').mockImplementation(() => time);
  try {
    const { model } = tile(), scene = new THREE.Scene(); scene.add(model);
    const lod = new VegetationLod('medium', library), camera = new THREE.PerspectiveCamera(65, 1, .1, 1000);
    lod.add(model);
    const sharp = () => library.materials.lodSharpness.value as number;
    const frames = (n: number, move: boolean) => { for (let i = 0; i < n; i++) {
      time += 16; if (move) { camera.position.x += 0.5; camera.updateMatrixWorld(); } lod.update(camera);
    } };
    frames(30, true); expect(sharp()).toBe(0);
    frames(10, false); expect(sharp()).toBe(0); // the first 0.25 s of standing still change nothing
    frames(40, false); expect(sharp()).toBe(1); // then 0.5 s to a step
    frames(4, true); expect(sharp()).toBeGreaterThan(0); frames(16, true); expect(sharp()).toBe(0); // 0.25 s back
    lod.dispose();
  } finally { now.mockRestore(); }
});

it('draws trees-lidar crowns: the lidar height and radius instead of the instance scale, leaning on a fixed base', () => {
  const { model, mesh } = tile(2, 'tree_detailed_fall', i => new THREE.Vector3(i * 20, 2, -30));
  mesh.userData.instance_object_ids = ['puut:a', 'area:b'];
  mesh.userData.instance_species = ['Tilia cordata', 'Tilia cordata'];
  mesh.userData.instance_crowns = [null, [21, 6.5, 0.05, -0.02]];
  const scene = new THREE.Scene(); scene.add(model);
  const lod = new VegetationLod('medium', library);
  lod.add(model);
  const trees = new Map<string, { matrix: THREE.Matrix4; asset: string }>();
  lod.update(new THREE.PerspectiveCamera(65, 1, 0.1, 1000));
  lod.forEachTree(tree => trees.set(tree.id, { matrix: tree.matrix.clone(), asset: tree.asset }));
  const asset = library.asset(trees.get('area:b')!.asset);
  const expected = natureScale(21, 6.5, asset.height, asset.radius);
  const leaning = trees.get('area:b')!.matrix.elements, upright = trees.get('puut:a')!.matrix.elements;
  // The base stays on the trunk; the y axis carries the lidar height and the lean; x/z keep the lidar radius.
  expect([leaning[12], leaning[13], leaning[14]]).toEqual([20, 2, -30]);
  expect(leaning[5]).toBeCloseTo(expected.y, 6);
  expect(leaning[4] / leaning[5]).toBeCloseTo(0.05, 6); expect(leaning[6] / leaning[5]).toBeCloseTo(-0.02, 6);
  expect(Math.hypot(leaning[0], leaning[2])).toBeCloseTo(expected.xz, 3);
  // Without a crown the instance scale is drawn, upright.
  const own = library.asset(trees.get('puut:a')!.asset);
  expect(upright[5]).toBeCloseTo(natureScale(15, 4, own.height, own.radius).y, 6);
  expect([upright[4], upright[6]]).toEqual([0, 0]);
  // leanTree shears every basis vector by its height component.
  const m = leanTree(new THREE.Matrix4().makeRotationZ(0.3), 0.1, 0.2).elements;
  expect(m[0]).toBeCloseTo(Math.cos(0.3) + 0.1 * Math.sin(0.3), 6); expect(m[2]).toBeCloseTo(0.2 * Math.sin(0.3), 6);
  lod.remove(model);
});

it('stretches a tree footprint to its lidar ellipse, preserving the area, before the lean', () => {
  const unit = (angle: number) => shapeTree(new THREE.Matrix4(), [10, 4, 0, 0, 1.25, angle]).elements;
  const along = unit(0);
  expect(along[0]).toBeCloseTo(1.25, 6); expect(along[10]).toBeCloseTo(0.8, 6); expect(along[5]).toBe(1);
  const turned = unit(Math.PI / 2);
  expect(turned[0]).toBeCloseTo(0.8, 6); expect(turned[10]).toBeCloseTo(1.25, 6);
  const diagonal = new THREE.Matrix4().fromArray(unit(Math.PI / 4));
  expect(diagonal.determinant()).toBeCloseTo(1, 6);
  expect(new THREE.Vector3(1, 0, 1).normalize().applyMatrix4(diagonal).length()).toBeCloseTo(1.25, 6);
  // The lean is applied after the ellipse: the crown offset per metre of height is the measured shear.
  const both = shapeTree(new THREE.Matrix4(), [10, 4, 0.05, 0, 1.25, 0]).elements;
  expect(both[4]).toBeCloseTo(0.05, 6);
});

describe('tree shadow casters', () => {
  const casting = (model: THREE.Object3D, mesh: THREE.Object3D) => generated(model, mesh)
    .filter(b => b.visible && b.userData.shadowProxy && base(b) === library.materials.leaves).reduce((n, b) => n + b.count, 0);
  const cascade = (center: THREE.Vector3, half: number) => {
    const camera = new THREE.OrthographicCamera(-half, half, half, -half, 0.5, 600);
    camera.position.copy(center).add(new THREE.Vector3(0, 300, 0)); camera.lookAt(center); camera.updateMatrixWorld();
    return new THREE.Frustum().setFromProjectionMatrix(new THREE.Matrix4().multiplyMatrices(camera.projectionMatrix, camera.matrixWorldInverse));
  };

  it('culls casters by the light frustum, not the view: trees behind the camera keep their shadows', () => {
    // 36 trees 10 m to the side and 5-40 m behind a camera that looks down -z: none is in view, all are in the near band.
    const { model, mesh } = tile(36, 'tree_detailed_fall', i => new THREE.Vector3(10, 0, 5 + i));
    const scene = new THREE.Scene(); scene.add(model);
    const camera = new THREE.PerspectiveCamera(40, 16 / 9, 0.1, 1000); camera.position.set(0, 1.7, -5); camera.updateMatrixWorld();
    const lod = new VegetationLod('medium', library);
    // A low sun beyond the trees (+z) throws their shadows forward, into the view.
    const toSun = new THREE.Vector3(0, Math.sin(THREE.MathUtils.degToRad(12)), Math.cos(THREE.MathUtils.degToRad(12)));
    // A cascade around the camera, wider than the view: the trees behind it are inside the light's volume.
    let frusta = [cascade(new THREE.Vector3(0, 0, 0), 60)];
    lod.casters = () => ({ toSun, frusta });
    lod.add(model); lod.update(camera);
    const drawn = generated(model, mesh).filter(b => b.visible && !b.userData.shadowProxy).reduce((n, b) => n + b.count, 0);
    expect(drawn).toBe(0);
    expect(casting(model, mesh)).toBe(36);
    // Turning towards them changes what is drawn, not what casts.
    camera.rotation.y = Math.PI; camera.updateMatrixWorld(); lod.update(camera);
    expect(casting(model, mesh)).toBe(36);
    // A light volume elsewhere (ahead of the camera only) drops them.
    frusta = [cascade(new THREE.Vector3(0, 0, -150), 50)]; camera.position.x += 0.1; camera.updateMatrixWorld(); lod.update(camera);
    expect(casting(model, mesh)).toBe(0);
    // At noon their shadows lie under them, out of view: nothing to draw into the map.
    frusta = [cascade(new THREE.Vector3(0, 0, 0), 60)]; toSun.set(0, 1, 0);
    camera.rotation.y = 0; camera.position.x -= 0.1; camera.updateMatrixWorld(); lod.update(camera);
    expect(casting(model, mesh)).toBe(0);
    lod.remove(model);
  });

  it('keeps a tree casting while its long low-sun shadow reaches the camera', () => {
    // Trees 55-65 m behind, beyond the 50 m near band; a low sun beyond them throws their shadows past the camera.
    const { model, mesh } = tile(5, 'tree_detailed_fall', i => new THREE.Vector3(0, 0, 55 + i * 2.5));
    const scene = new THREE.Scene(); scene.add(model);
    const camera = new THREE.PerspectiveCamera(65, 1, 0.1, 1000); camera.updateMatrixWorld();
    const lod = new VegetationLod('medium', library);
    const toSun = new THREE.Vector3(0, 1, 0);
    lod.casters = () => ({ toSun, frusta: [] });
    lod.add(model); lod.update(camera);
    expect(casting(model, mesh)).toBe(0); // noon: short shadows stay far behind
    // The sun low beyond the trees (+z): their shadows run about 4.7 times their height towards the camera.
    toSun.set(0, Math.sin(THREE.MathUtils.degToRad(12)), Math.cos(THREE.MathUtils.degToRad(12)));
    camera.position.x += 0.1; camera.updateMatrixWorld(); lod.update(camera);
    expect(casting(model, mesh)).toBe(5);
    lod.remove(model);
  });
});
